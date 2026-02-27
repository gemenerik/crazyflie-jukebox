/**
 * ,---------,       ____  _ __
 * |  ,-^-,  |      / __ )(_) /_______________ _____  ___
 * | (  O  ) |     / __  / / __/ ___/ ___/ __ `/_  / / _ \
 * | / ,--´  |    / /_/ / / /_/ /__/ /  / /_/ / / /_/  __/
 *    +------`   /_____/_/\__/\___/_/   \__,_/ /___/\___/
 *
 * Crazyflie control firmware
 *
 * Copyright (C) 2026 Bitcraze AB
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, in version 3.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 *
 * jukebox.c - Music player using motor beeps
 */

#include "app.h"
#include "FreeRTOS.h"
#include "task.h"

#include "debug.h"
#include "motors.h"
#include "system.h"
#include "app_channel.h"
#include "stabilizer.h"
#include "watchdog.h"

#define DEBUG_MODULE "JUKEBOX"

// Maximum number of music events we can store
#define MAX_MUSIC_EVENTS 5000

// Event types
typedef enum {
  NOTE_ON,
  NOTE_OFF
} EventType;

// Music event structure
typedef struct {
  uint16_t delta_ms;    // Time since last event
  uint8_t motor;        // Motor ID (0-3 for M1-M4)
  EventType event;      // NOTE_ON or NOTE_OFF
  uint16_t frequency;   // Frequency in Hz (0 for NOTE_OFF)
} __attribute__((packed)) MusicEvent;

// Appchannel packet types
typedef enum {
  PKT_START_UPLOAD,     // Start uploading a new sequence
  PKT_EVENT_DATA,       // Music event data
  PKT_END_UPLOAD,       // Finish upload (wait for PKT_START_PLAYBACK)
  PKT_START_PLAYBACK,   // Trigger playback of uploaded sequence
  PKT_UPLOAD_ACK,       // Sent by drone to confirm upload received
} PacketType;

// Packet structures for appchannel communication
typedef struct {
  uint8_t type;         // PacketType
  uint16_t total_events; // Total number of events that will be sent
} __attribute__((packed)) StartUploadPacket;

typedef struct {
  uint8_t type;         // PacketType (PKT_EVENT_DATA)
  MusicEvent event;     // The music event
} __attribute__((packed)) EventDataPacket;

typedef struct {
  uint8_t type;         // PacketType (PKT_END_UPLOAD)
} __attribute__((packed)) EndUploadPacket;

// Global buffer for uploaded music events
static MusicEvent musicSequence[MAX_MUSIC_EVENTS];
static size_t musicEventCount = 0;

// Helper to start/stop a motor note immediately (non-blocking)
void setMotorFrequency(uint8_t motorIndex, uint16_t frequency)
{
  // Map motor index (0-3) to motor IDs
  uint32_t motorIds[] = {MOTOR_M1, MOTOR_M2, MOTOR_M3, MOTOR_M4};
  uint32_t motorId = motorIds[motorIndex];

  if (frequency > 0) {
    // Calculate the ratio for this frequency
    uint16_t ratio = (MOTORS_TIM_BEEP_CLK_FREQ / frequency) / 20;
    motorsBeep(motorId, true, frequency, ratio);
  } else {
    motorsBeep(motorId, false, 0, 0);
  }
}

// Play the music sequence
void playSequence(const MusicEvent* sequence, size_t length)
{
  // Suspend both stabilizer and rate supervisor tasks to prevent interference
  if (stabilizerTaskHandle != NULL) {
    vTaskSuspend(stabilizerTaskHandle);
  }

  if (rateSupervisorTaskHandle != NULL) {
    vTaskSuspend(rateSupervisorTaskHandle);
  }

  for (size_t i = 0; i < length; i++) {
    const MusicEvent* event = &sequence[i];

    // Wait for delta time, feeding watchdog periodically
    if (event->delta_ms > 0) {
      uint32_t remainingMs = event->delta_ms;
      while (remainingMs > 0) {
        uint32_t delayMs = (remainingMs > 50) ? 50 : remainingMs;
        vTaskDelay(M2T(delayMs));
        remainingMs -= delayMs;
        watchdogReset();  // Feed watchdog every 50ms (timeout is 100-353ms)
      }
    }

    // Process event
    if (event->event == NOTE_ON) {
      setMotorFrequency(event->motor, event->frequency);
    } else {
      setMotorFrequency(event->motor, 0);
    }

    watchdogReset();
  }

  // Feed watchdog before resuming tasks
  watchdogReset();

  // Resume stabilizer first, then rate supervisor (reverse order of suspension)
  if (stabilizerTaskHandle != NULL) {
    vTaskResume(stabilizerTaskHandle);
  }

  // Small delay to let stabilizer task run before rate supervisor checks it
  vTaskDelay(M2T(10));
  watchdogReset();

  if (rateSupervisorTaskHandle != NULL) {
    vTaskResume(rateSupervisorTaskHandle);
  }
}

