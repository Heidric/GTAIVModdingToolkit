"""Create privacy-conscious diagnostic archives for troubleshooting."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from build_info import build_summary

from .app_logging import (
    application_log_directory,
    flush_application_logs,
)

_MAX_LOG_BYTES = 2 * 1024 * 1024
_MAX_LOG_FILES = 4


@dataclass(frozen=True)
class SupportBundleResult:
    output_path: Path
    included_files: tuple[str, ...]


def _path_variants(value: str | os.PathLike[str] | None) -> set[str]:
    if value is None:
        return set()

    raw = os.fspath(value).strip()
    if not raw:
        return set()

    expanded = os.path.abspath(os.path.expanduser(raw))
    variants = {
        raw,
        expanded,
        raw.replace("\\", "/"),
        raw.replace("/", "\\"),
        expanded.replace("\\", "/"),
        expanded.replace("/", "\\"),
    }
    return {item.rstrip("/\\") for item in variants if item.rstrip("/\\")}


def redact_text(
    text: str,
    *,
    gtaiv_path: str | os.PathLike[str] | None = None,
    home_directory: str | os.PathLike[str] | None = None,
    temporary_directory: str | os.PathLike[str] | None = None,
) -> str:
    """Replace user-specific absolute paths with stable placeholders."""

    replacements: list[tuple[str, str]] = []
    for value, marker in (
        (gtaiv_path, "<GTAIV_PATH>"),
        (home_directory if home_directory is not None else Path.home(), "<USER_HOME>"),
        (
            temporary_directory if temporary_directory is not None else tempfile.gettempdir(),
            "<TEMP_PATH>",
        ),
    ):
        replacements.extend((variant, marker) for variant in _path_variants(value))

    result = text
    for variant, marker in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        result = re.sub(re.escape(variant), marker, result, flags=re.IGNORECASE)
    return result


def _redact_value(
    value: object,
    *,
    gtaiv_path: str | os.PathLike[str] | None = None,
    home_directory: str | os.PathLike[str] | None = None,
    temporary_directory: str | os.PathLike[str] | None = None,
) -> object:
    """Redact every string in a structured diagnostic value before JSON encoding."""

    if isinstance(value, str):
        return redact_text(
            value,
            gtaiv_path=gtaiv_path,
            home_directory=home_directory,
            temporary_directory=temporary_directory,
        )
    if isinstance(value, dict):
        return {
            key: _redact_value(
                item,
                gtaiv_path=gtaiv_path,
                home_directory=home_directory,
                temporary_directory=temporary_directory,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_value(
                item,
                gtaiv_path=gtaiv_path,
                home_directory=home_directory,
                temporary_directory=temporary_directory,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _redact_value(
                item,
                gtaiv_path=gtaiv_path,
                home_directory=home_directory,
                temporary_directory=temporary_directory,
            )
            for item in value
        )
    return value


def _resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root).resolve()
    return Path(__file__).resolve().parents[1]


def _file_status(path: Path) -> dict[str, object]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    except OSError as exc:
        return {"path": str(path), "exists": None, "error": str(exc)}

    return {
        "path": str(path),
        "exists": True,
        "kind": "directory" if path.is_dir() else "file",
        "size": stat.st_size if path.is_file() else None,
        "modified_utc": datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(),
    }


def _installation_snapshot(gtaiv_path: Path | None) -> dict[str, object]:
    if gtaiv_path is None:
        return {"provided": False}

    root = gtaiv_path.expanduser().resolve()
    original_sfx = root / "pc" / "audio" / "sfx"
    update_sfx = root / "update" / "pc" / "audio" / "sfx"
    original_textures = root / "pc" / "textures"
    update_textures = root / "update" / "pc" / "textures"

    paths = [
        root,
        root / "GTAIV.exe",
        original_sfx,
        update_sfx,
        root / "pc" / "audio" / "config" / "sounds.dat15",
        root / "update" / "pc" / "audio" / "config" / "sounds.dat15",
        original_textures,
        update_textures,
    ]
    for directory in (original_textures, update_textures):
        if directory.is_dir():
            paths.extend(sorted(directory.glob("radio_hud*.wtd")))

    def rpf_count(directory: Path) -> int | None:
        try:
            return sum(1 for _ in directory.glob("radio_*.rpf")) if directory.is_dir() else 0
        except OSError:
            return None

    return {
        "provided": True,
        "root": str(root),
        "radio_rpf_count": {
            "original": rpf_count(original_sfx),
            "fusionfix_override": rpf_count(update_sfx),
        },
        "paths": [_file_status(path) for path in paths],
    }


def _environment_snapshot(gtaiv_path: Path | None) -> dict[str, object]:
    resource_root = _resource_root()
    return {
        "build": build_summary().splitlines(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "executable": sys.executable,
            "frozen": bool(getattr(sys, "frozen", False)),
            "working_directory": os.getcwd(),
        },
        "dependencies": {
            "ffmpeg": shutil.which("ffmpeg"),
            "ivam": _file_status(resource_root / "tools" / "ivam.exe"),
            "ivaudioconv": _file_status(resource_root / "tools" / "IVAudioConv.exe"),
        },
        "game": _installation_snapshot(gtaiv_path),
    }


def _read_log_tail(path: Path) -> str:
    with path.open("rb") as stream:
        try:
            stream.seek(-_MAX_LOG_BYTES, os.SEEK_END)
        except OSError:
            stream.seek(0)
        payload = stream.read(_MAX_LOG_BYTES)
    return payload.decode("utf-8", errors="replace")


def _log_files(log_directory: Path) -> tuple[Path, ...]:
    candidates = []
    for path in log_directory.glob("app.log*"):
        if path.is_file():
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            candidates.append((modified, path))
    candidates.sort(reverse=True)
    return tuple(path for _, path in candidates[:_MAX_LOG_FILES])


def create_support_bundle(
    output_path: str | os.PathLike[str],
    *,
    gtaiv_path: str | os.PathLike[str] | None = None,
    log_directory: str | os.PathLike[str] | None = None,
    home_directory: str | os.PathLike[str] | None = None,
    temporary_directory: str | os.PathLike[str] | None = None,
) -> SupportBundleResult:
    """Create an atomic ZIP containing redacted diagnostics and recent logs."""

    destination = Path(output_path).expanduser().resolve()
    if destination.suffix.casefold() != ".zip":
        raise ValueError("support bundle output must use the .zip extension")
    destination.parent.mkdir(parents=True, exist_ok=True)

    game_root = Path(gtaiv_path).expanduser().resolve() if gtaiv_path else None
    logs_root = (
        Path(log_directory).expanduser().resolve()
        if log_directory is not None
        else application_log_directory()
    )
    home = Path(home_directory).expanduser().resolve() if home_directory else Path.home()
    temp_root = (
        Path(temporary_directory).expanduser().resolve()
        if temporary_directory is not None
        else Path(tempfile.gettempdir()).resolve()
    )

    flush_application_logs()
    snapshot = _redact_value(
        _environment_snapshot(game_root),
        gtaiv_path=game_root,
        home_directory=home,
        temporary_directory=temp_root,
    )
    snapshot_text = json.dumps(snapshot, indent=2, ensure_ascii=False)

    included = ["diagnostics.json", "privacy.txt"]
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(fd)
    temporary_path = Path(temporary_name)

    try:
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            archive.writestr("diagnostics.json", snapshot_text + "\n")
            archive.writestr(
                "privacy.txt",
                (
                    "This archive contains application/build information, file metadata, "
                    "dependency availability, and recent text logs.\n"
                    "It does not contain GTA IV executables, RPF/WTD archives, audio, "
                    "replacement images, or other game-file contents.\n"
                    "Known user-home, temporary, and selected GTA IV paths are replaced "
                    "with placeholders. Review the archive before sharing it.\n"
                ),
            )

            for index, log_path in enumerate(_log_files(logs_root)):
                try:
                    log_text = _read_log_tail(log_path)
                except OSError as exc:
                    log_text = f"Unable to read {log_path.name}: {exc}\n"
                log_text = redact_text(
                    log_text,
                    gtaiv_path=game_root,
                    home_directory=home,
                    temporary_directory=temp_root,
                )
                archive_name = "logs/app.log" if index == 0 else f"logs/app-{index}.log"
                archive.writestr(archive_name, log_text)
                included.append(archive_name)

        os.replace(temporary_path, destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return SupportBundleResult(
        output_path=destination,
        included_files=tuple(included),
    )
