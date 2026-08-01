"""Application-facing RPF inspection, export, and replacement operations."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from core.archive_backups import prune_archive_backups
from core.installation_lock import installation_lock
from core.rpf import RPFParser


class _Parser(Protocol):
    paths: list[dict]

    def read_file(self, file_path: str) -> bytes: ...

    def add_file(self, source_file: str, rpf_path: str) -> None: ...


ParserFactory = Callable[[str, str], _Parser]
ReplaceFile = Callable[[str, str], None]


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


@dataclass(frozen=True)
class RPFReplacementResult:
    """Committed replacement metadata and the retained original archive backup."""

    archive_path: Path
    backup_path: Path
    entry_path: str
    previous_size: int
    replacement_size: int
    previous_offset: int
    final_offset: int

    @property
    def relocated(self) -> bool:
        return self.previous_offset != self.final_offset


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
    if path.suffix.casefold() not in {".rpf", ".img"}:
        raise ValueError("archive_path must point to an .rpf or .img file")
    if not path.is_file():
        raise FileNotFoundError(f"GTA IV archive not found: {path}")
    return path


def _validated_executable_path(gtaiv_exe_path: str | os.PathLike[str]) -> Path:
    path = Path(gtaiv_exe_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"GTAIV.exe not found: {path}")
    return path


def _validated_replacement_path(
    replacement_path: str | os.PathLike[str],
    archive_path: Path,
) -> Path:
    path = Path(replacement_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"RPF replacement file not found: {path}")
    if path == archive_path:
        raise ValueError("RPF replacement file must not be the archive itself")
    return path


def _open_parser(
    archive_path: Path,
    executable_path: Path,
    parser_factory: ParserFactory | None,
) -> _Parser:
    factory = RPFParser if parser_factory is None else parser_factory
    return factory(str(archive_path), str(executable_path))


def _snapshot_from_parser(archive_path: Path, parser: _Parser) -> RPFArchiveSnapshot:
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
        archive_path=archive_path,
        entries=tuple(sorted(entries, key=lambda entry: entry.path.casefold())),
    )


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
    return _snapshot_from_parser(archive, parser)


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


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _temporary_copy(
    source: Path,
    *,
    directory: Path,
    prefix: str,
    suffix: str,
) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=prefix,
        suffix=suffix,
        dir=directory,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        with temporary.open("r+b") as copied:
            os.fsync(copied.fileno())
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _unique_backup_path(archive_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = archive_path.with_name(
        f"{archive_path.stem}.backup-{timestamp}{archive_path.suffix}"
    )
    counter = 1
    while candidate.exists():
        candidate = archive_path.with_name(
            f"{archive_path.stem}.backup-{timestamp}-{counter}{archive_path.suffix}"
        )
        counter += 1
    return candidate


def _validate_replacement_snapshot(
    before: RPFArchiveSnapshot,
    after: RPFArchiveSnapshot,
    entry_path: str,
    expected_size: int,
) -> RPFArchiveEntry:
    before_by_path = {entry.path: entry for entry in before.entries}
    after_by_path = {entry.path: entry for entry in after.entries}
    if before_by_path.keys() != after_by_path.keys():
        raise RuntimeError("Staged RPF verification failed: archive entries changed")

    for path, previous in before_by_path.items():
        current = after_by_path[path]
        if path == entry_path:
            continue
        if current != previous:
            raise RuntimeError(
                "Staged RPF verification failed: unrelated entry metadata changed: "
                f"{path}"
            )

    replacement = after_by_path[entry_path]
    if replacement.size != expected_size:
        raise RuntimeError(
            "Staged RPF verification failed: replacement entry size does not match "
            "the source file"
        )
    return replacement


def _verify_replacement(
    archive_path: Path,
    executable_path: Path,
    parser_factory: ParserFactory | None,
    before: RPFArchiveSnapshot,
    entry_path: str,
    expected_size: int,
    expected_sha256: str,
) -> RPFArchiveEntry:
    parser = _open_parser(archive_path, executable_path, parser_factory)
    snapshot = _snapshot_from_parser(archive_path, parser)
    replacement = _validate_replacement_snapshot(
        before,
        snapshot,
        entry_path,
        expected_size,
    )
    if _sha256_bytes(parser.read_file(entry_path)) != expected_sha256:
        raise RuntimeError(
            "Staged RPF verification failed: replacement bytes do not match "
            "the source file"
        )
    return replacement


def _restore_archive_backup(
    backup_path: Path,
    archive_path: Path,
    expected_sha256: str,
) -> None:
    rollback = _temporary_copy(
        backup_path,
        directory=archive_path.parent,
        prefix=".gtaiv_toolkit_rpf_rollback_",
        suffix=archive_path.suffix,
    )
    try:
        os.replace(rollback, archive_path)
    finally:
        rollback.unlink(missing_ok=True)
    if _sha256_path(archive_path) != expected_sha256:
        raise RuntimeError(
            f"RPF rollback verification failed; backup retained at {backup_path}"
        )


def _replace_rpf_entry_transactional_locked(
    archive: Path,
    executable: Path,
    normalized_entry: str,
    replacement: Path,
    *,
    parser_factory: ParserFactory | None,
    replace_file: ReplaceFile,
) -> RPFReplacementResult:
    """Replace one RPF entry while the caller holds the installation lock."""
    staged_archive: Path | None = None
    staged_replacement: Path | None = None
    pending_backup: Path | None = None
    backup_path: Path | None = None

    original_parser = _open_parser(archive, executable, parser_factory)
    original_snapshot = _snapshot_from_parser(archive, original_parser)
    previous_entry = original_snapshot.entry(normalized_entry)
    original_sha256 = _sha256_path(archive)

    try:
        staged_archive = _temporary_copy(
            archive,
            directory=archive.parent,
            prefix=".gtaiv_toolkit_rpf_",
            suffix=f".staged{archive.suffix}",
        )
        staged_replacement = _temporary_copy(
            replacement,
            directory=archive.parent,
            prefix=".gtaiv_toolkit_rpf_payload_",
            suffix=replacement.suffix or ".bin",
        )
        expected_size = staged_replacement.stat().st_size
        expected_sha256 = _sha256_path(staged_replacement)

        staged_parser = _open_parser(
            staged_archive,
            executable,
            parser_factory,
        )
        staged_parser.add_file(str(staged_replacement), normalized_entry)
        staged_entry = _verify_replacement(
            staged_archive,
            executable,
            parser_factory,
            original_snapshot,
            normalized_entry,
            expected_size,
            expected_sha256,
        )

        backup_path = _unique_backup_path(archive)
        pending_backup = _temporary_copy(
            archive,
            directory=archive.parent,
            prefix=".gtaiv_toolkit_rpf_backup_",
            suffix=".tmp",
        )
        os.replace(pending_backup, backup_path)
        pending_backup = None
        if _sha256_path(backup_path) != original_sha256:
            raise RuntimeError("RPF backup verification failed")

        try:
            replace_file(str(staged_archive), str(archive))
            staged_archive = None
            committed_entry = _verify_replacement(
                archive,
                executable,
                parser_factory,
                original_snapshot,
                normalized_entry,
                expected_size,
                expected_sha256,
            )
            if committed_entry != staged_entry:
                raise RuntimeError(
                    "Committed RPF verification failed: entry metadata differs "
                    "from the verified staged archive"
                )
        except Exception:
            current_sha256 = _sha256_path(archive) if archive.is_file() else None
            if current_sha256 != original_sha256:
                _restore_archive_backup(
                    backup_path,
                    archive,
                    original_sha256,
                )
            backup_path.unlink(missing_ok=True)
            backup_path = None
            raise

        prune_archive_backups(archive)

        return RPFReplacementResult(
            archive_path=archive,
            backup_path=backup_path.resolve(),
            entry_path=normalized_entry,
            previous_size=previous_entry.size,
            replacement_size=expected_size,
            previous_offset=previous_entry.offset,
            final_offset=committed_entry.offset,
        )
    except Exception:
        if backup_path is not None:
            current_sha256 = _sha256_path(archive) if archive.is_file() else None
            if current_sha256 == original_sha256:
                backup_path.unlink(missing_ok=True)
                backup_path = None
        raise
    finally:
        for temporary in (
            staged_archive,
            staged_replacement,
            pending_backup,
        ):
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def replace_rpf_entry_transactional(
    archive_path: str | os.PathLike[str],
    gtaiv_exe_path: str | os.PathLike[str],
    entry_path: str,
    replacement_path: str | os.PathLike[str],
    *,
    parser_factory: ParserFactory | None = None,
    replace_file: ReplaceFile | None = None,
) -> RPFReplacementResult:
    """Replace one existing RPF entry through a verified, rollback-safe copy."""
    archive = _validated_archive_path(archive_path)
    executable = _validated_executable_path(gtaiv_exe_path)
    replacement = _validated_replacement_path(replacement_path, archive)
    normalized_entry = normalize_entry_path(entry_path)
    commit_file = os.replace if replace_file is None else replace_file

    with installation_lock(
        executable.parent,
        operation=f"RPF entry replacement in {archive.name}",
        scope="rpf",
    ):
        return _replace_rpf_entry_transactional_locked(
            archive,
            executable,
            normalized_entry,
            replacement,
            parser_factory=parser_factory,
            replace_file=commit_file,
        )
