"""Inspect and surgically patch textures embedded in GTA IV RSC5 WDR files."""

from __future__ import annotations

import hashlib
import io
import os
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, UnidentifiedImageError

from core.radio_logo.wtd import WTDTexture, texture_to_dds
from core.wtd_archive import (
    WTDArchiveSnapshot,
    WTDTextureEntry,
    WTDTexturePreview,
)


RSC5_MAGIC = 0x05435352
VIRTUAL_BASE = 0x50000000
PHYSICAL_BASE = 0x60000000
MAX_DECOMPRESSED_SIZE = 512 * 1024 * 1024

_DRAWABLE_SHADER_GROUP_POINTER_OFFSET = 8
_SHADER_GROUP_TEXTURE_DICTIONARY_POINTER_OFFSET = 4
_TEXTURE_DICTIONARY_SIZE = 32
_TEXTURE_STRUCTURE_SIZE = 80

_FORMATS: dict[int, tuple[str, int, bool]] = {
    0x31545844: ("DXT1", 8, True),
    0x33545844: ("DXT3", 16, True),
    0x35545844: ("DXT5", 16, True),
    0x31495441: ("ATI1", 8, True),
    0x32495441: ("ATI2", 16, True),
    21: ("A8R8G8B8", 4, False),
    25: ("A1R5G5B5", 2, False),
    23: ("R5G6B5", 2, False),
    28: ("A8", 1, False),
    50: ("L8", 1, False),
}
_FORMAT_TO_TEXFURY = {
    "DXT1": "BC1",
    "DXT5": "BC3",
    "A8R8G8B8": "A8R8G8B8",
}
SUPPORTED_WDR_TEXTURE_REPLACEMENT_FORMATS = frozenset(_FORMAT_TO_TEXFURY)


class WDRParseError(ValueError):
    """Raised when a WDR drawable is malformed or unsupported."""


class WDRTextureReplacementError(RuntimeError):
    """Raised when an embedded WDR texture cannot be replaced safely."""


@dataclass(frozen=True)
class WDRHeader:
    resource_type: int
    flags: int
    virtual_size: int
    physical_size: int
    texture_count: int


@dataclass(frozen=True)
class WDRArchive:
    path: Path
    header: WDRHeader
    textures: tuple[WTDTexture, ...]


@dataclass(frozen=True)
class WDRTextureReplacementResult:
    source_path: Path
    output_path: Path
    replacement_image_path: Path
    texture: WTDTextureEntry
    output_size: int
    output_sha256: str
    virtual_sha256: str


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
        replaceable=(
            texture.extractable
            and texture.format_name in SUPPORTED_WDR_TEXTURE_REPLACEMENT_FORMATS
        ),
    )


def _select_texture(archive: WDRArchive, index: int) -> WTDTexture:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("texture index must be an integer")
    for texture in archive.textures:
        if texture.index == index:
            return texture
    raise KeyError(f"embedded WDR texture index not found: {index}")


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
        with destination.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
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


def _decode_resource_size(flags: int, base_shift: int, exponent_shift: int) -> int:
    base = (flags >> base_shift) & 0x7FF
    exponent = (flags >> exponent_shift) & 0xF
    return base << (exponent + 8)


def _virtual_offset(pointer: int, virtual_size: int, length: int = 1) -> int:
    offset = pointer - VIRTUAL_BASE
    if pointer == 0 or offset < 0 or offset + length > virtual_size:
        raise WDRParseError(
            f"invalid virtual pointer 0x{pointer:08X} for {length} byte(s)"
        )
    return offset


def _physical_offset(pointer: int, physical_size: int, length: int = 1) -> int:
    offset = pointer - PHYSICAL_BASE
    if pointer == 0 or offset < 0 or offset + length > physical_size:
        raise WDRParseError(
            f"invalid physical pointer 0x{pointer:08X} for {length} byte(s)"
        )
    return offset


def _read_c_string(data: bytes, offset: int, limit: int) -> str:
    if offset < 0 or offset >= limit:
        raise WDRParseError(f"string pointer is outside virtual memory: 0x{offset:X}")
    end = data.find(b"\0", offset, limit)
    if end < 0:
        raise WDRParseError("unterminated embedded texture name")
    return data[offset:end].decode("utf-8", errors="replace")


