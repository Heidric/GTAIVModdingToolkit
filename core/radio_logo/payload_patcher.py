"""Patch GTA IV WTD texture payloads without rebuilding RSC5 metadata."""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

from .texture_dictionary import (
    DictionaryComparison,
    TexfuryUnavailableError,
    TextureDictionaryError,
    TextureDictionaryValidationError,
    compare_wtd_files,
)
from .wtd import MAX_DECOMPRESSED_SIZE, RSC5_MAGIC, WTDArchive, WTDTexture, read_wtd


_FORMAT_TO_TEXFURY = {
    # The color radio texture is an opaque RGB image on black. GTA IV's HUD
    # shader masks/blends it separately; encoding the DXT1 payload as BC1A
    # can make the selected station disappear.
    "DXT1": "BC1",
    "DXT5": "BC3",
    "A8R8G8B8": "A8R8G8B8",
}


@dataclass(frozen=True)
class PayloadPatchResult:
    """Result of replacing one or more fixed-size physical texture payloads."""

    source_path: Path
    output_path: Path
    replaced_textures: tuple[str, ...]
    texture_count: int
    output_size: int
    output_sha256: str
    virtual_sha256: str
    comparison: DictionaryComparison


@dataclass(frozen=True)
class _ResourceSections:
    header: bytes
    virtual: bytes
    physical: bytes


def _load_texfury_encoder() -> SimpleNamespace:
    try:
        from texfury import BCFormat, Texture
    except (ImportError, OSError) as exc:
        raise TexfuryUnavailableError(
            "texfury 1.6.2 is required for GTA IV texture encoding; "
            "install runtime dependencies from requirements.txt"
        ) from exc
    return SimpleNamespace(BCFormat=BCFormat, Texture=Texture)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_resource_size(flags: int, base_shift: int, exponent_shift: int) -> int:
    base = (flags >> base_shift) & 0x7FF
    exponent = (flags >> exponent_shift) & 0xF
    return base << (exponent + 8)


def _read_resource_sections(path: Path) -> _ResourceSections:
    file_data = path.read_bytes()
    if len(file_data) < 12:
        raise TextureDictionaryError(f"WTD header is truncated: {path}")

    magic, _resource_type, flags = struct.unpack_from("<III", file_data, 0)
    if magic != RSC5_MAGIC:
        raise TextureDictionaryError(f"invalid RSC5 magic in {path}")

    virtual_size = _decode_resource_size(flags, 0, 11)
    physical_size = _decode_resource_size(flags, 15, 26)
    expected_size = virtual_size + physical_size
    if virtual_size < 32 or physical_size <= 0:
        raise TextureDictionaryError(
            f"invalid RSC5 sizes in {path}: virtual={virtual_size}, "
            f"physical={physical_size}"
        )
    if expected_size > MAX_DECOMPRESSED_SIZE:
        raise TextureDictionaryError(
            f"refusing to decompress {expected_size} bytes from {path}"
        )

    decompressor = zlib.decompressobj()
    try:
        resource = decompressor.decompress(file_data[12:], expected_size + 1)
        resource += decompressor.flush()
    except zlib.error as exc:
        raise TextureDictionaryError(
            f"cannot decompress RSC5 payload from {path}: {exc}"
        ) from exc

    if not decompressor.eof:
        raise TextureDictionaryError(f"compressed RSC5 payload is truncated: {path}")
    if decompressor.unused_data:
        raise TextureDictionaryError(f"unexpected data follows RSC5 payload: {path}")
    if len(resource) != expected_size:
        raise TextureDictionaryError(
            f"decompressed size mismatch in {path}: expected {expected_size}, "
            f"got {len(resource)}"
        )

    return _ResourceSections(
        header=file_data[:12],
        virtual=resource[:virtual_size],
        physical=resource[virtual_size:],
    )


