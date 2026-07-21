"""Read and extract GTA IV RSC5 texture dictionaries (WTD files)."""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import tempfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

RSC5_MAGIC = 0x05435352
VIRTUAL_BASE = 0x50000000
PHYSICAL_BASE = 0x60000000
MAX_DECOMPRESSED_SIZE = 512 * 1024 * 1024

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


class WTDParseError(ValueError):
    """Raised when a WTD file is malformed or unsupported."""


@dataclass(frozen=True)
class WTDHeader:
    resource_type: int
    flags: int
    virtual_size: int
    physical_size: int
    texture_count: int


@dataclass(frozen=True)
class WTDTexture:
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
    data_offset: int | None
    data_size: int | None
    data: bytes | None

    @property
    def extractable(self) -> bool:
        return self.data is not None and self.format_code in _FORMATS


@dataclass(frozen=True)
class WTDArchive:
    path: Path
    header: WTDHeader
    textures: tuple[WTDTexture, ...]

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "header": asdict(self.header),
            "textures": [
                {
                    key: value
                    for key, value in asdict(texture).items()
                    if key != "data"
                }
                | {"extractable": texture.extractable}
                for texture in self.textures
            ],
        }


def _decode_resource_size(flags: int, base_shift: int, exponent_shift: int) -> int:
    base = (flags >> base_shift) & 0x7FF
    exponent = (flags >> exponent_shift) & 0xF
    return base << (exponent + 8)


def _read_c_string(data: bytes, offset: int, limit: int) -> str:
    if offset < 0 or offset >= limit:
        raise WTDParseError(f"string pointer is outside virtual memory: 0x{offset:X}")
    end = data.find(b"\0", offset, limit)
    if end < 0:
        raise WTDParseError("unterminated texture name")
    return data[offset:end].decode("utf-8", errors="replace")


def _virtual_offset(pointer: int, virtual_size: int, length: int = 1) -> int:
    offset = pointer - VIRTUAL_BASE
    if pointer == 0 or offset < 0 or offset + length > virtual_size:
        raise WTDParseError(
            f"invalid virtual pointer 0x{pointer:08X} for {length} byte(s)"
        )
    return offset


def _physical_offset(pointer: int, physical_size: int, length: int = 1) -> int:
    offset = pointer - PHYSICAL_BASE
    if pointer == 0 or offset < 0 or offset + length > physical_size:
        raise WTDParseError(
            f"invalid physical pointer 0x{pointer:08X} for {length} byte(s)"
        )
    return offset


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
        raise WTDParseError(f"invalid texture dimensions: {width}x{height}")

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
    if name.lower().startswith("pack:/"):
        name = name[6:]
    name = name.rsplit("/", 1)[-1]
    if name.lower().endswith(".dds"):
        name = name[:-4]
    name = name.strip()
    return name or f"texture_{index:03d}_{texture_hash:08x}"