def _mip_chain_size(
    width: int,
    height: int,
    format_code: int,
    mip_count: int,
) -> int | None:
    format_info = _FORMATS.get(format_code)
    if format_info is None:
        return None
    if width <= 0 or height <= 0:
        raise WDRParseError(f"invalid embedded texture dimensions: {width}x{height}")

    _, bytes_per_unit, compressed = format_info
    total = 0
    for level in range(max(1, mip_count)):
        mip_width = max(1, width >> level)
        mip_height = max(1, height >> level)
        if compressed:
            blocks_wide = max(1, (mip_width + 3) // 4)
            blocks_high = max(1, (mip_height + 3) // 4)
            total += blocks_wide * blocks_high * bytes_per_unit
        else:
            total += mip_width * mip_height * bytes_per_unit
    return total


def _normalise_texture_name(raw_name: str, index: int, texture_hash: int) -> str:
    name = raw_name.replace("\\", "/")
    if name.casefold().startswith("pack:/"):
        name = name[6:]
    name = name.rsplit("/", 1)[-1]
    if name.casefold().endswith(".dds"):
        name = name[:-4]
    name = name.strip()
    return name or f"texture_{index:03d}_{texture_hash:08x}"


def _decompress_resource(file_data: bytes) -> tuple[int, int, bytes, bytes]:
    if len(file_data) < 12:
        raise WDRParseError("WDR header is truncated")

    magic, resource_type, flags = struct.unpack_from("<III", file_data, 0)
    if magic != RSC5_MAGIC:
        raise WDRParseError(f"invalid RSC5 magic: 0x{magic:08X}")

    virtual_size = _decode_resource_size(flags, 0, 11)
    physical_size = _decode_resource_size(flags, 15, 26)
    expected_size = virtual_size + physical_size
    if virtual_size < 12 or physical_size <= 0:
        raise WDRParseError(
            f"invalid resource sizes: virtual={virtual_size}, physical={physical_size}"
        )
    if expected_size > MAX_DECOMPRESSED_SIZE:
        raise WDRParseError(
            f"refusing to decompress {expected_size} bytes "
            f"(limit: {MAX_DECOMPRESSED_SIZE})"
        )

    decompressor = zlib.decompressobj()
    try:
        resource_data = decompressor.decompress(file_data[12:], expected_size + 1)
        resource_data += decompressor.flush()
    except zlib.error as exc:
        raise WDRParseError(f"cannot decompress RSC5 payload: {exc}") from exc

    if not decompressor.eof:
        raise WDRParseError("compressed RSC5 payload is truncated")
    if decompressor.unused_data:
        raise WDRParseError("unexpected data follows the WDR RSC5 payload")
    if len(resource_data) != expected_size:
        raise WDRParseError(
            f"decompressed size mismatch: expected {expected_size}, "
            f"got {len(resource_data)}"
        )
    return (
        resource_type,
        flags,
        resource_data[:virtual_size],
        resource_data[virtual_size:],
    )


def read_wdr(path: str | os.PathLike[str]) -> WDRArchive:
    """Parse a WDR and retain textures embedded in its drawable shader group."""
    wdr_path = Path(path).expanduser().resolve()
    if not wdr_path.is_file():
        raise FileNotFoundError(wdr_path)

    resource_type, flags, virtual_data, physical_data = _decompress_resource(
        wdr_path.read_bytes()
    )
    virtual_size = len(virtual_data)
    physical_size = len(physical_data)

    shader_group_ptr = struct.unpack_from(
        "<I", virtual_data, _DRAWABLE_SHADER_GROUP_POINTER_OFFSET
    )[0]
    if shader_group_ptr == 0:
        return WDRArchive(
            path=wdr_path,
            header=WDRHeader(
                resource_type=resource_type,
                flags=flags,
                virtual_size=virtual_size,
                physical_size=physical_size,
                texture_count=0,
            ),
            textures=(),
        )

    shader_group_offset = _virtual_offset(shader_group_ptr, virtual_size, 8)
    texture_dictionary_ptr = struct.unpack_from(
        "<I",
        virtual_data,
        shader_group_offset + _SHADER_GROUP_TEXTURE_DICTIONARY_POINTER_OFFSET,
    )[0]
    if texture_dictionary_ptr == 0:
        return WDRArchive(
            path=wdr_path,
            header=WDRHeader(
                resource_type=resource_type,
                flags=flags,
                virtual_size=virtual_size,
                physical_size=physical_size,
                texture_count=0,
            ),
            textures=(),
        )

    dictionary_offset = _virtual_offset(
        texture_dictionary_ptr,
        virtual_size,
        _TEXTURE_DICTIONARY_SIZE,
    )
    hash_table_ptr = struct.unpack_from("<I", virtual_data, dictionary_offset + 16)[0]
    hash_count = struct.unpack_from("<H", virtual_data, dictionary_offset + 20)[0]
    texture_table_ptr = struct.unpack_from(
        "<I", virtual_data, dictionary_offset + 24
    )[0]
    texture_count = struct.unpack_from("<H", virtual_data, dictionary_offset + 28)[0]

    hashes: tuple[int, ...] = ()
    if hash_count:
        hash_offset = _virtual_offset(hash_table_ptr, virtual_size, hash_count * 4)
        hashes = struct.unpack_from(f"<{hash_count}I", virtual_data, hash_offset)

    texture_pointers: tuple[int, ...] = ()
    if texture_count:
        pointer_offset = _virtual_offset(
            texture_table_ptr,
            virtual_size,
            texture_count * 4,
        )
        texture_pointers = struct.unpack_from(
            f"<{texture_count}I",
            virtual_data,
            pointer_offset,
        )

    textures: list[WTDTexture] = []
    for index, texture_ptr in enumerate(texture_pointers):
        texture_offset = _virtual_offset(
            texture_ptr,
            virtual_size,
            _TEXTURE_STRUCTURE_SIZE,
        )
        name_ptr = struct.unpack_from("<I", virtual_data, texture_offset + 20)[0]
        width, height = struct.unpack_from("<HH", virtual_data, texture_offset + 28)
        format_code = struct.unpack_from("<I", virtual_data, texture_offset + 32)[0]
        stride = struct.unpack_from("<H", virtual_data, texture_offset + 36)[0]
        texture_type = virtual_data[texture_offset + 38]
        mip_count = max(1, virtual_data[texture_offset + 39])
        data_ptr = struct.unpack_from("<I", virtual_data, texture_offset + 72)[0]

        raw_name = ""
        if name_ptr:
            name_offset = _virtual_offset(name_ptr, virtual_size)
            raw_name = _read_c_string(virtual_data, name_offset, virtual_size)

        texture_hash = hashes[index] if index < len(hashes) else 0
        name = _normalise_texture_name(raw_name, index, texture_hash)
        format_name = _FORMATS.get(
            format_code,
            (f"UNKNOWN_0x{format_code:08X}", 0, False),
        )[0]
        data_size = _mip_chain_size(width, height, format_code, mip_count)

        data_offset: int | None = None
        texture_data: bytes | None = None
        if data_size is not None and data_ptr:
            data_offset = _physical_offset(data_ptr, physical_size, data_size)
            texture_data = physical_data[data_offset : data_offset + data_size]

        textures.append(
            WTDTexture(
                index=index,
                hash=texture_hash,
                name=name,
                raw_name=raw_name,
                width=width,
                height=height,
                format_code=format_code,
                format_name=format_name,
                stride=stride,
                texture_type=texture_type,
                mip_count=mip_count,
                data_offset=data_offset,
                data_size=data_size,
                data=texture_data,
            )
        )

    return WDRArchive(
        path=wdr_path,
        header=WDRHeader(
            resource_type=resource_type,
            flags=flags,
            virtual_size=virtual_size,
            physical_size=physical_size,
            texture_count=texture_count,
        ),
        textures=tuple(textures),
    )


def inspect_wdr_archive(path: str | os.PathLike[str]) -> WTDArchiveSnapshot:
    """Return metadata for textures embedded in one WDR drawable."""
    archive = read_wdr(path)
    return WTDArchiveSnapshot(
        path=archive.path,
        resource_type=archive.header.resource_type,
        flags=archive.header.flags,
        virtual_size=archive.header.virtual_size,
        physical_size=archive.header.physical_size,
        textures=tuple(_entry_from_texture(texture) for texture in archive.textures),
    )


def _texture_to_png(texture: WTDTexture) -> bytes:
    dds = texture_to_dds(texture)
    try:
        with Image.open(io.BytesIO(dds)) as source:
            source.load()
            image = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise WDRParseError(
            f"embedded texture {texture.name!r} cannot be decoded: {exc}"
        ) from exc
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def export_wdr_texture(
    path: str | os.PathLike[str],
    texture_index: int,
    destination_path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> Path:
    """Export one embedded WDR texture as PNG or exact DDS."""
    archive = read_wdr(path)
    texture = _select_texture(archive, texture_index)
    destination = Path(destination_path).expanduser().resolve()
    suffix = destination.suffix.casefold()
    if suffix == ".png":
        payload = _texture_to_png(texture)
    elif suffix == ".dds":
        payload = texture_to_dds(texture)
    else:
        raise ValueError("texture export destination must end with .png or .dds")
    result = _write_bytes_atomic(destination, payload, overwrite=overwrite)
    if result.stat().st_size != len(payload):
        raise OSError(f"Exported texture size verification failed: {result}")
    return result


def render_wdr_texture_preview(
    path: str | os.PathLike[str],
    texture_index: int,
    *,
    max_dimension: int = 512,
) -> WTDTexturePreview:
    """Decode one embedded WDR texture and return a bounded PNG preview."""
    if not isinstance(max_dimension, int):
        raise TypeError("max_dimension must be an integer")
    if max_dimension <= 0:
        raise ValueError("max_dimension must be greater than zero")

    archive = read_wdr(path)
    texture = _select_texture(archive, texture_index)
    png_data = _texture_to_png(texture)
    with Image.open(io.BytesIO(png_data)) as source:
        source.load()
        preview = source.convert("RGBA")
    preview.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    preview.save(output, format="PNG")
    return WTDTexturePreview(
        texture=_entry_from_texture(texture),
        png_data=output.getvalue(),
        width=preview.width,
        height=preview.height,
    )


def _load_texfury_encoder() -> SimpleNamespace:
    try:
        from texfury import BCFormat, Texture
    except (ImportError, OSError) as exc:
        raise WDRTextureReplacementError(
            "texfury 1.6.2 is required for GTA IV texture encoding; "
            "install runtime dependencies from requirements.txt"
        ) from exc
    return SimpleNamespace(BCFormat=BCFormat, Texture=Texture)


def _mip_min_size(width: int, height: int, mip_count: int) -> int:
    if mip_count <= 1:
        return max(width, height)
    return max(1, max(width, height) >> (mip_count - 1))


def _encode_replacement_payload(
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
        raise WDRTextureReplacementError(
            f"replacement metadata mismatch for {target.name!r}: "
            f"expected {expected_metadata!r}, got {actual_metadata!r}"
        )

    payload = bytes(replacement.data)
    if target.data_size is None or len(payload) != target.data_size:
        raise WDRTextureReplacementError(
            f"replacement payload size mismatch for {target.name!r}: "
            f"expected {target.data_size}, got {len(payload)}"
        )
    return payload


def _texture_metadata(texture: WTDTexture) -> tuple[object, ...]:
    return (
        texture.index,
        texture.hash,
        texture.name,
        texture.raw_name,
        texture.width,
        texture.height,
        texture.format_code,
        texture.format_name,
        texture.stride,
        texture.texture_type,
        texture.mip_count,
        texture.data_offset,
        texture.data_size,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_wdr_texture_from_image(
    path: str | os.PathLike[str],
    texture_index: int,
    image_path: str | os.PathLike[str],
    destination_path: str | os.PathLike[str],
    *,
    quality: float = 0.9,
    overwrite: bool = False,
) -> WDRTextureReplacementResult:
    """Create a validated WDR with one fixed-size embedded texture replaced."""
    if not 0.0 <= quality <= 1.0:
        raise ValueError("quality must be between 0.0 and 1.0")
    if isinstance(texture_index, bool) or not isinstance(texture_index, int):
        raise TypeError("texture index must be an integer")
    if texture_index < 0:
        raise ValueError("texture index must not be negative")

    source = Path(path).expanduser().resolve()
    destination = Path(destination_path).expanduser().resolve()
    image = Path(image_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.casefold() != ".wdr":
        raise ValueError(f"source must be a .wdr file: {source}")
    if destination.suffix.casefold() != ".wdr":
        raise ValueError(f"destination must be a .wdr file: {destination}")
    if source == destination:
        raise ValueError("source and destination WDR paths must be different")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    if not image.is_file():
        raise FileNotFoundError(image)
    destination.parent.mkdir(parents=True, exist_ok=True)

    archive = read_wdr(source)
    target = _select_texture(archive, texture_index)
    if target.data_offset is None or target.data_size is None or target.data is None:
        raise WDRTextureReplacementError(
            f"texture {target.name!r} has no extractable physical payload"
        )
    if target.format_name not in SUPPORTED_WDR_TEXTURE_REPLACEMENT_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_WDR_TEXTURE_REPLACEMENT_FORMATS))
        raise WDRTextureReplacementError(
            f"cannot encode replacement for {target.name!r} in "
            f"{target.format_name}; supported formats are {supported}"
        )

    file_data = source.read_bytes()
    _resource_type, _flags, virtual_data, physical_data = _decompress_resource(
        file_data
    )
    replacement_payload = _encode_replacement_payload(
        target,
        image,
        quality=quality,
        texfury=_load_texfury_encoder(),
    )
    start = target.data_offset
    end = start + target.data_size
    patched_physical = bytearray(physical_data)
    patched_physical[start:end] = replacement_payload
    staged_data = file_data[:12] + zlib.compress(
        virtual_data + bytes(patched_physical),
        level=9,
    )

    descriptor, stage_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".wdr.tmp",
        dir=destination.parent,
    )
    stage = Path(stage_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(staged_data)
            output.flush()
            os.fsync(output.fileno())

        candidate = read_wdr(stage)
        if len(candidate.textures) != len(archive.textures):
            raise WDRTextureReplacementError(
                "patched WDR changed the embedded texture count"
            )
        for previous, current in zip(archive.textures, candidate.textures):
            if _texture_metadata(previous) != _texture_metadata(current):
                raise WDRTextureReplacementError(
                    "patched WDR changed embedded texture metadata at index "
                    f"{previous.index}"
                )
            expected = (
                replacement_payload
                if previous.index == target.index
                else previous.data
            )
            if current.data != expected:
                change_kind = (
                    "replacement payload does not match encoded bytes"
                    if previous.index == target.index
                    else "unrelated embedded texture payload changed"
                )
                raise WDRTextureReplacementError(
                    f"{change_kind} at texture index {previous.index}"
                )

        _type, _candidate_flags, staged_virtual, staged_physical = (
            _decompress_resource(stage.read_bytes())
        )
        if staged_virtual != virtual_data:
            raise WDRTextureReplacementError(
                "patched WDR changed virtual metadata or drawable geometry"
            )
        if (
            physical_data[:start] != staged_physical[:start]
            or physical_data[end:] != staged_physical[end:]
        ):
            raise WDRTextureReplacementError(
                "patched WDR changed physical bytes outside the target texture"
            )

        os.replace(stage, destination)
        return WDRTextureReplacementResult(
            source_path=source,
            output_path=destination,
            replacement_image_path=image,
            texture=_entry_from_texture(target),
            output_size=destination.stat().st_size,
            output_sha256=_sha256_file(destination),
            virtual_sha256=hashlib.sha256(virtual_data).hexdigest(),
        )
    finally:
        stage.unlink(missing_ok=True)
