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

// Helper function to play a note on a single motor
void playMotorNote(uint32_t motorId, uint16_t frequency, uint16_t duration_ms)
{
  DEBUG_PRINT("playMotorNote: motor=%lu freq=%u dur=%u\n", motorId, frequency, duration_ms);

  if (frequency > 0) {
    // Calculate the ratio for this frequency
    uint16_t ratio = (MOTORS_TIM_BEEP_CLK_FREQ / frequency) / 20;
    DEBUG_PRINT("Calculated ratio: %u\n", ratio);

    motorsBeep(motorId, true, frequency, ratio);
    DEBUG_PRINT("Motor beep enabled\n");
  }

  vTaskDelay(M2T(duration_ms));

  // Turn off the motor
  motorsBeep(motorId, false, 0, 0);
  DEBUG_PRINT("Motor beep disabled\n");
}

void appMain()
{
  DEBUG_PRINT("Jukebox app started!\n");

  // Wait for system to be fully initialized (motors, sensors, etc.)
  systemWaitStart();
  DEBUG_PRINT("System ready!\n");

  DEBUG_PRINT("Playing single note on motor 1...\n");

  // Play a single note (C4) on motor 1 for 1000ms
  playMotorNote(MOTOR_M1, C4, 1000);

  DEBUG_PRINT("Note finished!\n");

  // Keep app running
  while(1) {
    vTaskDelay(M2T(1000));
  }
}
