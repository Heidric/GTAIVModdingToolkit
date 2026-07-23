"""Supported replacement-audio inputs shared by UI and backend workflows."""

from __future__ import annotations

import os
from pathlib import Path


SUPPORTED_AUDIO_EXTENSIONS = (
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".aac",
    ".m4a",
)
AUDIO_FILE_FILTER = "Audio Files (" + " ".join(
    f"*{extension}" for extension in SUPPORTED_AUDIO_EXTENSIONS
) + ")"
_SUPPORTED_AUDIO_EXTENSION_SET = frozenset(SUPPORTED_AUDIO_EXTENSIONS)


def validate_replacement_audio(
    value: str | os.PathLike[str],
) -> Path:
    """Resolve and validate one supported replacement-audio file."""
    try:
        raw_path = os.fspath(value)
    except TypeError as exc:
        raise ValueError("Replacement audio path must identify a file.") from exc
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Replacement audio path must identify a file.")

    try:
        path = Path(raw_path).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise ValueError("Replacement audio path must identify a file.") from exc

    if not path.is_file():
        raise FileNotFoundError(f"Replacement audio not found: {path}")

    extension = path.suffix.casefold()
    if extension not in _SUPPORTED_AUDIO_EXTENSION_SET:
        supported = ", ".join(
            item.removeprefix(".").upper()
            for item in SUPPORTED_AUDIO_EXTENSIONS
        )
        actual = extension.removeprefix(".").upper() or "NO EXTENSION"
        raise ValueError(
            f"Unsupported replacement audio format: {actual}. "
            f"Supported formats: {supported}."
        )

    return path
