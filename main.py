#!/usr/bin/env python3
"""
Crazyflie Jukebox - Upload and play music sequences on Crazyflie motors

This script uploads music sequences to the Crazyflie jukebox app via app channel.
Each motor acts as a speaker by modulating PWM frequency for 4-voice polyphony.

Example usage:
    python main.py --help                             # Show all available arguments
    python main.py                                    # Connect to default URI, play test sequence
    python main.py --uri radio://0/80/2M/E7E7E7E701   # Connect to a specific URI
    python main.py --midi song.mid                    # Convert and upload a MIDI file
    python main.py --uris URI1 URI2 --midi song.mid   # Connect to multiple drones
"""

import asyncio
import struct
import sys
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional

import tyro

from cflib2 import Crazyflie, LinkContext
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
    PKT_START_PLAYBACK = 3
    PKT_UPLOAD_ACK = 4


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

    # Wait for drone to confirm it received all events
    while True:
        packets = await app_channel.receive()
        for pkt in packets:
            if len(pkt) > 0 and pkt[0] == PacketType.PKT_UPLOAD_ACK:
                print(f"Upload confirmed by drone.")
                return



async def start_playback(app_channel) -> None:
    """Send the start playback command to trigger the uploaded sequence."""
    packet = struct.pack('<B', PacketType.PKT_START_PLAYBACK)
    app_channel.send(packet)


async def stream_console(cf: Crazyflie, label: str = "CF") -> None:
    """Background task to stream console output"""
    console = cf.console()
    print(f"\n--- {label} Console ---")

    try:
        while True:
            lines = await console.get_lines()
            for line in lines:
                print(f"[{label}] {line}")
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass


def select_tracks(converter) -> List[int]:
    """
    Interactively ask user which tracks to include.

    Returns:
        List of selected track indices (all tracks with notes if user picks all).
    """
    tracks_with_notes = [t['index'] for t in converter.track_info if t['note_count'] > 0]

    if len(tracks_with_notes) <= 1:
        print(f"\nOnly one track with notes found, using it automatically.")
        return tracks_with_notes

    print(f"\nThis MIDI file has {len(tracks_with_notes)} tracks with notes.")
    print("You can use all tracks (they'll be mixed together),")
    print("or select specific tracks to reduce complexity.")

    while True:
        user_input = input("\nEnter track numbers (e.g., '1 3 5') or press Enter for all tracks: ").strip()

        if not user_input:
            print("Using all tracks")
            return tracks_with_notes

        try:
            selected = [int(x.strip()) for x in user_input.split()]
            invalid = [t for t in selected if t not in tracks_with_notes]
            if invalid:
                print(f"Invalid track numbers: {invalid}")
                print(f"   Valid tracks with notes: {tracks_with_notes}")
                continue
            print(f"Using tracks: {selected}")
            return selected
        except ValueError:
            print("Invalid input. Please enter track numbers separated by spaces (e.g., '1 3 5')")
            continue


