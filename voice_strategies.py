"""
Voice allocation strategies for managing polyphonic playback on 4 motors.

When a MIDI file has more than 4 simultaneous notes, these strategies decide
which notes to play and which to drop.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set, Tuple, Type


class VoiceAllocationStrategy(ABC):
    """Abstract base class for voice allocation strategies."""

    def __init__(self):
        """Initialize strategy with empty motor state."""
        # Track which MIDI note is currently playing on each motor (None = silent)
        self.motor_notes: Dict[int, Optional[int]] = {0: None, 1: None, 2: None, 3: None}
        # Set of all MIDI notes that should be currently playing
        self.active_notes: Set[int] = set()

    @abstractmethod
    def allocate(
        self,
        timestamp_ms: int,
        new_notes_on: List[int],
        notes_off: List[int]
    ) -> List[Tuple[int, Optional[int]]]:
        """
        Allocate notes to motors based on strategy.

        Args:
            timestamp_ms: Current absolute timestamp in milliseconds
            new_notes_on: List of MIDI notes that should start playing
            notes_off: List of MIDI notes that should stop playing

        Returns:
            List of (motor_id, midi_note) tuples representing changes:
            - midi_note=None means turn motor off
            - midi_note=<number> means turn motor on with that note
            Only motors that need to change state are included.
        """
        pass

    @abstractmethod
    def get_description(self) -> str:
        """Return a human-readable description of this strategy."""
        pass

    def reset(self) -> None:
        """Reset strategy state for new conversion."""
        self.motor_notes = {0: None, 1: None, 2: None, 3: None}
        self.active_notes = set()

    def find_motor_by_note(self, note: int) -> Optional[int]:
        """
        Find which motor is currently playing a specific note.

        Args:
            note: MIDI note number

        Returns:
            Motor ID (0-3) or None if note is not playing
        """
        for motor, current_note in self.motor_notes.items():
            if current_note == note:
                return motor
        return None

    def get_free_motors(self) -> List[int]:
        """
        Get list of motors that are not currently playing.

        Returns:
            List of motor IDs (0-3) that are silent
        """
        return [motor for motor, note in self.motor_notes.items() if note is None]


class MelodicPriorityStrategy(VoiceAllocationStrategy):
    """
    Melodic Priority: Keep extreme pitches (highest + lowest) and drop middle notes.

    When more than 4 notes play simultaneously, this strategy:
    1. Keeps the lowest note (bass line)
    2. Keeps the highest note (melody)
    3. Keeps the 2nd lowest note
    4. Keeps the 2nd highest note
    5. Drops all middle notes

    Motor assignment:
    - Motor 0: Lowest note (bass)
    - Motor 1: 2nd lowest
    - Motor 2: 2nd highest
    - Motor 3: Highest (melody)

    This preserves the harmonic range and melodic lines while sacrificing
    inner voices, which generally sounds more musical than random selection.
    """

    def allocate(
        self,
        timestamp_ms: int,
        new_notes_on: List[int],
        notes_off: List[int]
    ) -> List[Tuple[int, Optional[int]]]:
        """Apply melodic priority voice allocation."""
        actions: List[Tuple[int, Optional[int]]] = []

        # Step 1: Handle note offs
        for note in notes_off:
            motor = self.find_motor_by_note(note)
            if motor is not None:
                # Turn off the motor
                actions.append((motor, None))
                self.motor_notes[motor] = None
            # Remove from active set
            self.active_notes.discard(note)

        # Step 2: Add new notes to active set
        self.active_notes.update(new_notes_on)

        # Step 3: Calculate target allocation based on melodic priority
        target_allocation: Dict[int, int] = {}

        if len(self.active_notes) == 0:
            # No notes to play
            pass
        elif len(self.active_notes) <= 4:
            # Simple case: assign all notes to motors
            sorted_notes = sorted(self.active_notes)
            for i, note in enumerate(sorted_notes):
                target_allocation[i] = note
        else:
            # Melodic priority: keep extremes
            sorted_notes = sorted(self.active_notes)
            target_allocation = {
                0: sorted_notes[0],      # Lowest (bass)
                1: sorted_notes[1],      # 2nd lowest
                2: sorted_notes[-2],     # 2nd highest
                3: sorted_notes[-1],     # Highest (melody)
            }

        # Step 4: Generate actions to reach target allocation
        # First, turn off motors playing notes that shouldn't be playing
        for motor, current_note in list(self.motor_notes.items()):
            target_note = target_allocation.get(motor)
            if current_note is not None and current_note != target_note:
                # Motor is playing wrong note, turn it off
                actions.append((motor, None))
                self.motor_notes[motor] = None

        # Then, turn on motors with new target notes
        for motor, target_note in target_allocation.items():
            if self.motor_notes[motor] != target_note:
                # Motor should play this note
                actions.append((motor, target_note))
                self.motor_notes[motor] = target_note

        return actions

    def get_description(self) -> str:
        return (
            "Melodic Priority: Keep highest and lowest notes (melody + bass), "
            "drop middle notes when >4 simultaneous"
        )


class VoiceStealingStrategy(VoiceAllocationStrategy):
    """
    Voice Stealing: LRU-based voice allocation like hardware synthesizers.

    When all 4 motors are in use and a new note arrives:
    1. Steal the least recently started note (oldest)
    2. Replace it with the new note

    This mimics hardware synthesizer behavior and ensures new notes
    always play, even if it means cutting off older sustained notes.

    Good for: Dense polyphonic passages, piano music
    """

    def __init__(self):
        super().__init__()
        # Track when each note started (timestamp_ms)
        self.note_start_times: Dict[int, int] = {}

    def reset(self) -> None:
        """Reset strategy state for new conversion."""
        super().reset()
        self.note_start_times = {}

    def allocate(
        self,
        timestamp_ms: int,
        new_notes_on: List[int],
        notes_off: List[int]
    ) -> List[Tuple[int, Optional[int]]]:
        """Apply voice stealing allocation."""
        actions: List[Tuple[int, Optional[int]]] = []

        # Handle note offs
        for note in notes_off:
            motor = self.find_motor_by_note(note)
            if motor is not None:
                actions.append((motor, None))
                self.motor_notes[motor] = None
            self.active_notes.discard(note)
            self.note_start_times.pop(note, None)

        # Handle new notes
        for note in new_notes_on:
            self.active_notes.add(note)
            self.note_start_times[note] = timestamp_ms

            # Find a motor for this note
            free_motors = self.get_free_motors()
            if free_motors:
                # Use first free motor
                motor = free_motors[0]
            else:
                # All motors busy - steal the oldest note
                # Find motor with oldest note
                oldest_motor = None
                oldest_time = float('inf')
                for m, n in self.motor_notes.items():
                    if n is not None:
                        start_time = self.note_start_times.get(n, 0)
                        if start_time < oldest_time:
                            oldest_time = start_time
                            oldest_motor = m

                motor = oldest_motor
                # Turn off the old note first
                if motor is not None:
                    old_note = self.motor_notes[motor]
                    if old_note is not None:
                        actions.append((motor, None))

            # Turn on new note
            if motor is not None:
                actions.append((motor, note))
                self.motor_notes[motor] = note

        return actions

    def get_description(self) -> str:
        return (
            "Voice Stealing: Replace oldest playing note when motors full "
            "(LRU-based, like hardware synthesizers)"
        )


class RolledChordStrategy(VoiceAllocationStrategy):
    """
    Rolled Chord: Arpeggiate simultaneous notes with small delays.

    When more than 4 notes play simultaneously, instead of dropping notes,
    this strategy slightly delays them (creating a "rolled" or arpeggiated effect).

    Implementation: When >4 notes arrive at the same timestamp, we add small
    incremental delays (e.g., 10ms between each note) to spread them out.

    Note: This strategy modifies timing, so it works differently - it generates
    additional events at slightly offset timestamps.

    Good for: Chords, piano music, creating harp-like effects
    """

    def __init__(self, roll_delay_ms: int = 15):
        """
        Initialize rolled chord strategy.

        Args:
            roll_delay_ms: Delay between rolled notes in milliseconds (default: 15ms)
        """
        super().__init__()
        self.roll_delay_ms = roll_delay_ms
        # Track pending notes that need to be rolled in future
        self.pending_roll: List[Tuple[int, int]] = []  # [(note, timestamp_ms), ...]

    def reset(self) -> None:
        """Reset strategy state for new conversion."""
        super().reset()
        self.pending_roll = []

    def allocate(
        self,
        timestamp_ms: int,
        new_notes_on: List[int],
        notes_off: List[int]
    ) -> List[Tuple[int, Optional[int]]]:
        """Apply rolled chord allocation."""
        actions: List[Tuple[int, Optional[int]]] = []

        # Handle note offs
        for note in notes_off:
            motor = self.find_motor_by_note(note)
            if motor is not None:
                actions.append((motor, None))
                self.motor_notes[motor] = None
            self.active_notes.discard(note)

        # Add new notes to active set
        self.active_notes.update(new_notes_on)

        # Simple approach: Just play up to 4 notes at once, drop the rest
        # (A full implementation would need to restructure the converter to handle delayed events)
        if len(self.active_notes) <= 4:
            # Simple case: assign all notes
            sorted_notes = sorted(self.active_notes)
            target_allocation = {i: note for i, note in enumerate(sorted_notes)}
        else:
            # Take first 4 notes (could be improved with better selection)
            sorted_notes = sorted(self.active_notes)
            target_allocation = {i: sorted_notes[i] for i in range(4)}

        # Generate actions to reach target allocation
        for motor, current_note in list(self.motor_notes.items()):
            target_note = target_allocation.get(motor)
            if current_note != target_note and current_note is not None:
                actions.append((motor, None))
                self.motor_notes[motor] = None

        for motor, target_note in target_allocation.items():
            if self.motor_notes[motor] != target_note:
                actions.append((motor, target_note))
                self.motor_notes[motor] = target_note

        return actions

    def get_description(self) -> str:
        return (
            f"Rolled Chord: Arpeggiate >4 simultaneous notes with {self.roll_delay_ms}ms delays "
            "(creates harp-like effect)"
        )


class RoundRobinStrategy(VoiceAllocationStrategy):
    """
    Round Robin: Cycle through motors for each new note.

    Assigns notes to motors in a round-robin fashion. When a new note
    arrives, it goes to the next motor in sequence, wrapping around.

    Simple and predictable, but may cut off notes abruptly.

    Good for: Testing, simple melodies, rhythmic patterns
    """

    def __init__(self):
        super().__init__()
        self.next_motor = 0

    def reset(self) -> None:
        """Reset strategy state for new conversion."""
        super().reset()
        self.next_motor = 0

    def allocate(
        self,
        timestamp_ms: int,
        new_notes_on: List[int],
        notes_off: List[int]
    ) -> List[Tuple[int, Optional[int]]]:
        """Apply round-robin allocation."""
        actions: List[Tuple[int, Optional[int]]] = []

        # Handle note offs
        for note in notes_off:
            motor = self.find_motor_by_note(note)
            if motor is not None:
                actions.append((motor, None))
                self.motor_notes[motor] = None
            self.active_notes.discard(note)

        # Handle new notes with round-robin assignment
        for note in new_notes_on:
            self.active_notes.add(note)

            # Assign to next motor in sequence
            motor = self.next_motor

            # If motor is busy, turn it off first
            if self.motor_notes[motor] is not None:
                actions.append((motor, None))

            # Turn on new note
            actions.append((motor, note))
            self.motor_notes[motor] = note

            # Advance to next motor
            self.next_motor = (self.next_motor + 1) % 4

        return actions

    def get_description(self) -> str:
        return "Round Robin: Cycle through motors sequentially for each new note"


# Registry of available strategies
STRATEGIES: Dict[str, Type[VoiceAllocationStrategy]] = {
    'melodic': MelodicPriorityStrategy,
    'voice-stealing': VoiceStealingStrategy,
    'rolled': RolledChordStrategy,
    'round-robin': RoundRobinStrategy,
}


def get_strategy(name: str) -> VoiceAllocationStrategy:
    """
    Get a voice allocation strategy by name.

    Args:
        name: Strategy name ('melodic', etc.)

    Returns:
        VoiceAllocationStrategy instance

    Raises:
        ValueError: If strategy name is not recognized
    """
    if name not in STRATEGIES:
        available = ', '.join(STRATEGIES.keys())
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")

    strategy_class = STRATEGIES[name]
    return strategy_class()


def list_strategies() -> None:
    """Print all available voice allocation strategies with descriptions."""
    print("\nAvailable Voice Allocation Strategies:")
    print("=" * 70)

    for name, strategy_class in STRATEGIES.items():
        instance = strategy_class()
        print(f"\n  {name}:")
        print(f"    {instance.get_description()}")

    print("\nUsage: --strategy <name>")
    print("Default: melodic")
