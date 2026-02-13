#!/usr/bin/env python3
"""
Crazyflie Jukebox - Upload and play music sequences on Crazyflie motors

This script uploads music sequences to the Crazyflie jukebox app via app channel.
Each motor acts as a speaker by modulating PWM frequency for 4-voice polyphony.

Example usage:
    python main.py                              # Connect to default URI, upload test sequence
    python main.py radio://0/80/2M/E7E7E7E701   # Connect to custom URI
"""

import argparse
import asyncio
import struct
from enum import IntEnum
from dataclasses import dataclass
from typing import List

from cflib import Crazyflie, LinkContext
from midi_utils import (
    MIN_MOTOR_FREQUENCY_HZ,
    MAX_MOTOR_FREQUENCY_HZ,
    # Note frequencies
    C4, DES4, D4, ES4, E4, F4, GES4, G4, AS4, A4, B4, H4,
    C5, DES5, D5, ES5, E5, F5, GES5, G5, AS5, A5, B5, H5,
    C6, DES6, D6, ES6, E6, F6, GES6, G6, AS6, A6, B6, H6,
    C7, DES7, D7, ES7, E7, F7, GES7, G7, AS7, A7, H7, B7,
)


# Firmware buffer limit (must match MAX_MUSIC_EVENTS in jukebox.c)
MAX_MUSIC_EVENTS = 5000


# Match C firmware definitions
class EventType(IntEnum):
    NOTE_ON = 0
    NOTE_OFF = 1


class PacketType(IntEnum):
    PKT_START_UPLOAD = 0
    PKT_EVENT_DATA = 1
    PKT_END_UPLOAD = 2


@dataclass
class MusicEvent:
    """Represents a single music event (note on/off on a motor)"""
    delta_ms: int      # Time since last event (uint16_t)
    motor: int         # Motor ID 0-3 (uint8_t)
    event: EventType   # NOTE_ON or NOTE_OFF
    frequency: int     # Frequency in Hz (uint16_t)

    def pack(self) -> bytes:
        """Pack into binary format matching C struct"""
        # struct MusicEvent { uint16_t delta_ms; uint8_t motor; EventType event; uint16_t frequency; }
        # With __attribute__((packed)), this is 6 bytes: HH BB H = 2 + 1 + 1 + 2
        return struct.pack('<HBBH', self.delta_ms, self.motor, self.event, self.frequency)