def read_wtd(path: str | os.PathLike[str]) -> WTDArchive:
    """Parse an RSC5 WTD and retain extractable texture payloads."""

    wtd_path = Path(path).expanduser().resolve()
    if not wtd_path.is_file():
        raise FileNotFoundError(wtd_path)

    file_data = wtd_path.read_bytes()
    if len(file_data) < 12:
        raise WTDParseError("WTD header is truncated")

    magic, resource_type, flags = struct.unpack_from("<III", file_data, 0)
    if magic != RSC5_MAGIC:
        raise WTDParseError(f"invalid RSC5 magic: 0x{magic:08X}")

    virtual_size = _decode_resource_size(flags, 0, 11)
    physical_size = _decode_resource_size(flags, 15, 26)
    expected_size = virtual_size + physical_size
    if virtual_size < 32 or physical_size <= 0:
        raise WTDParseError(
            f"invalid resource sizes: virtual={virtual_size}, physical={physical_size}"
        )
    if expected_size > MAX_DECOMPRESSED_SIZE:
        raise WTDParseError(
            f"refusing to decompress {expected_size} bytes "
            f"(limit: {MAX_DECOMPRESSED_SIZE})"
        )

    decompressor = zlib.decompressobj()
    try:
        resource_data = decompressor.decompress(file_data[12:], expected_size + 1)
        resource_data += decompressor.flush()
    except zlib.error as exc:
        raise WTDParseError(f"cannot decompress RSC5 payload: {exc}") from exc

    if not decompressor.eof:
        raise WTDParseError("compressed RSC5 payload is truncated")
    if len(resource_data) != expected_size:
        raise WTDParseError(
            f"decompressed size mismatch: expected {expected_size}, "
            f"got {len(resource_data)}"
        )

    virtual_data = resource_data[:virtual_size]
    physical_data = resource_data[virtual_size:]

    hash_table_ptr = struct.unpack_from("<I", virtual_data, 16)[0]
    hash_count, _hash_capacity = struct.unpack_from("<HH", virtual_data, 20)
    texture_table_ptr = struct.unpack_from("<I", virtual_data, 24)[0]
    texture_count, _texture_capacity = struct.unpack_from("<HH", virtual_data, 28)

    hashes: list[int] = []
    if hash_count:
        hash_offset = _virtual_offset(hash_table_ptr, virtual_size, hash_count * 4)
        hashes = list(struct.unpack_from(f"<{hash_count}I", virtual_data, hash_offset))

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
        texture_offset = _virtual_offset(texture_ptr, virtual_size, 80)
        name_ptr = struct.unpack_from("<I", virtual_data, texture_offset + 20)[0]
        width, height = struct.unpack_from("<HH", virtual_data, texture_offset + 28)
        format_code = struct.unpack_from("<I", virtual_data, texture_offset + 32)[0]
        stride = struct.unpack_from("<H", virtual_data, texture_offset + 36)[0]
        texture_type = virtual_data[texture_offset + 38]
        mip_count = virtual_data[texture_offset + 39]
        data_ptr = struct.unpack_from("<I", virtual_data, texture_offset + 72)[0]

        raw_name = ""
        if name_ptr:
            name_offset = _virtual_offset(name_ptr, virtual_size)
            raw_name = _read_c_string(virtual_data, name_offset, virtual_size)

        texture_hash = hashes[index] if index < len(hashes) else 0
        name = _normalise_texture_name(raw_name, index, texture_hash)
        format_name = _FORMATS.get(format_code, (f"UNKNOWN_0x{format_code:08X}", 0, False))[0]
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
                mip_count=max(1, mip_count),
                data_offset=data_offset,
                data_size=data_size,
                data=texture_data,
            )
        )

    return WTDArchive(
        path=wtd_path,
        header=WTDHeader(
            resource_type=resource_type,
            flags=flags,
            virtual_size=virtual_size,
            physical_size=physical_size,
            texture_count=texture_count,
        ),
        textures=tuple(textures),
    )


def _dds_pixel_format(format_code: int) -> tuple[int, bytes, int, int, int, int, int]:
    if format_code in {
        0x31545844,
        0x33545844,
        0x35545844,
        0x31495441,
        0x32495441,
    }:
        return 0x4, struct.pack("<I", format_code), 0, 0, 0, 0, 0
    if format_code == 21:
        return 0x41, b"\0\0\0\0", 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000
    if format_code == 25:
        return 0x41, b"\0\0\0\0", 16, 0x00007C00, 0x000003E0, 0x0000001F, 0x00008000
    if format_code == 23:
        return 0x40, b"\0\0\0\0", 16, 0x0000F800, 0x000007E0, 0x0000001F, 0
    if format_code == 28:
        return 0x2, b"\0\0\0\0", 8, 0, 0, 0, 0x000000FF
    if format_code == 50:
        return 0x20000, b"\0\0\0\0", 8, 0x000000FF, 0, 0, 0
    raise WTDParseError(f"cannot create DDS for format 0x{format_code:08X}")


