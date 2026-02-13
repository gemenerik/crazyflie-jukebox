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

#define DEBUG_MODULE "JUKEBOX"

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
} MusicEvent;

// Hardcoded test sequence: C major chord (C4-E4-G4-C5) held for 1s, then rest, then repeat
static const MusicEvent testSequence[] = {
  // Start C major chord - all motors start simultaneously
  {0,    0, NOTE_ON,  C4},   // Motor 1: C4
  {0,    1, NOTE_ON,  E4},   // Motor 2: E4
  {0,    2, NOTE_ON,  G4},   // Motor 3: G4
  {0,    3, NOTE_ON,  C5},   // Motor 4: C5

  // Hold for 1000ms, then stop all
  {1000, 0, NOTE_OFF, 0},
  {0,    1, NOTE_OFF, 0},
  {0,    2, NOTE_OFF, 0},
  {0,    3, NOTE_OFF, 0},

  // Rest for 500ms, then play F major chord (F4-A4-C5-F5)
  {500,  0, NOTE_ON,  F4},
  {0,    1, NOTE_ON,  A4},
  {0,    2, NOTE_ON,  C5},
  {0,    3, NOTE_ON,  F5},

  // Hold for 1000ms, then stop
  {1000, 0, NOTE_OFF, 0},
  {0,    1, NOTE_OFF, 0},
  {0,    2, NOTE_OFF, 0},
  {0,    3, NOTE_OFF, 0},

  // Rest for 500ms, then play G major chord (G4-H4-D5-G5)
  {500,  0, NOTE_ON,  G4},
  {0,    1, NOTE_ON,  H4},   // H4 is B4 in German notation
  {0,    2, NOTE_ON,  D5},
  {0,    3, NOTE_ON,  G5},

  // Hold for 1500ms, then stop
  {1500, 0, NOTE_OFF, 0},
  {0,    1, NOTE_OFF, 0},
  {0,    2, NOTE_OFF, 0},
  {0,    3, NOTE_OFF, 0},
};

#define SEQUENCE_LENGTH (sizeof(testSequence) / sizeof(MusicEvent))

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
    DEBUG_PRINT("Motor %u ON: %u Hz\n", motorIndex, frequency);
  } else {
    motorsBeep(motorId, false, 0, 0);
    DEBUG_PRINT("Motor %u OFF\n", motorIndex);
  }
}

// Play the music sequence
void playSequence(const MusicEvent* sequence, size_t length)
{
  DEBUG_PRINT("Playing sequence with %u events\n", length);

  for (size_t i = 0; i < length; i++) {
    const MusicEvent* event = &sequence[i];

    // Wait for delta time
    if (event->delta_ms > 0) {
      vTaskDelay(M2T(event->delta_ms));
    }

    // Process event
    if (event->event == NOTE_ON) {
      setMotorFrequency(event->motor, event->frequency);
    } else {
      setMotorFrequency(event->motor, 0);
    }
  }

  DEBUG_PRINT("Sequence finished\n");
}

void appMain()
{
  DEBUG_PRINT("Jukebox app started!\n");

  // Wait for system to be fully initialized (motors, sensors, etc.)
  systemWaitStart();
  DEBUG_PRINT("System ready!\n");

  DEBUG_PRINT("Playing polyphonic test sequence...\n");

  // Play the hardcoded sequence
  playSequence(testSequence, SEQUENCE_LENGTH);

  DEBUG_PRINT("Playback finished!\n");

  // Keep app running
  while(1) {
    vTaskDelay(M2T(1000));
  }
}
