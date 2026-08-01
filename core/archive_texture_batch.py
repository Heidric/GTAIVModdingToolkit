"""Batch texture replacement for WTD/WDR entries in one GTA IV archive."""

from __future__ import annotations

import os
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from core.archive_backups import prune_archive_backups
from core.installation_lock import installation_lock
from core.rpf_archive import (
    ParserFactory,
    ReplaceFile,
    _open_parser,
    _restore_archive_backup,
    _sha256_bytes,
    _sha256_path,
    _snapshot_from_parser,
    _temporary_copy,
    _unique_backup_path,
    _validate_replacement_snapshot,
    _validated_archive_path,
    _validated_executable_path,
    normalize_entry_path,
)
from core.wdr_archive import replace_wdr_texture_from_image
from core.wtd_archive import replace_wtd_texture_from_image


@dataclass(frozen=True)
class TextureReplacementRequest:
    """One texture replacement queued against an entry in the open archive."""

    entry_path: str
    texture_index: int
    texture_name: str
    image_path: Path

    @property
    def key(self) -> tuple[str, int]:
        return (self.entry_path.casefold(), self.texture_index)


@dataclass(frozen=True)
class BatchEntryResult:
    entry_path: str
    previous_size: int
    replacement_size: int
    previous_offset: int
    final_offset: int
    texture_count: int

    @property
    def relocated(self) -> bool:
        return self.previous_offset != self.final_offset


@dataclass(frozen=True)
class ArchiveTextureBatchResult:
    archive_path: Path
    backup_path: Path
    replacements: tuple[TextureReplacementRequest, ...]
    entries: tuple[BatchEntryResult, ...]

    @property
    def relocated_entries(self) -> tuple[str, ...]:
        return tuple(entry.entry_path for entry in self.entries if entry.relocated)


ResourceReplacer = Callable[..., object]


def _normalize_requests(
    replacements: Iterable[TextureReplacementRequest],
) -> tuple[TextureReplacementRequest, ...]:
    normalized: list[TextureReplacementRequest] = []
    seen: set[tuple[str, int]] = set()
    for raw in replacements:
        if not isinstance(raw, TextureReplacementRequest):
            raise TypeError("replacements must contain TextureReplacementRequest values")
        entry_path = normalize_entry_path(raw.entry_path)
        suffix = PurePosixPath(entry_path).suffix.casefold()
        if suffix not in {".wtd", ".wdr"}:
            raise ValueError(
                "batch texture replacement supports only .wtd and .wdr entries"
            )
        if isinstance(raw.texture_index, bool) or not isinstance(raw.texture_index, int):
            raise TypeError("texture index must be an integer")
        if raw.texture_index < 0:
            raise ValueError("texture index must not be negative")
        texture_name = raw.texture_name.strip()
        if not texture_name:
            raise ValueError("texture name must not be empty")
        image = Path(raw.image_path).expanduser().resolve()
        if not image.is_file():
            raise FileNotFoundError(f"Texture replacement image not found: {image}")
        request = TextureReplacementRequest(
            entry_path=entry_path,
            texture_index=raw.texture_index,
            texture_name=texture_name,
            image_path=image,
        )
        if request.key in seen:
            raise ValueError(
                "duplicate queued texture replacement for "
                f"{entry_path} texture #{raw.texture_index}"
            )
        seen.add(request.key)
        normalized.append(request)
    if not normalized:
        raise ValueError("at least one texture replacement is required")
    return tuple(normalized)


def _group_requests(
    replacements: tuple[TextureReplacementRequest, ...],
) -> tuple[tuple[str, tuple[TextureReplacementRequest, ...]], ...]:
    groups: OrderedDict[str, list[TextureReplacementRequest]] = OrderedDict()
    display_paths: dict[str, str] = {}
    for request in replacements:
        key = request.entry_path.casefold()
        groups.setdefault(key, []).append(request)
        display_paths.setdefault(key, request.entry_path)
    return tuple(
        (display_paths[key], tuple(values)) for key, values in groups.items()
    )


def _write_payload(path: Path, payload: bytes) -> None:
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    if path.stat().st_size != len(payload):
        raise OSError(f"Temporary resource size verification failed: {path}")


