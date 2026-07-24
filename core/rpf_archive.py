"""Application-facing read-only RPF inspection and export operations."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from core.rpf import RPFParser


class _Parser(Protocol):
    paths: list[dict]

    def read_file(self, file_path: str) -> bytes: ...


ParserFactory = Callable[[str, str], _Parser]


@dataclass(frozen=True)
class RPFArchiveEntry:
    """One resolved file stored inside an RPF archive."""

    path: str
    size: int
    offset: int

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def parent(self) -> str:
        return self.path.rpartition("/")[0]


@dataclass(frozen=True)
class RPFArchiveSnapshot:
    """Immutable metadata collected from one parsed RPF archive."""

    archive_path: Path
    entries: tuple[RPFArchiveEntry, ...]

    def entry(self, entry_path: str) -> RPFArchiveEntry:
        normalized = normalize_entry_path(entry_path)
        for candidate in self.entries:
            if candidate.path == normalized:
                return candidate
        raise KeyError(f"RPF entry not found: {normalized}")


def normalize_entry_path(entry_path: str) -> str:
    """Normalize a user-facing RPF path without treating it as a local path."""
    if not isinstance(entry_path, str):
        raise TypeError("entry_path must be a string")
    normalized = entry_path.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or normalized.endswith("/"):
        raise ValueError("entry_path must identify an RPF file entry")
    if any(part in ("", ".", "..") for part in normalized.split("/")):
        raise ValueError("entry_path contains an invalid path component")
    return normalized


def _validated_archive_path(archive_path: str | os.PathLike[str]) -> Path:
    path = Path(archive_path).expanduser().resolve()
    if path.suffix.casefold() != ".rpf":
        raise ValueError("archive_path must point to an .rpf file")
    if not path.is_file():
        raise FileNotFoundError(f"RPF archive not found: {path}")
    return path


def _validated_executable_path(gtaiv_exe_path: str | os.PathLike[str]) -> Path:
    path = Path(gtaiv_exe_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"GTAIV.exe not found: {path}")
    return path


def _open_parser(
    archive_path: Path,
    executable_path: Path,
    parser_factory: ParserFactory | None,
) -> _Parser:
    factory = RPFParser if parser_factory is None else parser_factory
    return factory(str(archive_path), str(executable_path))


def inspect_rpf_archive(
    archive_path: str | os.PathLike[str],
    gtaiv_exe_path: str | os.PathLike[str],
    *,
    parser_factory: ParserFactory | None = None,
) -> RPFArchiveSnapshot:
    """Parse an RPF archive and return sorted, immutable file metadata."""
    archive = _validated_archive_path(archive_path)
    executable = _validated_executable_path(gtaiv_exe_path)
    parser = _open_parser(archive, executable, parser_factory)

    entries = []
    seen_paths = set()
    for raw_entry in parser.paths:
        path = normalize_entry_path(raw_entry["path"])
        size = int(raw_entry["size"])
        offset = int(raw_entry["offset"])
        if size < 0 or offset < 0:
            raise ValueError(f"RPF entry has an invalid byte range: {path}")
        if path in seen_paths:
            raise ValueError(f"RPF parser returned a duplicate entry path: {path}")
        seen_paths.add(path)
        entries.append(RPFArchiveEntry(path=path, size=size, offset=offset))

    return RPFArchiveSnapshot(
        archive_path=archive,
        entries=tuple(sorted(entries, key=lambda entry: entry.path.casefold())),
    )


def export_rpf_entry(
    archive_path: str | os.PathLike[str],
    gtaiv_exe_path: str | os.PathLike[str],
    entry_path: str,
    destination_path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    parser_factory: ParserFactory | None = None,
) -> Path:
    """Export one RPF entry to an explicit local destination."""
    archive = _validated_archive_path(archive_path)
    executable = _validated_executable_path(gtaiv_exe_path)
    normalized_entry = normalize_entry_path(entry_path)
    destination = Path(destination_path).expanduser().resolve()
    if destination.exists() and destination.is_dir():
        raise IsADirectoryError(f"Export destination is a directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    parser = _open_parser(archive, executable, parser_factory)
    data = parser.read_file(normalized_entry)

    if overwrite:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        try:
            with destination.open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            raise
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    if destination.stat().st_size != len(data):
        raise OSError(
            f"Exported file size does not match the RPF entry: {destination}"
        )
    return destination