def assign_tracks_to_drones(selected_tracks: List[int], uris: List[str], track_info: List[dict]) -> dict:
    """
    Interactively assign tracks to drones.

    For each drone (except the last), prompt user to pick tracks from the remaining pool.
    The last drone auto-gets all remaining tracks.

    Args:
        selected_tracks: List of track indices to distribute
        uris: List of drone URIs
        track_info: Track metadata for display

    Returns:
        Dict mapping URI to list of track indices
    """
    track_names = {}
    for t in track_info:
        name = t['name']
        if t['instrument']:
            name += f" ({t['instrument']})"
        track_names[t['index']] = name

    remaining = list(selected_tracks)
    assignment = {}

    for i, uri in enumerate(uris):
        is_last = (i == len(uris) - 1)

        if is_last:
            # Last drone gets all remaining tracks
            assignment[uri] = remaining
            print(f"\nDrone {i+1} ({uri}) auto-assigned remaining tracks: {remaining}")
            for t in remaining:
                print(f"  Track {t}: {track_names.get(t, '?')}")
            break

        print(f"\nDrone {i+1} ({uri}) — available tracks:")
        for t in remaining:
            print(f"  {t}: {track_names.get(t, '?')}")

        while True:
            user_input = input(f"Assign tracks to drone {i+1} (e.g., '1 3'): ").strip()
            if not user_input:
                print("Must assign at least one track.")
                continue
            try:
                picked = [int(x.strip()) for x in user_input.split()]
                invalid = [t for t in picked if t not in remaining]
                if invalid:
                    print(f"Invalid or already assigned: {invalid}")
                    continue
                assignment[uri] = picked
                for t in picked:
                    remaining.remove(t)
                print(f"Drone {i+1} assigned tracks: {picked}")
                break
            except ValueError:
                print("Invalid input.")
                continue

        if not remaining:
            # All tracks assigned, remaining drones get nothing
            for j in range(i + 1, len(uris)):
                assignment[uris[j]] = []
                print(f"\nDrone {j+1} ({uris[j]}) — no remaining tracks to assign.")
            break

    return assignment


def convert_tracks_to_sequence(
    converter, tracks: List[int], strategy_name: str, transformer_name: str
) -> List[MusicEvent]:
    """
    Convert specific tracks from a loaded MIDI file to a MusicEvent sequence.

    Args:
        converter: MidiConverter with loaded MIDI file
        tracks: Track indices to include
        strategy_name: Voice allocation strategy name
        transformer_name: Frequency transformer name

    Returns:
        List of MusicEvent objects (truncated to MAX_MUSIC_EVENTS if needed)
    """
    from voice_strategies import get_strategy
    from frequency_transformers import get_transformer

    strategy = get_strategy(strategy_name)
    transformer = get_transformer(transformer_name, min_hz=MIN_MOTOR_FREQUENCY_HZ, max_hz=MAX_MOTOR_FREQUENCY_HZ)

    sequence = converter.convert(strategy, transformer, tracks if tracks else None)
    print(f"  Generated {len(sequence)} events")

    if len(sequence) > MAX_MUSIC_EVENTS:
        print(f"  WARNING: Truncating {len(sequence)} events to {MAX_MUSIC_EVENTS}")
        total_duration_ms = sum(event.delta_ms for event in sequence)
        truncated_duration_ms = sum(event.delta_ms for event in sequence[:MAX_MUSIC_EVENTS])
        kept_percentage = (truncated_duration_ms / total_duration_ms * 100) if total_duration_ms > 0 else 0
        print(f"  Keeping ~{kept_percentage:.1f}% ({truncated_duration_ms/1000:.1f}s / {total_duration_ms/1000:.1f}s)")
        sequence = sequence[:MAX_MUSIC_EVENTS]

    return sequence


@dataclass
class Args:
    """Upload music to Crazyflie jukebox."""

    uri: Optional[str] = None
    """Crazyflie URI (default: radio://0/80/2M/E7E7E7E7E7). Mutually exclusive with --uris."""

    uris: Optional[List[str]] = None
    """Multiple Crazyflie URIs. Mutually exclusive with --uri."""

    midi: Optional[str] = None
    """Path to MIDI file to convert and upload."""

    strategy: str = "melodic"
    """Voice allocation strategy."""

    transpose: str = "octave-clip"
    """Frequency transformation method."""

    list_strategies: bool = False
    """List available strategies and transformers, then exit."""


