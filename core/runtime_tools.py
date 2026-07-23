"""Qt-independent runtime helpers shared by backend and UI modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def resource_path(relative_path: str) -> str:
    """Return a bundled resource path independent of the working directory."""
    base_path = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
    )
    return str(base_path / relative_path)


def check_ffmpeg() -> bool:
    """Return whether FFmpeg is available in the current process PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