// Handle incoming appchannel packets to upload music sequence
void receiveAndPlayMusic()
{
  uint8_t buffer[APPCHANNEL_MTU];

  DEBUG_PRINT("Waiting for music upload via appchannel...\n");

  while (1) {
    // Wait for START_UPLOAD packet
    size_t len = appchannelReceiveDataPacket(buffer, sizeof(buffer), APPCHANNEL_WAIT_FOREVER);

    if (len == 0) {
      DEBUG_PRINT("ERROR: No data received\n");
      continue;
    }

    StartUploadPacket* startPkt = (StartUploadPacket*)buffer;
    if (startPkt->type != PKT_START_UPLOAD) {
      DEBUG_PRINT("WARNING: Expected START_UPLOAD, got type %d. Ignoring.\n", startPkt->type);
      continue;
    }

    DEBUG_PRINT("Starting upload: expecting %u events\n", startPkt->total_events);

    // Reset buffer
    musicEventCount = 0;

    // Receive events
    bool uploadComplete = false;
    while (!uploadComplete) {
      len = appchannelReceiveDataPacket(buffer, sizeof(buffer), APPCHANNEL_WAIT_FOREVER);

      if (len == 0) {
        DEBUG_PRINT("ERROR: No data received\n");
        break;
      }

      uint8_t pktType = buffer[0];

      if (pktType == PKT_EVENT_DATA) {
        if (musicEventCount < MAX_MUSIC_EVENTS) {
          EventDataPacket* eventPkt = (EventDataPacket*)buffer;
          musicSequence[musicEventCount] = eventPkt->event;
          musicEventCount++;

          if (musicEventCount % 50 == 0) {
            DEBUG_PRINT("Received %u events...\n", musicEventCount);
          }

          if (musicEventCount == MAX_MUSIC_EVENTS) {
            DEBUG_PRINT("WARNING: Event buffer full at %u events, discarding remaining\n", musicEventCount);
          }
        }
        // else: buffer full, discard event but keep reading until END_UPLOAD
      }
      else if (pktType == PKT_END_UPLOAD) {
        DEBUG_PRINT("Upload complete: %u events received\n", musicEventCount);
        // Send ACK back to host
        uint8_t ack = PKT_UPLOAD_ACK;
        appchannelSendDataPacket(&ack, sizeof(ack));
        uploadComplete = true;
      }
      else {
        DEBUG_PRINT("ERROR: Unknown packet type %d\n", pktType);
        break;
      }
    }

    // Wait for PKT_START_PLAYBACK
    if (musicEventCount > 0) {
      DEBUG_PRINT("Waiting for START_PLAYBACK command...\n");
      while (1) {
        len = appchannelReceiveDataPacket(buffer, sizeof(buffer), APPCHANNEL_WAIT_FOREVER);
        if (len > 0 && buffer[0] == PKT_START_PLAYBACK) {
          DEBUG_PRINT("Starting playback!\n");
          playSequence(musicSequence, musicEventCount);
          DEBUG_PRINT("Playback finished!\n");
          break;
        }
      }
    } else {
      DEBUG_PRINT("WARNING: No events to play\n");
    }

    // Ready for next upload
    DEBUG_PRINT("\nReady for next song upload.\n");
  }
}

void appMain()
{
  DEBUG_PRINT("Jukebox app started!\n");
  DEBUG_PRINT("sizeof(MusicEvent) = %u\n", sizeof(MusicEvent));
  DEBUG_PRINT("sizeof(EventDataPacket) = %u\n", sizeof(EventDataPacket));

  // Wait for system to be fully initialized (motors, sensors, etc.)
  systemWaitStart();
  DEBUG_PRINT("System ready!\n");

  // Main loop: receive and play music sequences from appchannel
  receiveAndPlayMusic();
}