def _patch_resource_group(
    source_path: Path,
    requests: tuple[TextureReplacementRequest, ...],
    workspace: Path,
    *,
    quality: float,
    wtd_replacer: ResourceReplacer,
    wdr_replacer: ResourceReplacer,
) -> Path:
    suffix = source_path.suffix.casefold()
    replacer = wdr_replacer if suffix == ".wdr" else wtd_replacer
    current = source_path
    for sequence, request in enumerate(requests, start=1):
        output = workspace / f"{source_path.stem}-replacement-{sequence:03d}{suffix}"
        result = replacer(
            current,
            request.texture_index,
            request.image_path,
            output,
            quality=quality,
            overwrite=False,
        )
        result_texture = getattr(result, "texture", None)
        if result_texture is None or result_texture.index != request.texture_index:
            raise RuntimeError(
                "resource replacement returned an unexpected texture index"
            )
        if result_texture.name != request.texture_name:
            raise RuntimeError(
                "queued texture metadata is stale: expected "
                f"{request.texture_name!r}, got {result_texture.name!r}"
            )
        if Path(result.output_path).expanduser().resolve() != output.resolve():
            raise RuntimeError("resource replacement returned an unexpected output path")
        if not output.is_file() or output.stat().st_size != result.output_size:
            raise RuntimeError("resource replacement output verification failed")
        if _sha256_path(output) != result.output_sha256:
            raise RuntimeError("resource replacement output hash verification failed")
        current = output
    return current


def _verify_archive_against_stage(
    archive: Path,
    executable: Path,
    parser_factory: ParserFactory | None,
    staged_snapshot,
    expected_hashes: dict[str, str],
) -> None:
    parser = _open_parser(archive, executable, parser_factory)
    snapshot = _snapshot_from_parser(archive, parser)
    if snapshot.entries != staged_snapshot.entries:
        raise RuntimeError(
            "Committed archive verification failed: entry metadata differs from stage"
        )
    for entry_path, expected_hash in expected_hashes.items():
        if _sha256_bytes(parser.read_file(entry_path)) != expected_hash:
            raise RuntimeError(
                "Committed archive verification failed: replacement bytes differ for "
                f"{entry_path}"
            )