async def main_async() -> None:
    args = tyro.cli(Args)

    # Handle --list-strategies
    if args.list_strategies:
        from voice_strategies import list_strategies
        from frequency_transformers import list_transformers
        list_strategies()
        list_transformers()
        return

    # Validate mutual exclusivity of --uri / --uris
    if args.uri is not None and args.uris is not None:
        print("Error: --uri and --uris are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    # Build list of URIs
    if args.uris is not None:
        uris = args.uris
    else:
        uris = [args.uri if args.uri is not None else "radio://0/80/2M/E7E7E7E7E7"]

    multi_drone = len(uris) > 1

    # Determine sequences per drone
    if args.midi:
        from midi_converter import MidiConverter

        try:
            print(f"\nLoading MIDI file: {args.midi}")
            converter = MidiConverter()
            converter.load_midi(args.midi)
            print(converter.get_info())
            print(converter.get_track_info())

            selected_tracks = select_tracks(converter)

            if multi_drone:
                # Assign tracks to drones interactively
                track_assignment = assign_tracks_to_drones(
                    selected_tracks, uris, converter.track_info
                )
                # Convert per drone
                drone_sequences = {}
                print(f"\nConverting with strategy={args.strategy}, transpose={args.transpose}:")
                for uri, tracks in track_assignment.items():
                    if not tracks:
                        print(f"\n  {uri}: no tracks, skipping")
                        continue
                    print(f"\n  {uri} (tracks {tracks}):")
                    drone_sequences[uri] = convert_tracks_to_sequence(
                        converter, tracks, args.strategy, args.transpose
                    )
            else:
                # Single drone gets all selected tracks
                print(f"\nConverting with strategy={args.strategy}, transpose={args.transpose}:")
                drone_sequences = {
                    uris[0]: convert_tracks_to_sequence(
                        converter, selected_tracks, args.strategy, args.transpose
                    )
                }
        except Exception as e:
            print(f"\nError loading MIDI file: {e}")
            return
    else:
        if multi_drone:
            print("Error: --midi is required when using multiple drones.", file=sys.stderr)
            sys.exit(1)
        drone_sequences = {uris[0]: TEST_SEQUENCE}
        print(f"\nUsing built-in test sequence ({len(TEST_SEQUENCE)} events)")

    # Filter out drones with no sequence
    active_uris = [uri for uri in uris if drone_sequences.get(uri)]

    if not active_uris:
        print("Error: No drones have any events to play.")
        return

    # Connect to all drones
    print(f"\nConnecting to {len(active_uris)} Crazyflie(s)...")
    context = LinkContext()
    cfs = await asyncio.gather(
        *[Crazyflie.connect_from_uri(context, uri) for uri in active_uris]
    )
    print("All connected!")

    # Start console streaming for all drones
    console_tasks = [
        asyncio.create_task(stream_console(cf, uri))
        for cf, uri in zip(cfs, active_uris)
    ]

    # Get app channels
    app_channels = {}
    for uri, cf in zip(active_uris, cfs):
        platform = cf.platform()
        app_channel = await platform.get_app_channel()
        if app_channel is None:
            print(f"Error: Could not acquire app channel for {uri}")
            for task in console_tasks:
                task.cancel()
            await asyncio.gather(*[cf.disconnect() for cf in cfs])
            return
        app_channels[uri] = app_channel

    print(f"{len(app_channels)} app channel(s) acquired!")
    print("=" * 60)

    try:
        await asyncio.sleep(0.5)

        # Upload to all drones in parallel
        print("\nUploading sequences...")
        await asyncio.gather(*[
            upload_sequence(app_channels[uri], drone_sequences[uri])
            for uri in active_uris
        ])

        # Trigger playback on all drones as fast as possible
        print("\nStarting playback...")
        for uri in active_uris:
            await start_playback(app_channels[uri])
        print("Playback triggered on all drones!")

        # Stay connected until user interrupts
        print("\n" + "=" * 60)
        print("Connected! Press Ctrl+C to disconnect.")
        print("=" * 60)

        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    finally:
        for task in console_tasks:
            task.cancel()
        print("Disconnecting...")
        await asyncio.gather(*[cf.disconnect() for cf in cfs])
        print("Disconnected!")


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass  # Suppress traceback on Ctrl+C


if __name__ == "__main__":
    main()
