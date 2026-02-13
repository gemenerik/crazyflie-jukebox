"""
MIDI file converter for Crazyflie Jukebox.

Converts MIDI files to MusicEvent sequences suitable for upload to Crazyflie.
Handles tempo changes, multi-track files, and voice allocation for 4-motor polyphony.
"""

import os
from typing import List, Tuple, Dict
from dataclasses import dataclass
import mido

# Import existing types from main
import sys
sys.path.insert(0, os.path.dirname(__file__))
from main import MusicEvent, EventType

from voice_strategies import VoiceAllocationStrategy, MelodicPriorityStrategy
from frequency_transformers import FrequencyTransformer, OctaveClippingTransformer


class MidiConversionError(Exception):
    """Raised when MIDI conversion fails."""
    pass


@dataclass
class MidiNote:
    """Represents a MIDI note event with absolute timing."""
    timestamp_ms: int    # Absolute time in milliseconds
    note: int            # MIDI note number (0-127)
    is_note_on: bool     # True for note_on, False for note_off


class MidiConverter:
    """
    Converts MIDI files to Crazyflie MusicEvent sequences.

    This class handles:
    - Parsing MIDI files with mido library
    - Converting MIDI ticks to milliseconds (respecting tempo changes)
    - Merging multiple tracks into a single timeline
    - Applying voice allocation strategy for 4-motor limit
    - Applying frequency transformation (octave clipping, etc.)
    - Generating MusicEvent list with delta timing
    """

    def __init__(self):
        """Initialize converter with empty state."""
        self.midi_file: Optional[mido.MidiFile] = None
        self.tempo_map: List[Tuple[int, int]] = []  # [(tick, microseconds_per_beat), ...]
        self.ticks_per_beat: int = 0
        self.timeline: List[MidiNote] = []

    def load_midi(self, filename: str) -> None:
        """
        Load and parse a MIDI file.

        Args:
            filename: Path to MIDI file (.mid or .midi)

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is not a valid MIDI file
            MidiConversionError: If parsing fails
        """
        # Validate file exists
        if not os.path.exists(filename):
            raise FileNotFoundError(f"MIDI file not found: {filename}")

        # Validate file extension
        if not (filename.lower().endswith('.mid') or filename.lower().endswith('.midi')):
            raise ValueError(f"Not a MIDI file (must end in .mid or .midi): {filename}")

        # Parse MIDI file
        try:
            self.midi_file = mido.MidiFile(filename)
        except Exception as e:
            raise MidiConversionError(f"Failed to parse MIDI file: {e}")

        # Validate file has content
        if len(self.midi_file.tracks) == 0:
            raise MidiConversionError("MIDI file has no tracks")

        # Extract timing information
        self.ticks_per_beat = self.midi_file.ticks_per_beat
        self.tempo_map = self._build_tempo_map()

        # Build timeline of all note events
        self.timeline = self._build_timeline()

        if len(self.timeline) == 0:
            raise MidiConversionError("MIDI file has no note events")

    def _build_tempo_map(self) -> List[Tuple[int, int]]:
        """
        Extract tempo changes from MIDI file.

        MIDI files can have tempo changes via 'set_tempo' meta events.
        We need to track these to convert ticks to milliseconds accurately.

        Returns:
            List of (absolute_tick, microseconds_per_beat) tuples, sorted by tick
            Default: [(0, 500000)] which is 120 BPM
        """
        tempo_map = [(0, 500000)]  # Default: 120 BPM = 500000 microseconds per beat

        for track in self.midi_file.tracks:
            current_tick = 0
            for msg in track:
                current_tick += msg.time
                if msg.type == 'set_tempo':
                    tempo_map.append((current_tick, msg.tempo))

        # Sort by tick and remove duplicates (keep last tempo at each tick)
        tempo_map.sort(key=lambda x: x[0])

        # Remove duplicate ticks, keeping the last one
        unique_tempo_map = []
        for tick, tempo in tempo_map:
            if unique_tempo_map and unique_tempo_map[-1][0] == tick:
                unique_tempo_map[-1] = (tick, tempo)
            else:
                unique_tempo_map.append((tick, tempo))

        return unique_tempo_map

    def _ticks_to_ms(self, ticks: int) -> int:
        """
        Convert MIDI ticks to milliseconds using tempo map.

        MIDI timing is based on "ticks" which are fractions of a beat.
        The tempo map tells us how long each beat lasts.

        Args:
            ticks: Absolute tick count from start of file

        Returns:
            Absolute time in milliseconds
        """
        current_tick = 0
        current_time_ms = 0.0
        tempo_idx = 0

        while current_tick < ticks and tempo_idx < len(self.tempo_map):
            # Get current tempo
            tempo_us_per_beat = self.tempo_map[tempo_idx][1]

            # Find next tempo change (or infinity)
            if tempo_idx + 1 < len(self.tempo_map):
                next_tempo_tick = self.tempo_map[tempo_idx + 1][0]
            else:
                next_tempo_tick = float('inf')

            # Calculate time in this tempo region
            ticks_in_region = min(ticks - current_tick, next_tempo_tick - current_tick)

            # Convert: ticks → beats → microseconds → milliseconds
            beats = ticks_in_region / self.ticks_per_beat
            microseconds = beats * tempo_us_per_beat
            milliseconds = microseconds / 1000.0

            current_time_ms += milliseconds
            current_tick += ticks_in_region

            # Move to next tempo region if needed
            if current_tick >= next_tempo_tick:
                tempo_idx += 1

        return int(round(current_time_ms))

    def _build_timeline(self) -> List[MidiNote]:
        """
        Build absolute timeline of all note events from all tracks.

        Merges all tracks into a single sorted timeline, converting
        relative tick times to absolute milliseconds.

        Returns:
            List of MidiNote objects sorted by timestamp
        """
        all_events: List[MidiNote] = []

        for track_idx, track in enumerate(self.midi_file.tracks):
            current_tick = 0

            for msg in track:
                current_tick += msg.time

                # Handle note_on messages
                if msg.type == 'note_on':
                    timestamp_ms = self._ticks_to_ms(current_tick)
                    # MIDI spec: note_on with velocity=0 is equivalent to note_off
                    is_on = msg.velocity > 0
                    all_events.append(MidiNote(timestamp_ms, msg.note, is_on))

                # Handle note_off messages
                elif msg.type == 'note_off':
                    timestamp_ms = self._ticks_to_ms(current_tick)
                    all_events.append(MidiNote(timestamp_ms, msg.note, False))

        # Sort by timestamp (stable sort preserves order of simultaneous events)
        all_events.sort(key=lambda e: e.timestamp_ms)

        return all_events

    def convert(
        self,
        strategy: VoiceAllocationStrategy = None,
        freq_transformer: FrequencyTransformer = None
    ) -> List[MusicEvent]:
        """
        Convert MIDI timeline to MusicEvent sequence.

        Args:
            strategy: Voice allocation strategy (default: MelodicPriorityStrategy)
            freq_transformer: Frequency transformer (default: OctaveClippingTransformer)

        Returns:
            List of MusicEvent objects with delta timing

        Raises:
            MidiConversionError: If no MIDI file loaded
        """
        if self.midi_file is None:
            raise MidiConversionError("No MIDI file loaded. Call load_midi() first.")

        # Use defaults if not specified
        if strategy is None:
            strategy = MelodicPriorityStrategy()
        if freq_transformer is None:
            freq_transformer = OctaveClippingTransformer()

        # Reset strategy state
        strategy.reset()

        # Group events by timestamp
        events_by_time: Dict[int, Dict[str, List[int]]] = {}
        for event in self.timeline:
            if event.timestamp_ms not in events_by_time:
                events_by_time[event.timestamp_ms] = {'on': [], 'off': []}

            if event.is_note_on:
                events_by_time[event.timestamp_ms]['on'].append(event.note)
            else:
                events_by_time[event.timestamp_ms]['off'].append(event.note)

        # Process each timestamp and generate MusicEvents
        output_events: List[MusicEvent] = []
        last_timestamp = 0

        for timestamp in sorted(events_by_time.keys()):
            events = events_by_time[timestamp]

            # Apply voice allocation strategy
            # Returns: [(motor_id, midi_note), ...] where midi_note=None means turn off
            actions = strategy.allocate(timestamp, events['on'], events['off'])

            # Convert actions to MusicEvents
            for i, (motor, midi_note) in enumerate(actions):
                # Calculate delta time (only for first event at this timestamp)
                if i == 0:
                    delta_ms = timestamp - last_timestamp
                else:
                    delta_ms = 0  # Simultaneous with previous event

                # Determine event type and frequency
                if midi_note is None:
                    # Turn motor off
                    event_type = EventType.NOTE_OFF
                    frequency = 0
                else:
                    # Turn motor on with transformed frequency
                    event_type = EventType.NOTE_ON
                    frequency = freq_transformer.transform(midi_note)

                # Create MusicEvent
                output_events.append(
                    MusicEvent(
                        delta_ms=delta_ms,
                        motor=motor,
                        event=event_type,
                        frequency=frequency
                    )
                )

            # Update last timestamp if we generated events
            if actions:
                last_timestamp = timestamp

        return output_events

    def get_info(self) -> str:
        """
        Get human-readable information about loaded MIDI file.

        Returns:
            Multi-line string with MIDI file statistics
        """
        if self.midi_file is None:
            return "No MIDI file loaded"

        # Calculate duration
        if self.timeline:
            duration_ms = self.timeline[-1].timestamp_ms
            duration_sec = duration_ms / 1000.0
        else:
            duration_sec = 0

        # Count note events
        note_on_count = sum(1 for e in self.timeline if e.is_note_on)
        note_off_count = sum(1 for e in self.timeline if not e.is_note_on)

        # Get note range
        if self.timeline:
            notes = [e.note for e in self.timeline]
            min_note = min(notes)
            max_note = max(notes)
            from midi_utils import get_note_name
            note_range = f"{get_note_name(min_note)} to {get_note_name(max_note)}"
        else:
            note_range = "N/A"

        info = f"""MIDI File Information:
  Tracks: {len(self.midi_file.tracks)}
  Ticks per beat: {self.ticks_per_beat}
  Duration: {duration_sec:.1f} seconds
  Total events: {len(self.timeline)} ({note_on_count} note-on, {note_off_count} note-off)
  Note range: {note_range}
  Tempo changes: {len(self.tempo_map) - 1}"""

        return info
