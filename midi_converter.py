"""
MIDI file converter for Crazyflie Jukebox.

Converts MIDI files to MusicEvent sequences suitable for upload to Crazyflie.
Handles tempo changes, multi-track files, and voice allocation for 4-motor polyphony.
"""

import os
from typing import List, Tuple, Dict, Optional
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
    channel: int = 0     # MIDI channel (0-15)


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
        self.track_info: List[Dict] = []  # Track metadata for user selection
        self._type0_split: bool = False  # True when Type 0 file is split by channel

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

        # Analyze tracks for user selection
        self.track_info = self._analyze_tracks()

        # Build timeline of all note events (will be rebuilt during convert if tracks selected)
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

    def _is_type0_multichannel(self) -> bool:
        """Check if this is a Type 0 MIDI file with multiple note channels."""
        if self.midi_file.type != 0 or len(self.midi_file.tracks) != 1:
            return False
        channels = set()
        for msg in self.midi_file.tracks[0]:
            if msg.type in ('note_on', 'note_off') and hasattr(msg, 'channel'):
                channels.add(msg.channel)
        return len(channels) > 1

    def _analyze_tracks(self) -> List[Dict]:
        """
        Analyze each track to help users select which ones to use.

        For Type 0 MIDI files with multiple channels, creates virtual tracks
        per channel so users can select individual instruments.

        Returns:
            List of dicts with track metadata: {
                'index': int,
                'name': str,
                'instrument': str,
                'note_count': int,
                'note_range': tuple(min, max),
                'channels': set
            }
        """
        # For Type 0 files with multiple channels, split by channel
        if self._is_type0_multichannel():
            return self._analyze_channels_as_tracks()

        track_info = []

        for track_idx, track in enumerate(self.midi_file.tracks):
            info = {
                'index': track_idx,
                'name': f'Track {track_idx}',
                'instrument': None,
                'note_count': 0,
                'note_range': (127, 0),  # (min, max)
                'channels': set()
            }

            notes = []
            for msg in track:
                # Extract track name
                if msg.type == 'track_name':
                    info['name'] = msg.name

                # Extract program change (instrument)
                if msg.type == 'program_change':
                    # MIDI General MIDI instrument names
                    info['instrument'] = self._get_instrument_name(msg.program)

                # Count notes
                if msg.type in ('note_on', 'note_off'):
                    if hasattr(msg, 'channel'):
                        info['channels'].add(msg.channel)

                if msg.type == 'note_on' and msg.velocity > 0:
                    info['note_count'] += 1
                    notes.append(msg.note)

            # Calculate note range
            if notes:
                info['note_range'] = (min(notes), max(notes))

            track_info.append(info)

        return track_info

    def _analyze_channels_as_tracks(self) -> List[Dict]:
        """
        Analyze a Type 0 MIDI file by splitting channels into virtual tracks.

        Returns:
            List of dicts with per-channel track metadata, using channel number as index.
        """
        if self.midi_file is None:
            return []
        track = self.midi_file.tracks[0]

        # Collect per-channel info
        channel_data: Dict[int, Dict] = {}
        track_name = None
        programs: Dict[int, int] = {}  # channel -> program number

        for msg in track:
            if msg.type == 'track_name':
                track_name = msg.name
            if msg.type == 'program_change':
                programs[msg.channel] = msg.program
            if msg.type in ('note_on', 'note_off') and hasattr(msg, 'channel'):
                ch = msg.channel
                if ch not in channel_data:
                    channel_data[ch] = {'notes': [], 'note_count': 0}
                if msg.type == 'note_on' and msg.velocity > 0:
                    channel_data[ch]['note_count'] += 1
                    channel_data[ch]['notes'].append(msg.note)

        self._type0_split = True  # Flag so _build_timeline knows to filter by channel

        track_info = []
        for ch in sorted(channel_data.keys()):
            data = channel_data[ch]
            instrument = self._get_instrument_name(programs[ch]) if ch in programs else None
            # Channel 9 is always percussion in GM
            if ch == 9:
                name = "Percussion"
                if instrument is None:
                    instrument = "Percussion"
            else:
                name = f"Ch {ch}"
                if track_name and len(channel_data) > 1:
                    name = f"Ch {ch}"

            info = {
                'index': ch,  # Use channel number as index for virtual tracks
                'name': name,
                'instrument': instrument,
                'note_count': data['note_count'],
                'note_range': (min(data['notes']), max(data['notes'])) if data['notes'] else (127, 0),
                'channels': {ch}
            }
            track_info.append(info)

        return track_info

    def _get_instrument_name(self, program: int) -> str:
        """Get General MIDI instrument name from program number."""
        # Simplified GM instrument names (0-127)
        instruments = [
            # Piano (0-7)
            "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
            "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavi",
            # Chromatic Percussion (8-15)
            "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba", "Xylophone",
            "Tubular Bells", "Dulcimer",
            # Organ (16-23)
            "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ",
            "Accordion", "Harmonica", "Tango Accordion",
            # Guitar (24-31)
            "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
            "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
            "Distortion Guitar", "Guitar harmonics",
            # Bass (32-39)
            "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
            "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
            # Strings (40-47)
            "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings",
            "Orchestral Harp", "Timpani",
            # Ensemble (48-55)
            "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
            "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
            # Brass (56-63)
            "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section",
            "Synth Brass 1", "Synth Brass 2",
            # Reed (64-71)
            "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn",
            "Bassoon", "Clarinet",
            # Pipe (72-79)
            "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi",
            "Whistle", "Ocarina",
            # Synth Lead (80-87)
            "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
            "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
            # Synth Pad (88-95)
            "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
            "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
            # Synth Effects (96-103)
            "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
            "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
            # Ethnic (104-111)
            "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bag pipe", "Fiddle", "Shanai",
            # Percussive (112-119)
            "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom",
            "Synth Drum", "Reverse Cymbal",
            # Sound Effects (120-127)
            "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring",
            "Helicopter", "Applause", "Gunshot"
        ]
        if 0 <= program < len(instruments):
            return instruments[program]
        return f"Program {program}"

    def _build_timeline(self, selected_tracks: Optional[List[int]] = None) -> List[MidiNote]:
        """
        Build absolute timeline of note events from selected tracks.

        Merges selected tracks into a single sorted timeline, converting
        relative tick times to absolute milliseconds.

        For Type 0 multichannel files, selected_tracks refers to channel numbers
        instead of track indices.

        Args:
            selected_tracks: List of track indices (or channel numbers for Type 0)
                             to include (None = all)

        Returns:
            List of MidiNote objects sorted by timestamp
        """
        all_events: List[MidiNote] = []
        filter_by_channel = getattr(self, '_type0_split', False)

        for track_idx, track in enumerate(self.midi_file.tracks):
            # For normal multi-track files, skip unselected tracks
            if not filter_by_channel and selected_tracks is not None and track_idx not in selected_tracks:
                continue

            current_tick = 0

            for msg in track:
                current_tick += msg.time

                # Handle note_on messages
                if msg.type == 'note_on':
                    channel = msg.channel if hasattr(msg, 'channel') else 0
                    # For Type 0 files, filter by channel
                    if filter_by_channel and selected_tracks is not None and channel not in selected_tracks:
                        continue
                    timestamp_ms = self._ticks_to_ms(current_tick)
                    # MIDI spec: note_on with velocity=0 is equivalent to note_off
                    is_on = msg.velocity > 0
                    all_events.append(MidiNote(timestamp_ms, msg.note, is_on, channel))

                # Handle note_off messages
                elif msg.type == 'note_off':
                    channel = msg.channel if hasattr(msg, 'channel') else 0
                    if filter_by_channel and selected_tracks is not None and channel not in selected_tracks:
                        continue
                    timestamp_ms = self._ticks_to_ms(current_tick)
                    all_events.append(MidiNote(timestamp_ms, msg.note, False, channel))

        # Sort by timestamp (stable sort preserves order of simultaneous events)
        all_events.sort(key=lambda e: e.timestamp_ms)

        return all_events

    def convert(
        self,
        strategy: VoiceAllocationStrategy = None,
        freq_transformer: FrequencyTransformer = None,
        selected_tracks: Optional[List[int]] = None
    ) -> List[MusicEvent]:
        """
        Convert MIDI timeline to MusicEvent sequence.

        Args:
            strategy: Voice allocation strategy (default: MelodicPriorityStrategy)
            freq_transformer: Frequency transformer (default: OctaveClippingTransformer)
            selected_tracks: List of track indices to include (None = all tracks)

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

        # Rebuild timeline if specific tracks selected
        if selected_tracks is not None:
            timeline = self._build_timeline(selected_tracks)
            if len(timeline) == 0:
                raise MidiConversionError(f"Selected tracks {selected_tracks} have no note events")
        else:
            timeline = self.timeline

        # Reset strategy state
        strategy.reset()

        # Group events by timestamp
        events_by_time: Dict[int, Dict[str, List[int]]] = {}
        for event in timeline:
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

    def get_track_info(self) -> str:
        """
        Get formatted track information for user selection.

        Returns:
            Multi-line string with track details
        """
        if not self.track_info:
            return "No track information available"

        from midi_utils import get_note_name

        lines = ["\nMIDI Tracks:"]
        lines.append("=" * 80)
        lines.append(f"{'#':<4} {'Name':<25} {'Instrument':<25} {'Notes':<8} {'Range':<15}")
        lines.append("-" * 80)

        for track in self.track_info:
            # Skip tracks with no notes
            if track['note_count'] == 0:
                continue

            idx = track['index']
            name = track['name'][:24]
            instrument = (track['instrument'] or "Unknown")[:24]
            note_count = track['note_count']

            if track['note_range'][0] <= track['note_range'][1]:
                min_note = get_note_name(track['note_range'][0])
                max_note = get_note_name(track['note_range'][1])
                note_range = f"{min_note}-{max_note}"
            else:
                note_range = "N/A"

            lines.append(f"{idx:<4} {name:<25} {instrument:<25} {note_count:<8} {note_range:<15}")

        lines.append("=" * 80)
        return "\n".join(lines)

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

        track_line = f"  Tracks: {len(self.midi_file.tracks)}"
        if self._type0_split:
            track_line += f" (Type 0 — split into {len(self.track_info)} channels)"

        info = f"""MIDI File Information:
{track_line}
  Ticks per beat: {self.ticks_per_beat}
  Duration: {duration_sec:.1f} seconds
  Total events: {len(self.timeline)} ({note_on_count} note-on, {note_off_count} note-off)
  Note range: {note_range}
  Tempo changes: {len(self.tempo_map) - 1}"""

        return info
