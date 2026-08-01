"""Transactional replacement for embedded textures in archive-contained WDR files."""

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
from core.wdr_archive import (
    WDRTextureReplacementResult,
    replace_wdr_texture_from_image,
)
from core.wtd_archive import WTDTextureEntry


WDRReplacer = Callable[..., WDRTextureReplacementResult]


@dataclass(frozen=True)
class RPFWDRTextureReplacementResult:
    """Committed archive and WDR metadata for one embedded texture replacement."""

    archive_path: Path
    backup_path: Path
    entry_path: str
    replacement_image_path: Path
    texture: WTDTextureEntry
    previous_entry_size: int
    replacement_entry_size: int
    previous_offset: int
    final_offset: int
    wdr_sha256: str
    virtual_sha256: str

    @property
    def relocated(self) -> bool:
        return self.previous_offset != self.final_offset


def _validated_image_path(path: str | os.PathLike[str]) -> Path:
    image = Path(path).expanduser().resolve()
    if not image.is_file():
        raise FileNotFoundError(f"Texture replacement image not found: {image}")
    return image


def _validate_wdr_entry_path(entry_path: str) -> str:
    normalized = normalize_entry_path(entry_path)
    if PurePosixPath(normalized).suffix.casefold() != ".wdr":
        raise ValueError("entry_path must identify a .wdr file inside the archive")
    return normalized


def _write_bytes_verified(path: Path, payload: bytes) -> None:
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    if path.stat().st_size != len(payload):
        raise OSError(f"Temporary WDR extraction size verification failed: {path}")


def replace_rpf_wdr_texture_from_image_transactional(
    archive_path: str | os.PathLike[str],
    gtaiv_exe_path: str | os.PathLike[str],
    entry_path: str,
    texture_index: int,
    image_path: str | os.PathLike[str],
    *,
    quality: float = 0.9,
    parser_factory: ParserFactory | None = None,
    replace_file: ReplaceFile | None = None,
    wdr_replacer: WDRReplacer | None = None,
) -> RPFWDRTextureReplacementResult:
    """Replace one embedded WDR texture under one rollback-safe archive lock."""
    archive = _validated_archive_path(archive_path)
    executable = _validated_executable_path(gtaiv_exe_path)
    normalized_entry = _validate_wdr_entry_path(entry_path)
    image = _validated_image_path(image_path)
    commit_file = os.replace if replace_file is None else replace_file
    replacer = replace_wdr_texture_from_image if wdr_replacer is None else wdr_replacer

    with installation_lock(
        executable.parent,
        operation=f"WDR texture replacement in {archive.name}",
        scope="rpf",
    ):
        parser = _open_parser(archive, executable, parser_factory)
        snapshot = _snapshot_from_parser(archive, parser)
        source_entry = snapshot.entry(normalized_entry)
        source_payload = parser.read_file(normalized_entry)
        if len(source_payload) != source_entry.size:
            raise RuntimeError(
                "WDR extraction verification failed: entry size does not match "
                "the extracted payload"
            )

        with tempfile.TemporaryDirectory(
            prefix=".gtaiv_toolkit_rpf_wdr_",
            dir=archive.parent,
        ) as workspace_name:
            workspace = Path(workspace_name)
            source_wdr = workspace / "source.wdr"
            patched_wdr = workspace / "patched.wdr"
            _write_bytes_verified(source_wdr, source_payload)

            wdr_result = replacer(
                source_wdr,
                texture_index,
                image,
                patched_wdr,
                quality=quality,
                overwrite=False,
            )
            if wdr_result.source_path != source_wdr.resolve():
                raise RuntimeError(
                    "WDR replacement verification failed: replacer returned an "
                    "unexpected source path"
                )
            if wdr_result.replacement_image_path != image:
                raise RuntimeError(
                    "WDR replacement verification failed: replacer returned an "
                    "unexpected image path"
                )
            if wdr_result.texture.index != texture_index:
                raise RuntimeError(
                    "WDR replacement verification failed: replacer returned an "
                    "unexpected texture index"
                )
            resolved_output = Path(wdr_result.output_path).expanduser().resolve()
            if resolved_output != patched_wdr.resolve():
                raise RuntimeError(
                    "WDR replacement verification failed: replacer returned an "
                    "unexpected output path"
                )
            if not patched_wdr.is_file():
                raise RuntimeError(
                    "WDR replacement verification failed: patched WDR was not created"
                )
            if patched_wdr.stat().st_size != wdr_result.output_size:
                raise RuntimeError(
                    "WDR replacement verification failed: output size does not match "
                    "the reported result"
                )
            if _sha256_path(patched_wdr) != wdr_result.output_sha256:
                raise RuntimeError(
                    "WDR replacement verification failed: output hash does not match "
                    "the reported result"
                )

            archive_result = _replace_rpf_entry_transactional_locked(
                archive,
                executable,
                normalized_entry,
                patched_wdr,
                parser_factory=parser_factory,
                replace_file=commit_file,
            )

        return RPFWDRTextureReplacementResult(
            archive_path=archive_result.archive_path,
            backup_path=archive_result.backup_path,
            entry_path=archive_result.entry_path,
            replacement_image_path=image,
            texture=wdr_result.texture,
            previous_entry_size=archive_result.previous_size,
            replacement_entry_size=archive_result.replacement_size,
            previous_offset=archive_result.previous_offset,
            final_offset=archive_result.final_offset,
            wdr_sha256=wdr_result.output_sha256,
            virtual_sha256=wdr_result.virtual_sha256,
        )