def replace_archive_textures_transactional(
    archive_path: str | os.PathLike[str],
    gtaiv_exe_path: str | os.PathLike[str],
    replacements: Iterable[TextureReplacementRequest],
    *,
    quality: float = 0.9,
    rolling_backup_limit: int | None = None,
    parser_factory: ParserFactory | None = None,
    replace_file: ReplaceFile | None = None,
    wtd_replacer: ResourceReplacer | None = None,
    wdr_replacer: ResourceReplacer | None = None,
) -> ArchiveTextureBatchResult:
    """Apply all queued replacements with one archive backup and one commit."""
    if not 0.0 <= quality <= 1.0:
        raise ValueError("quality must be between 0.0 and 1.0")

    archive = _validated_archive_path(archive_path)
    executable = _validated_executable_path(gtaiv_exe_path)
    requests = _normalize_requests(replacements)
    groups = _group_requests(requests)
    commit_file = os.replace if replace_file is None else replace_file
    replace_wtd = replace_wtd_texture_from_image if wtd_replacer is None else wtd_replacer
    replace_wdr = replace_wdr_texture_from_image if wdr_replacer is None else wdr_replacer

    with installation_lock(
        executable.parent,
        operation=f"batch texture replacement in {archive.name}",
        scope="rpf",
    ):
        staged_archive: Path | None = None
        pending_backup: Path | None = None
        backup_path: Path | None = None
        original_sha256 = _sha256_path(archive)
        original_parser = _open_parser(archive, executable, parser_factory)
        original_snapshot = _snapshot_from_parser(archive, original_parser)
        original_entries = {
            entry.path: entry for entry in original_snapshot.entries
        }
        expected_hashes: dict[str, str] = {}
        entry_results: list[BatchEntryResult] = []

        try:
            staged_archive = _temporary_copy(
                archive,
                directory=archive.parent,
                prefix=".gtaiv_toolkit_batch_",
                suffix=f".staged{archive.suffix}",
            )
            with tempfile.TemporaryDirectory(
                prefix=".gtaiv_toolkit_texture_batch_",
                dir=archive.parent,
            ) as workspace_name:
                workspace = Path(workspace_name)
                for group_index, (entry_path, group) in enumerate(groups, start=1):
                    staged_parser = _open_parser(
                        staged_archive, executable, parser_factory
                    )
                    before_snapshot = _snapshot_from_parser(
                        staged_archive, staged_parser
                    )
                    source_entry = before_snapshot.entry(entry_path)
                    source_payload = staged_parser.read_file(entry_path)
                    if len(source_payload) != source_entry.size:
                        raise RuntimeError(
                            "Archive resource extraction verification failed for "
                            f"{entry_path}"
                        )

                    suffix = PurePosixPath(entry_path).suffix.casefold()
                    source_resource = workspace / f"entry-{group_index:03d}-source{suffix}"
                    _write_payload(source_resource, source_payload)
                    patched_resource = _patch_resource_group(
                        source_resource,
                        group,
                        workspace,
                        quality=quality,
                        wtd_replacer=replace_wtd,
                        wdr_replacer=replace_wdr,
                    )
                    expected_size = patched_resource.stat().st_size
                    expected_hash = _sha256_path(patched_resource)

                    staged_parser = _open_parser(
                        staged_archive, executable, parser_factory
                    )
                    staged_parser.add_file(str(patched_resource), entry_path)
                    verified_parser = _open_parser(
                        staged_archive, executable, parser_factory
                    )
                    after_snapshot = _snapshot_from_parser(
                        staged_archive, verified_parser
                    )
                    final_entry = _validate_replacement_snapshot(
                        before_snapshot,
                        after_snapshot,
                        entry_path,
                        expected_size,
                    )
                    if _sha256_bytes(verified_parser.read_file(entry_path)) != expected_hash:
                        raise RuntimeError(
                            "Staged archive verification failed for " f"{entry_path}"
                        )
                    expected_hashes[entry_path] = expected_hash
                    original_entry = original_entries[entry_path]
                    entry_results.append(
                        BatchEntryResult(
                            entry_path=entry_path,
                            previous_size=original_entry.size,
                            replacement_size=expected_size,
                            previous_offset=original_entry.offset,
                            final_offset=final_entry.offset,
                            texture_count=len(group),
                        )
                    )

            staged_parser = _open_parser(staged_archive, executable, parser_factory)
            staged_snapshot = _snapshot_from_parser(staged_archive, staged_parser)
            for entry_path, expected_hash in expected_hashes.items():
                if _sha256_bytes(staged_parser.read_file(entry_path)) != expected_hash:
                    raise RuntimeError(
                        "Final staged archive verification failed for " f"{entry_path}"
                    )

            backup_path = _unique_backup_path(archive)
            pending_backup = _temporary_copy(
                archive,
                directory=archive.parent,
                prefix=".gtaiv_toolkit_batch_backup_",
                suffix=".tmp",
            )
            os.replace(pending_backup, backup_path)
            pending_backup = None
            if _sha256_path(backup_path) != original_sha256:
                raise RuntimeError("Archive backup verification failed")

            try:
                commit_file(str(staged_archive), str(archive))
                staged_archive = None
                _verify_archive_against_stage(
                    archive,
                    executable,
                    parser_factory,
                    staged_snapshot,
                    expected_hashes,
                )
            except Exception:
                current_sha256 = _sha256_path(archive) if archive.is_file() else None
                if current_sha256 != original_sha256:
                    _restore_archive_backup(backup_path, archive, original_sha256)
                backup_path.unlink(missing_ok=True)
                backup_path = None
                raise

            prune_archive_backups(archive, rolling_backup_limit)
            return ArchiveTextureBatchResult(
                archive_path=archive,
                backup_path=backup_path.resolve(),
                replacements=requests,
                entries=tuple(entry_results),
            )
        except Exception:
            if backup_path is not None:
                current_sha256 = _sha256_path(archive) if archive.is_file() else None
                if current_sha256 == original_sha256:
                    backup_path.unlink(missing_ok=True)
            raise
        finally:
            for temporary in (staged_archive, pending_backup):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
