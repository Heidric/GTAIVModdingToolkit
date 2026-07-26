"""Transactional texture replacement for WTD entries stored inside RPF archives."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from core.installation_lock import installation_lock
from core.rpf_archive import (
    ParserFactory,
    ReplaceFile,
    _open_parser,
    _replace_rpf_entry_transactional_locked,
    _sha256_path,
    _snapshot_from_parser,
    _validated_archive_path,
    _validated_executable_path,
    normalize_entry_path,
)
from core.wtd_archive import (
    WTDTextureEntry,
    WTDTextureReplacementResult,
    replace_wtd_texture_from_image,
)


WTDReplacer = Callable[..., WTDTextureReplacementResult]


@dataclass(frozen=True)
class RPFWTDTextureReplacementResult:
    """Committed RPF and WTD metadata for one texture replacement."""

    archive_path: Path
    backup_path: Path
    entry_path: str
    replacement_image_path: Path
    texture: WTDTextureEntry
    previous_entry_size: int
    replacement_entry_size: int
    previous_offset: int
    final_offset: int
    wtd_sha256: str
    virtual_sha256: str

    @property
    def relocated(self) -> bool:
        return self.previous_offset != self.final_offset


def _validated_image_path(path: str | os.PathLike[str]) -> Path:
    image = Path(path).expanduser().resolve()
    if not image.is_file():
        raise FileNotFoundError(f"Texture replacement image not found: {image}")
    return image


def _validate_wtd_entry_path(entry_path: str) -> str:
    normalized = normalize_entry_path(entry_path)
    if PurePosixPath(normalized).suffix.casefold() != ".wtd":
        raise ValueError("entry_path must identify a .wtd file inside the RPF archive")
    return normalized


def _write_bytes_verified(path: Path, payload: bytes) -> None:
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    if path.stat().st_size != len(payload):
        raise OSError(f"Temporary WTD extraction size verification failed: {path}")


def replace_rpf_wtd_texture_from_image_transactional(
    archive_path: str | os.PathLike[str],
    gtaiv_exe_path: str | os.PathLike[str],
    entry_path: str,
    texture_index: int,
    image_path: str | os.PathLike[str],
    *,
    quality: float = 0.9,
    parser_factory: ParserFactory | None = None,
    replace_file: ReplaceFile | None = None,
    wtd_replacer: WTDReplacer | None = None,
) -> RPFWTDTextureReplacementResult:
    """Replace one texture in an RPF-contained WTD under one rollback-safe lock."""
    archive = _validated_archive_path(archive_path)
    executable = _validated_executable_path(gtaiv_exe_path)
    normalized_entry = _validate_wtd_entry_path(entry_path)
    image = _validated_image_path(image_path)
    commit_file = os.replace if replace_file is None else replace_file
    replacer = replace_wtd_texture_from_image if wtd_replacer is None else wtd_replacer

    with installation_lock(
        executable.parent,
        operation=f"WTD texture replacement in {archive.name}",
        scope="rpf",
    ):
        parser = _open_parser(archive, executable, parser_factory)
        snapshot = _snapshot_from_parser(archive, parser)
        source_entry = snapshot.entry(normalized_entry)
        source_payload = parser.read_file(normalized_entry)
        if len(source_payload) != source_entry.size:
            raise RuntimeError(
                "RPF WTD extraction verification failed: entry size does not match "
                "the extracted payload"
            )

        with tempfile.TemporaryDirectory(
            prefix=".gtaiv_toolkit_rpf_wtd_",
            dir=archive.parent,
        ) as workspace_name:
            workspace = Path(workspace_name)
            source_wtd = workspace / "source.wtd"
            patched_wtd = workspace / "patched.wtd"
            _write_bytes_verified(source_wtd, source_payload)

            wtd_result = replacer(
                source_wtd,
                texture_index,
                image,
                patched_wtd,
                quality=quality,
                overwrite=False,
            )
            if wtd_result.source_path != source_wtd.resolve():
                raise RuntimeError(
                    "WTD replacement verification failed: replacer returned an "
                    "unexpected source path"
                )
            if wtd_result.replacement_image_path != image:
                raise RuntimeError(
                    "WTD replacement verification failed: replacer returned an "
                    "unexpected image path"
                )
            if wtd_result.texture.index != texture_index:
                raise RuntimeError(
                    "WTD replacement verification failed: replacer returned an "
                    "unexpected texture index"
                )
            resolved_output = Path(wtd_result.output_path).expanduser().resolve()
            if resolved_output != patched_wtd.resolve():
                raise RuntimeError(
                    "WTD replacement verification failed: replacer returned an "
                    "unexpected output path"
                )
            if not patched_wtd.is_file():
                raise RuntimeError(
                    "WTD replacement verification failed: patched WTD was not created"
                )
            if patched_wtd.stat().st_size != wtd_result.output_size:
                raise RuntimeError(
                    "WTD replacement verification failed: output size does not match "
                    "the reported result"
                )
            if _sha256_path(patched_wtd) != wtd_result.output_sha256:
                raise RuntimeError(
                    "WTD replacement verification failed: output hash does not match "
                    "the reported result"
                )

            rpf_result = _replace_rpf_entry_transactional_locked(
                archive,
                executable,
                normalized_entry,
                patched_wtd,
                parser_factory=parser_factory,
                replace_file=commit_file,
            )

        return RPFWTDTextureReplacementResult(
            archive_path=rpf_result.archive_path,
            backup_path=rpf_result.backup_path,
            entry_path=rpf_result.entry_path,
            replacement_image_path=image,
            texture=wtd_result.texture,
            previous_entry_size=rpf_result.previous_size,
            replacement_entry_size=rpf_result.replacement_size,
            previous_offset=rpf_result.previous_offset,
            final_offset=rpf_result.final_offset,
            wtd_sha256=wtd_result.output_sha256,
            virtual_sha256=wtd_result.virtual_sha256,
        )