# Hardcoded test sequence matching the firmware
TEST_SEQUENCE = [
    # C major chord - all motors simultaneously
    MusicEvent(0, 0, EventType.NOTE_ON, C4),
    MusicEvent(0, 1, EventType.NOTE_ON, E4),
    MusicEvent(0, 2, EventType.NOTE_ON, G4),
    MusicEvent(0, 3, EventType.NOTE_ON, C5),

    # Hold 1000ms, then stop all
    MusicEvent(1000, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(0, 2, EventType.NOTE_OFF, 0),
    MusicEvent(0, 3, EventType.NOTE_OFF, 0),

    # Rest 500ms, then F major chord
    MusicEvent(500, 0, EventType.NOTE_ON, F4),
    MusicEvent(0, 1, EventType.NOTE_ON, A4),
    MusicEvent(0, 2, EventType.NOTE_ON, C5),
    MusicEvent(0, 3, EventType.NOTE_ON, F5),

    # Hold 1000ms, then stop
    MusicEvent(1000, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(0, 2, EventType.NOTE_OFF, 0),
    MusicEvent(0, 3, EventType.NOTE_OFF, 0),

    # Rest 500ms, then G major chord
    MusicEvent(500, 0, EventType.NOTE_ON, G4),
    MusicEvent(0, 1, EventType.NOTE_ON, H4),
    MusicEvent(0, 2, EventType.NOTE_ON, D5),
    MusicEvent(0, 3, EventType.NOTE_ON, G5),

    # Hold 1500ms, then stop
    MusicEvent(1500, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(0, 2, EventType.NOTE_OFF, 0),
    MusicEvent(0, 3, EventType.NOTE_OFF, 0),

    # Twinkle Twinkle melody with harmony
    MusicEvent(500, 0, EventType.NOTE_ON, C4),   # Twin-
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, C4),    # kle
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, G4),    # twin-
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, G4),    # kle
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, A4),    # lit-
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, A4),    # tle
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, G4),    # star
    MusicEvent(600, 0, EventType.NOTE_OFF, 0),

    # Harmony section
    MusicEvent(100, 0, EventType.NOTE_ON, F4),   # How I
    MusicEvent(0, 1, EventType.NOTE_ON, A4),
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, F4),    # won-
    MusicEvent(0, 1, EventType.NOTE_ON, A4),
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, E4),    # der
    MusicEvent(0, 1, EventType.NOTE_ON, G4),
    MusicEvent(0, 2, EventType.NOTE_ON, C5),
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(0, 2, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, E4),    # what
    MusicEvent(0, 1, EventType.NOTE_ON, G4),
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, D4),    # you
    MusicEvent(0, 1, EventType.NOTE_ON, F4),
    MusicEvent(0, 2, EventType.NOTE_ON, A4),
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(0, 2, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, D4),    # are
    MusicEvent(0, 1, EventType.NOTE_ON, F4),
    MusicEvent(300, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(50, 0, EventType.NOTE_ON, C4),    # (final)
    MusicEvent(0, 1, EventType.NOTE_ON, E4),
    MusicEvent(0, 2, EventType.NOTE_ON, G4),
    MusicEvent(0, 3, EventType.NOTE_ON, C5),
    MusicEvent(800, 0, EventType.NOTE_OFF, 0),
    MusicEvent(0, 1, EventType.NOTE_OFF, 0),
    MusicEvent(0, 2, EventType.NOTE_OFF, 0),
    MusicEvent(0, 3, EventType.NOTE_OFF, 0),
]


async def upload_sequence(app_channel, sequence: List[MusicEvent]) -> None:
    """Upload a music sequence to the Crazyflie"""
    print(f"\nUploading {len(sequence)} events...")

    # Send START_UPLOAD packet
    start_packet = struct.pack('<BH', PacketType.PKT_START_UPLOAD, len(sequence))
    app_channel.send(start_packet)
    print(f"Sent START_UPLOAD (total_events={len(sequence)})")

    # Send each event
    for i, event in enumerate(sequence):
        # Pack: type (uint8_t) + MusicEvent
        event_packet = struct.pack('<B', PacketType.PKT_EVENT_DATA) + event.pack()
        app_channel.send(event_packet)

        if (i + 1) % 50 == 0:
            print(f"Sent {i + 1}/{len(sequence)} events...")

    # Send END_UPLOAD packet
    end_packet = struct.pack('<B', PacketType.PKT_END_UPLOAD)
    app_channel.send(end_packet)
    print(f"Sent END_UPLOAD")
    print("Upload complete! Music should be playing on the Crazyflie.")


async def stream_console(cf: Crazyflie) -> None:
    """Background task to stream console output"""
    console = cf.console()
    print("\n--- Crazyflie Console ---")

    try:
        while True:
            lines = await console.get_lines()
            for line in lines:
                print(f"[CF] {line}")
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass


