"""
Frequency transformation strategies for mapping MIDI notes to motor frequencies.

This module provides pluggable transformers that handle notes outside the
Crazyflie motor's frequency range by shifting octaves, clamping, or other strategies.
"""

from abc import ABC, abstractmethod
from typing import Dict, Type
from midi_utils import midi_note_to_frequency, MIDI_NOTE_C4, MIDI_NOTE_F6


class FrequencyTransformer(ABC):
    """Abstract base class for frequency transformation strategies."""

    @abstractmethod
    def transform(self, midi_note: int) -> int:
        """
        Transform a MIDI note to a valid motor frequency.

        Args:
            midi_note: MIDI note number (0-127)

        Returns:
            Frequency in Hz suitable for Crazyflie motors
        """
        pass

    @abstractmethod
    def get_description(self) -> str:
        """Return a human-readable description of this transformer."""
        pass


class OctaveClippingTransformer(FrequencyTransformer):
    """
    Shift notes by octaves to fit within a valid range.

    When a note is outside the range, it is shifted up or down by octaves
    (12 semitones) until it fits. This preserves the pitch class but changes
    the octave, which generally sounds better than clamping.

    Example:
        min_note=60 (C4), max_note=77 (F6)
        Note 48 (C3) → shifted up to 60 (C4)
        Note 89 (F7) → shifted down to 77 (F6)
    """

    def __init__(self, min_note: int = MIDI_NOTE_C4, max_note: int = MIDI_NOTE_F6):
        """
        Initialize octave clipping transformer.

        Args:
            min_note: Minimum MIDI note (default: 60 = C4, 262 Hz)
            max_note: Maximum MIDI note (default: 77 = F6, 1396 Hz)
        """
        self.min_note = min_note
        self.max_note = max_note

    def transform(self, midi_note: int) -> int:
        """Shift note by octaves to fit range, then convert to Hz."""
        # Shift down if too high
        while midi_note > self.max_note:
            midi_note -= 12

        # Shift up if too low
        while midi_note < self.min_note:
            midi_note += 12

        return midi_note_to_frequency(midi_note)

    def get_description(self) -> str:
        from midi_utils import get_note_name
        return (
            f"Octave Clipping: Shift notes to range {get_note_name(self.min_note)} "
            f"to {get_note_name(self.max_note)} by moving octaves"
        )


class PassthroughTransformer(FrequencyTransformer):
    """
    No transformation - use raw MIDI note frequencies.

    This transformer passes through the frequency without any modification.
    Use this if motor testing shows it can handle the full MIDI range,
    or for experimentation to find the actual limits.
    """

    def transform(self, midi_note: int) -> int:
        """Convert MIDI note directly to frequency without transformation."""
        return midi_note_to_frequency(midi_note)

    def get_description(self) -> str:
        return "Passthrough: No transformation, use full MIDI frequency range"


class RangeClampingTransformer(FrequencyTransformer):
    """
    Clamp frequency to min/max Hz limits.

    Notes outside the frequency range are hard-clamped to the nearest limit.
    This may sound wrong (wrong pitch) but ensures frequencies never exceed
    motor capabilities.

    Example:
        min_hz=130, max_hz=1047
        Note generating 65 Hz → clamped to 130 Hz (sounds like different note)
        Note generating 2093 Hz → clamped to 1047 Hz (sounds like different note)
    """

    def __init__(self, min_hz: int = 130, max_hz: int = 1047):
        """
        Initialize range clamping transformer.

        Args:
            min_hz: Minimum frequency in Hz (default: 130 ≈ C3)
            max_hz: Maximum frequency in Hz (default: 1047 ≈ C6)
        """
        self.min_hz = min_hz
        self.max_hz = max_hz

    def transform(self, midi_note: int) -> int:
        """Convert to frequency and clamp to valid range."""
        freq = midi_note_to_frequency(midi_note)
        return max(self.min_hz, min(self.max_hz, freq))

    def get_description(self) -> str:
        return f"Range Clamping: Hard clamp frequencies to {self.min_hz}-{self.max_hz} Hz"


# Registry of available transformers
TRANSFORMERS: Dict[str, Type[FrequencyTransformer]] = {
    'octave-clip': OctaveClippingTransformer,
    'none': PassthroughTransformer,
    'clamp': RangeClampingTransformer,
}


def get_transformer(name: str) -> FrequencyTransformer:
    """
    Get a frequency transformer by name.

    Args:
        name: Transformer name ('octave-clip', 'none', 'clamp')

    Returns:
        FrequencyTransformer instance

    Raises:
        ValueError: If transformer name is not recognized
    """
    if name not in TRANSFORMERS:
        available = ', '.join(TRANSFORMERS.keys())
        raise ValueError(f"Unknown transformer '{name}'. Available: {available}")

    transformer_class = TRANSFORMERS[name]
    return transformer_class()


def list_transformers() -> None:
    """Print all available frequency transformers with descriptions."""
    print("\nAvailable Frequency Transformers:")
    print("=" * 70)

    for name, transformer_class in TRANSFORMERS.items():
        instance = transformer_class()
        print(f"\n  {name}:")
        print(f"    {instance.get_description()}")

    print("\nUsage: --transpose <name>")
    print("Default: octave-clip")
