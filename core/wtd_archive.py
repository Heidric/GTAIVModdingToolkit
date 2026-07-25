"""Application-facing WTD texture inspection, export, and preview operations."""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from core.radio_logo.wtd import (
    WTDArchive,
    WTDParseError,
    WTDTexture,
    read_wtd,
    texture_to_dds,
)


class WTDTextureNotFoundError(LookupError):
    """Raised when a requested texture index is absent from a WTD archive."""


class WTDTexturePreviewError(RuntimeError):
    """Raised when an extractable texture cannot be decoded for preview."""


@dataclass(frozen=True)
class WTDTextureEntry:
    """Immutable metadata for one texture dictionary entry."""

    index: int
    hash: int
    name: str
    raw_name: str
    width: int
    height: int
    format_code: int
    format_name: str
    stride: int
    texture_type: int
    mip_count: int
    data_size: int | None
    extractable: bool


@dataclass(frozen=True)
class WTDArchiveSnapshot:
    """Immutable WTD metadata suitable for a generic browser UI."""

    path: Path
    resource_type: int
    flags: int
    virtual_size: int
    physical_size: int
    textures: tuple[WTDTextureEntry, ...]

    def texture(self, index: int) -> WTDTextureEntry:
        if not isinstance(index, int):
            raise TypeError("texture index must be an integer")
        for texture in self.textures:
            if texture.index == index:
                return texture
        raise WTDTextureNotFoundError(f"WTD texture index not found: {index}")

    def textures_named(self, name: str) -> tuple[WTDTextureEntry, ...]:
        if not isinstance(name, str):
            raise TypeError("texture name must be a string")
        normalized = name.strip().casefold()
        if not normalized:
            raise ValueError("texture name must not be empty")
        return tuple(
            texture
            for texture in self.textures
            if texture.name.casefold() == normalized
        )


@dataclass(frozen=True)
class WTDTexturePreview:
    """PNG preview bytes and the resulting preview dimensions."""

    texture: WTDTextureEntry
    png_data: bytes
    width: int
    height: int


def _validated_wtd_path(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"WTD file not found: {candidate}")
    return candidate


def _entry_from_texture(texture: WTDTexture) -> WTDTextureEntry:
    return WTDTextureEntry(
        index=texture.index,
        hash=texture.hash,
        name=texture.name,
        raw_name=texture.raw_name,
        width=texture.width,
        height=texture.height,
        format_code=texture.format_code,
        format_name=texture.format_name,
        stride=texture.stride,
        texture_type=texture.texture_type,
        mip_count=texture.mip_count,
        data_size=texture.data_size,
        extractable=texture.extractable,
    )


def _snapshot_from_archive(archive: WTDArchive) -> WTDArchiveSnapshot:
    return WTDArchiveSnapshot(
        path=archive.path,
        resource_type=archive.header.resource_type,
        flags=archive.header.flags,
        virtual_size=archive.header.virtual_size,
        physical_size=archive.header.physical_size,
        textures=tuple(_entry_from_texture(texture) for texture in archive.textures),
    )


def _select_texture(archive: WTDArchive, index: int) -> WTDTexture:
    if not isinstance(index, int):
        raise TypeError("texture index must be an integer")
    for texture in archive.textures:
        if texture.index == index:
            return texture
    raise WTDTextureNotFoundError(f"WTD texture index not found: {index}")


def inspect_wtd_archive(path: str | os.PathLike[str]) -> WTDArchiveSnapshot:
    """Parse a WTD and return metadata without exposing texture payload bytes."""
    archive = read_wtd(_validated_wtd_path(path))
    return _snapshot_from_archive(archive)


def _write_bytes_atomic(
    destination: Path,
    payload: bytes,
    *,
    overwrite: bool,
) -> Path:
    if destination.exists() and destination.is_dir():
        raise IsADirectoryError(f"Destination is a directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if not overwrite:
        try:
            with destination.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            raise
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def export_wtd_texture(
    path: str | os.PathLike[str],
    texture_index: int,
    destination_path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> Path:
    """Export one supported WTD texture as a DDS file."""
    archive = read_wtd(_validated_wtd_path(path))
    texture = _select_texture(archive, texture_index)
    payload = texture_to_dds(texture)
    destination = Path(destination_path).expanduser().resolve()
    result = _write_bytes_atomic(destination, payload, overwrite=overwrite)
    if result.stat().st_size != len(payload):
        raise OSError(f"Exported DDS size verification failed: {result}")
    return result


def render_wtd_texture_preview(
    path: str | os.PathLike[str],
    texture_index: int,
    *,
    max_dimension: int = 512,
) -> WTDTexturePreview:
    """Decode one supported texture and return a bounded PNG preview."""
    if not isinstance(max_dimension, int):
        raise TypeError("max_dimension must be an integer")
    if max_dimension <= 0:
        raise ValueError("max_dimension must be greater than zero")

    archive = read_wtd(_validated_wtd_path(path))
    texture = _select_texture(archive, texture_index)
    dds = texture_to_dds(texture)

    try:
        with Image.open(io.BytesIO(dds)) as source:
            source.load()
            preview = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise WTDTexturePreviewError(
            f"Texture {texture.name!r} cannot be decoded for preview: {exc}"
        ) from exc

    preview.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    preview.save(output, format="PNG")
    entry = _entry_from_texture(texture)
    return WTDTexturePreview(
        texture=entry,
        png_data=output.getvalue(),
        width=preview.width,
        height=preview.height,
    )