def _normalise_paths(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    overwrite: bool,
) -> tuple[Path, Path]:
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()

    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path.suffix.casefold() != ".wtd":
        raise ValueError(f"source must be a .wtd file: {source_path}")
    if output_path.suffix.casefold() != ".wtd":
        raise ValueError(f"output must be a .wtd file: {output_path}")
    if source_path == output_path:
        raise ValueError("source and output WTD paths must be different")
    if output_path.exists() and not overwrite:
        raise FileExistsError(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return source_path, output_path


def _normalise_replacements(
    replacements: Mapping[str, str | os.PathLike[str]],
) -> dict[str, Path]:
    if not replacements:
        raise ValueError("at least one texture replacement is required")

    normalised: dict[str, Path] = {}
    display_names: dict[str, str] = {}
    for raw_name, raw_path in replacements.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("replacement texture names must not be empty")
        folded = raw_name.strip().casefold()
        if folded in normalised:
            raise ValueError(
                "duplicate replacement texture names differ only by case: "
                f"{display_names[folded]!r}, {raw_name!r}"
            )
        image_path = Path(raw_path).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        normalised[folded] = image_path
        display_names[folded] = raw_name
    return normalised


def _find_target_textures(
    archive: WTDArchive,
    replacements: Mapping[str, Path],
) -> dict[str, WTDTexture]:
    by_name: dict[str, WTDTexture] = {}
    for texture in archive.textures:
        folded = texture.name.casefold()
        if folded in by_name:
            raise TextureDictionaryValidationError(
                f"duplicate texture name in {archive.path.name}: {texture.name!r}"
            )
        by_name[folded] = texture

    missing = sorted(set(replacements) - set(by_name))
    if missing:
        available = ", ".join(texture.name for texture in archive.textures)
        raise KeyError(
            f"texture(s) not present in {archive.path.name}: {missing}; "
            f"available: {available}"
        )

    targets = {name: by_name[name] for name in replacements}
    ranges: list[tuple[int, int, str]] = []
    for target in targets.values():
        if target.data_offset is None or target.data_size is None or target.data is None:
            raise TextureDictionaryError(
                f"texture {target.name!r} has no extractable physical payload"
            )
        if target.format_name not in _FORMAT_TO_TEXFURY:
            raise TextureDictionaryError(
                f"cannot encode replacement for {target.name!r} in "
                f"{target.format_name}; supported formats are DXT1, DXT5 and "
                "A8R8G8B8"
            )
        start = target.data_offset
        end = start + target.data_size
        ranges.append((start, end, target.name))

    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if previous[1] > current[0]:
            raise TextureDictionaryValidationError(
                f"texture payload ranges overlap: {previous[2]!r}, {current[2]!r}"
            )
    return targets


def _mip_min_size(width: int, height: int, mip_count: int) -> int:
    if mip_count <= 1:
        return max(width, height)
    return max(1, max(width, height) >> (mip_count - 1))


def _encode_payload(
    target: WTDTexture,
    image_path: Path,
    *,
    quality: float,
    texfury: SimpleNamespace,
) -> bytes:
    format_name = _FORMAT_TO_TEXFURY[target.format_name]
    format_value = getattr(texfury.BCFormat, format_name)
    replacement = texfury.Texture.from_image(
        image_path,
        format=format_value,
        quality=quality,
        generate_mipmaps=target.mip_count > 1,
        min_mip_size=_mip_min_size(
            target.width,
            target.height,
            target.mip_count,
        ),
        resize=(target.width, target.height),
        resize_to_pot=False,
        name=target.name,
    )

    actual_format = getattr(replacement.format, "name", str(replacement.format))
    actual_metadata = (
        replacement.width,
        replacement.height,
        actual_format,
        replacement.mip_count,
    )
    expected_metadata = (
        target.width,
        target.height,
        format_name,
        target.mip_count,
    )
    if actual_metadata != expected_metadata:
        raise TextureDictionaryValidationError(
            f"replacement metadata mismatch for {target.name!r}: "
            f"expected {expected_metadata!r}, got {actual_metadata!r}"
        )

    payload = bytes(replacement.data)
    if len(payload) != target.data_size:
        raise TextureDictionaryValidationError(
            f"replacement payload size mismatch for {target.name!r}: "
            f"expected {target.data_size}, got {len(payload)}"
        )
    return payload


def _write_stage(output_path: Path, data: bytes) -> Path:
    handle, raw_path = tempfile.mkstemp(
        prefix=f".{output_path.stem}.",
        suffix=".wtd.tmp",
        dir=output_path.parent,
    )
    stage_path = Path(raw_path)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return stage_path
    except Exception:
        stage_path.unlink(missing_ok=True)
        raise


def _physical_bytes_unchanged_outside_ranges(
    original: bytes,
    candidate: bytes,
    ranges: list[tuple[int, int]],
) -> bool:
    cursor = 0
    for start, end in sorted(ranges):
        if original[cursor:start] != candidate[cursor:start]:
            return False
        cursor = end
    return original[cursor:] == candidate[cursor:]


def replace_texture_payloads_from_images(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    replacements: Mapping[str, str | os.PathLike[str]],
    *,
    quality: float = 0.9,
    overwrite: bool = False,
) -> PayloadPatchResult:
    """Replace fixed-size texture payloads while preserving RSC5 metadata bytes."""

    if not 0.0 <= quality <= 1.0:
        raise ValueError("quality must be between 0.0 and 1.0")

    source_path, output_path = _normalise_paths(
        source,
        output,
        overwrite=overwrite,
    )
    images = _normalise_replacements(replacements)
    archive = read_wtd(source_path)
    targets = _find_target_textures(archive, images)
    original = _read_resource_sections(source_path)
    patched_physical = bytearray(original.physical)
    texfury = _load_texfury_encoder()

    replaced_names: list[str] = []
    patch_ranges: list[tuple[int, int]] = []
    for folded_name, image_path in images.items():
        target = targets[folded_name]
        assert target.data_offset is not None and target.data_size is not None
        replacement_payload = _encode_payload(
            target,
            image_path,
            quality=quality,
            texfury=texfury,
        )
        start = target.data_offset
        end = start + target.data_size
        patched_physical[start:end] = replacement_payload
        replaced_names.append(target.name)
        patch_ranges.append((start, end))

    resource = original.virtual + bytes(patched_physical)
    staged_data = original.header + zlib.compress(resource, level=9)
    stage_path = _write_stage(output_path, staged_data)

    try:
        staged = _read_resource_sections(stage_path)
        if staged.header != original.header:
            raise TextureDictionaryValidationError(
                "RSC5 header changed during physical payload patching"
            )
        if staged.virtual != original.virtual:
            raise TextureDictionaryValidationError(
                "RSC5 virtual metadata changed during physical payload patching"
            )
        if not _physical_bytes_unchanged_outside_ranges(
            original.physical,
            staged.physical,
            patch_ranges,
        ):
            raise TextureDictionaryValidationError(
                "physical bytes outside replacement payloads changed"
            )

        comparison = compare_wtd_files(
            source_path,
            stage_path,
            allowed_payload_changes=replaced_names,
        )
        if not comparison.valid:
            raise TextureDictionaryValidationError(
                "surgically patched WTD failed structural validation: "
                f"missing={list(comparison.missing)!r}, "
                f"extra={list(comparison.extra)!r}, "
                f"metadata_changed={list(comparison.metadata_changed)!r}, "
                "unexpected_payload_changed="
                f"{list(comparison.unexpected_payload_changed)!r}"
            )

        os.replace(stage_path, output_path)
        return PayloadPatchResult(
            source_path=source_path,
            output_path=output_path,
            replaced_textures=tuple(replaced_names),
            texture_count=len(archive.textures),
            output_size=output_path.stat().st_size,
            output_sha256=_sha256_file(output_path),
            virtual_sha256=_sha256_bytes(original.virtual),
            comparison=comparison,
        )
    finally:
        stage_path.unlink(missing_ok=True)


def replace_texture_payload_from_image(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    texture_name: str,
    image: str | os.PathLike[str],
    *,
    quality: float = 0.9,
    overwrite: bool = False,
) -> PayloadPatchResult:
    """Convenience wrapper for one named texture payload."""

    return replace_texture_payloads_from_images(
        source,
        output,
        {texture_name: image},
        quality=quality,
        overwrite=overwrite,
    )
