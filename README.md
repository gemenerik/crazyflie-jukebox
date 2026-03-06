# Crazyflie Jukebox

Play music on your Crazyflie drone by modulating the motor PWM frequencies to generate sound.

Tested on Crazyflie 2.1 (non +). Likely incompatible with Brushless. Propellers must be attached to load the motors properly for accurate pitch.

**Warning:** Use at your own risk. While designed for low thrust, certain note combinations can cause the drone to move, flip, or take off unexpectedly.

## Setup

1. Flash the jukebox app to your Crazyflie:
   ```bash
   cd app_jukebox
   make -j$(nproc)
   CLOAD_CMDS="-w radio://0/80/2M/E7E7E7E7E7" make cload
   ```

2. Install Python dependencies:
   ```bash
   pip install -r pyproject.toml
   ```

## Usage

Run with a MIDI file:
```bash
python main.py --midi path/to/your/song.mid
```

Run with default test sequence:
```bash
python main.py
```

Connect to a specific Crazyflie:
```bash
python main.py --uri radio://0/80/2M/E7E7E7E701 --midi song.mid
```

### Multi-drone swarm playback

Play a MIDI file across multiple drones, with each drone playing different tracks:

```bash
python main.py --uris radio://0/80/2M/E7E7E7E701 radio://0/80/2M/E7E7E7E702 --midi song.mid
```

The tool will prompt you to select which MIDI tracks to use and how to assign them across drones. Playback is synchronized: all drones receive periodic sync pulses from the host to stay aligned.

Press Ctrl+C to disconnect and terminate. Terminating the program will not stop music playback.
