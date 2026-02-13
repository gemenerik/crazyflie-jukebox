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


# Registry of available strategies
STRATEGIES: Dict[str, Type[VoiceAllocationStrategy]] = {
    'melodic': MelodicPriorityStrategy,
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