def texture_to_dds(texture: WTDTexture) -> bytes:
    """Wrap one texture's raw mip chain in a legacy DDS header."""

    if not texture.extractable or texture.data is None or texture.data_size is None:
        raise WTDParseError(
            f"texture {texture.name!r} uses unsupported format "
            f"{texture.format_name}"
        )

    format_info = _FORMATS[texture.format_code]
    compressed = format_info[2]
    mip0_size = _mip_chain_size(
        texture.width,
        texture.height,
        texture.format_code,
        1,
    )
    assert mip0_size is not None

    ddsd_caps = 0x1
    ddsd_height = 0x2
    ddsd_width = 0x4
    ddsd_pitch = 0x8
    ddsd_pixel_format = 0x1000
    ddsd_mipmap_count = 0x20000
    ddsd_linear_size = 0x80000

    flags = ddsd_caps | ddsd_height | ddsd_width | ddsd_pixel_format
    flags |= ddsd_linear_size if compressed else ddsd_pitch
    if texture.mip_count > 1:
        flags |= ddsd_mipmap_count

    pitch_or_linear_size = (
        mip0_size
        if compressed
        else texture.width * format_info[1]
    )

    pf_flags, fourcc, bit_count, r_mask, g_mask, b_mask, a_mask = _dds_pixel_format(
        texture.format_code
    )
    pixel_format = struct.pack(
        "<II4sIIIII",
        32,
        pf_flags,
        fourcc,
        bit_count,
        r_mask,
        g_mask,
        b_mask,
        a_mask,
    )

    caps = 0x1000
    if texture.mip_count > 1:
        caps |= 0x8 | 0x400000

    header = struct.pack(
        "<IIIIIII11I",
        124,
        flags,
        texture.height,
        texture.width,
        pitch_or_linear_size,
        0,
        texture.mip_count,
        *([0] * 11),
    )
    header += pixel_format
    header += struct.pack("<IIIII", caps, 0, 0, 0, 0)

    if len(header) != 124:
        raise AssertionError(f"internal DDS header size is {len(header)}, expected 124")
    return b"DDS " + header + texture.data


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_output_name(texture: WTDTexture, used: set[str]) -> str:
    stem = _SAFE_FILENAME.sub("_", texture.name).strip("._") or f"texture_{texture.index:03d}"
    candidate = f"{stem}.dds"
    key = candidate.casefold()
    if key not in used:
        used.add(key)
        return candidate

    candidate = f"{stem}_{texture.hash:08x}.dds"
    key = candidate.casefold()
    suffix = 2
    while key in used:
        candidate = f"{stem}_{texture.hash:08x}_{suffix}.dds"
        key = candidate.casefold()
        suffix += 1
    used.add(key)
    return candidate


def extract_wtd(
    path: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Extract every supported texture as DDS using atomic file replacement."""

    archive = read_wtd(path)
    output_dir = Path(output_directory).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    used_names: set[str] = set()
    for texture in archive.textures:
        if not texture.extractable:
            continue
        output_path = output_dir / _safe_output_name(texture, used_names)
        if output_path.exists() and not overwrite:
            raise FileExistsError(output_path)

        payload = texture_to_dds(texture)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_dir,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, output_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        written.append(output_path)

    return tuple(written)


def _format_archive(archive: WTDArchive) -> Iterable[str]:
    yield f"WTD: {archive.path}"
    yield f"Resource type: 0x{archive.header.resource_type:08X}"
    yield f"Virtual memory: {archive.header.virtual_size} bytes"
    yield f"Physical memory: {archive.header.physical_size} bytes"
    yield f"Textures: {len(archive.textures)}"
    for texture in archive.textures:
        status = "extractable" if texture.extractable else "metadata only"
        size = texture.data_size if texture.data_size is not None else "unknown"
        yield (
            f"[{texture.index:02d}] {texture.name} | "
            f"{texture.width}x{texture.height} | {texture.format_name} | "
            f"mips={texture.mip_count} | hash=0x{texture.hash:08X} | "
            f"bytes={size} | {status}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or extract GTA IV RSC5 WTD texture dictionaries."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="List WTD textures")
    inspect_parser.add_argument("wtd")
    inspect_parser.add_argument("--json", action="store_true")

    extract_parser = subparsers.add_parser("extract", help="Extract textures as DDS")
    extract_parser.add_argument("wtd")
    extract_parser.add_argument("output_directory")
    extract_parser.add_argument("--overwrite", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            archive = read_wtd(args.wtd)
            if args.json:
                print(json.dumps(archive.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("\n".join(_format_archive(archive)))
            return 0

        written = extract_wtd(
            args.wtd,
            args.output_directory,
            overwrite=args.overwrite,
        )
        print(f"Extracted {len(written)} texture(s):")
        for path in written:
            print(path)
        return 0
    except (FileNotFoundError, FileExistsError, WTDParseError, OSError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
