"""
MIDI utility functions for frequency conversion and range constants.
"""

# Motor frequency range based on motors.h definitions
# Tested range: C4 (262 Hz) to F6 (1396 Hz)
MIN_MOTOR_FREQUENCY_HZ = 262   # C4
MAX_MOTOR_FREQUENCY_HZ = 1396  # F6

# MIDI note numbers for reference
# C4 = MIDI note 60 = 262 Hz
# F6 = MIDI note 77 = 1396 Hz
MIDI_NOTE_C4 = 60  # 262 Hz - minimum safe frequency
MIDI_NOTE_F6 = 77  # 1396 Hz - maximum safe frequency
MIDI_NOTE_A4 = 69  # Concert pitch: A4 = 440 Hz


def midi_note_to_frequency(note: int) -> int:
    """
    Convert MIDI note number to frequency in Hz.

    Uses equal temperament tuning with A4 = 440 Hz as reference.

    Formula: f = 440 * 2^((n - 69) / 12)

    Args:
        note: MIDI note number (0-127)
              - Note 0 = C-1 (~8 Hz)
              - Note 60 = C4 (Middle C, ~262 Hz)
              - Note 69 = A4 (Concert pitch, 440 Hz)
              - Note 127 = G9 (~12543 Hz)

    Returns:
        Frequency in Hz (rounded to nearest integer)

    Examples:
        >>> midi_note_to_frequency(60)  # C4 (Middle C)
        262
        >>> midi_note_to_frequency(69)  # A4 (Concert pitch)
        440
        >>> midi_note_to_frequency(81)  # A5
        880
    """
    frequency = 440.0 * (2.0 ** ((note - 69) / 12.0))
    return int(round(frequency))


def frequency_to_midi_note(frequency: float) -> int:
    """
    Convert frequency in Hz to nearest MIDI note number.

    Inverse of midi_note_to_frequency().

    Args:
        frequency: Frequency in Hz

    Returns:
        MIDI note number (0-127)

    Examples:
        >>> frequency_to_midi_note(262)
        60  # C4
        >>> frequency_to_midi_note(440)
        69  # A4
    """
    import math
    note = 69 + 12 * math.log2(frequency / 440.0)
    return int(round(note))


def is_note_in_motor_range(note: int, min_note: int = MIDI_NOTE_C4, max_note: int = MIDI_NOTE_F6) -> bool:
    """
    Check if a MIDI note falls within the motor's safe frequency range.

    Args:
        note: MIDI note number
        min_note: Minimum MIDI note (default: C4 = 262 Hz)
        max_note: Maximum MIDI note (default: F6 = 1396 Hz)

    Returns:
        True if note is within safe motor range
    """
    return min_note <= note <= max_note


def get_note_name(note: int) -> str:
    """
    Get the musical name of a MIDI note.

    Args:
        note: MIDI note number (0-127)

    Returns:
        Note name (e.g., "C4", "A#5", "Gb3")

    Examples:
        >>> get_note_name(60)
        'C4'
        >>> get_note_name(69)
        'A4'
        >>> get_note_name(61)
        'C#4'
    """
    note_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    octave = (note // 12) - 1
    note_name = note_names[note % 12]
    return f"{note_name}{octave}"