def load_sequence_from_midi(midi_path: str, strategy_name: str, transformer_name: str) -> List[MusicEvent]:
    """
    Load and convert MIDI file to MusicEvent sequence.

    Args:
        midi_path: Path to MIDI file
        strategy_name: Voice allocation strategy name
        transformer_name: Frequency transformer name

    Returns:
        List of MusicEvent objects
    """
    from midi_converter import MidiConverter
    from voice_strategies import get_strategy
    from frequency_transformers import get_transformer

    print(f"\nLoading MIDI file: {midi_path}")
    converter = MidiConverter()
    converter.load_midi(midi_path)

    # Print MIDI info
    print(converter.get_info())

    print(f"\nConverting with:")
    strategy = get_strategy(strategy_name)
    print(f"  Voice allocation: {strategy.get_description()}")

    # Get transformer with motor frequency range limits
    transformer = get_transformer(transformer_name, min_hz=MIN_MOTOR_FREQUENCY_HZ, max_hz=MAX_MOTOR_FREQUENCY_HZ)

    print(f"  Frequency transform: {transformer.get_description()}")

    sequence = converter.convert(strategy, transformer)
    print(f"\nGenerated {len(sequence)} MusicEvent objects")

    # Check if sequence exceeds firmware buffer limit
    if len(sequence) > MAX_MUSIC_EVENTS:
        print(f"\n⚠️  WARNING: Sequence has {len(sequence)} events, but firmware buffer limit is {MAX_MUSIC_EVENTS}")
        print(f"    Truncating to first {MAX_MUSIC_EVENTS} events...")

        # Calculate approximate duration being truncated
        if sequence:
            total_duration_ms = sum(event.delta_ms for event in sequence)
            truncated_duration_ms = sum(event.delta_ms for event in sequence[:MAX_MUSIC_EVENTS])
            kept_percentage = (truncated_duration_ms / total_duration_ms * 100) if total_duration_ms > 0 else 0
            print(f"    Keeping approximately {kept_percentage:.1f}% of the song ({truncated_duration_ms/1000:.1f}s / {total_duration_ms/1000:.1f}s)")

        sequence = sequence[:MAX_MUSIC_EVENTS]
        print(f"    Final sequence: {len(sequence)} events\n")

    return sequence


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload music to Crazyflie jukebox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                          # Use built-in test sequence
  %(prog)s --midi song.mid                          # Convert and upload MIDI file
  %(prog)s --midi song.mid --strategy melodic       # Keep extreme pitches (default)
  %(prog)s --midi song.mid --strategy voice-stealing # Replace oldest notes (LRU)
  %(prog)s --midi song.mid --strategy rolled        # Arpeggiate >4 notes
  %(prog)s --midi song.mid --strategy round-robin   # Cycle through motors
  %(prog)s --midi song.mid --transpose none         # No octave clipping
  %(prog)s --list-strategies                        # Show all options
        """
    )
    parser.add_argument(
        "uri",
        nargs="?",
        default="radio://0/80/2M/E7E7E7E7E7",
        help="Crazyflie URI (default: radio://0/80/2M/E7E7E7E7E7)",
    )
    parser.add_argument(
        "--midi",
        type=str,
        help="Path to MIDI file to convert and upload",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default="melodic",
        help="Voice allocation strategy (default: melodic)",
    )
    parser.add_argument(
        "--transpose",
        type=str,
        default="octave-clip",
        help="Frequency transformation method (default: octave-clip)",
    )
    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="List available strategies and transformers, then exit",
    )
    args = parser.parse_args()

    # Handle --list-strategies
    if args.list_strategies:
        from voice_strategies import list_strategies
        from frequency_transformers import list_transformers
        list_strategies()
        list_transformers()
        return

    # Determine sequence source
    if args.midi:
        try:
            sequence = load_sequence_from_midi(args.midi, args.strategy, args.transpose)
        except Exception as e:
            print(f"\nError loading MIDI file: {e}")
            return
    else:
        sequence = TEST_SEQUENCE
        print(f"\nUsing built-in test sequence ({len(sequence)} events)")

    print(f"\nConnecting to {args.uri}...")
    context = LinkContext()
    cf = await Crazyflie.connect_from_uri(context, args.uri)
    print("Connected!")

    # Start console streaming in background
    console_task = asyncio.create_task(stream_console(cf))

    platform = cf.platform()
    app_channel = await platform.get_app_channel()

    if app_channel is None:
        print("Error: Could not acquire app channel (already in use?)")
        console_task.cancel()
        await cf.disconnect()
        return

    print("App channel acquired!")
    print("=" * 60)

    try:
        # Wait a moment for console to show startup messages
        await asyncio.sleep(0.5)

        # Upload and play the sequence
        await upload_sequence(app_channel, sequence)

        # Wait for playback to complete
        print("\nWaiting for playback to complete...")
        await asyncio.sleep(15.0)

        print("\n" + "=" * 60)
        print("Done!")

    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        console_task.cancel()
        print("\nDisconnecting...")
        await cf.disconnect()
        print("Disconnected!")


if __name__ == "__main__":
    asyncio.run(main())
