import json
import struct
import zlib
from pathlib import Path

import pytest

from core.radio_logo.wtd import (
    PHYSICAL_BASE,
    RSC5_MAGIC,
    VIRTUAL_BASE,
    WTDParseError,
    extract_wtd,
    main,
    read_wtd,
    texture_to_dds,
)


def _encode_size(size: int, base_shift: int, exponent_shift: int) -> int:
    for exponent in range(15, -1, -1):
        unit = 1 << (exponent + 8)
        if size % unit == 0 and size // unit <= 0x7FF:
            return (size // unit) << base_shift | exponent << exponent_shift
    raise ValueError(size)


def _make_texture_struct(
    *,
    name_pointer: int,
    width: int,
    height: int,
    format_code: int,
    mip_count: int,
    data_pointer: int,
) -> bytes:
    data = bytearray(80)
    struct.pack_into("<I", data, 0, 0x00D50104)
    struct.pack_into("<I", data, 20, name_pointer)
    struct.pack_into("<HH", data, 28, width, height)
    struct.pack_into("<I", data, 32, format_code)
    struct.pack_into("<H", data, 36, 8)
    data[38] = 1
    data[39] = mip_count
    struct.pack_into("<I", data, 72, data_pointer)
    return bytes(data)


def build_wtd(
    path: Path,
    *,
    bad_texture_pointer: bool = False,
    unsupported_second: bool = False,
) -> Path:
    virtual_size = 0x1000
    physical_size = 0x1000
    virtual = bytearray(virtual_size)
    physical = bytearray(physical_size)

    hash_offset = 32
    pointer_offset = 40
    texture_offset = 48
    names_offset = texture_offset + 160

    names = [b"pack:/radio_alpha.dds\0", b"radio_beta\0"]
    name_offsets = [names_offset, names_offset + len(names[0])]
    for offset, name in zip(name_offsets, names):
        virtual[offset : offset + len(name)] = name

    struct.pack_into("<IHHIHH", virtual, 16, VIRTUAL_BASE + hash_offset, 2, 2, VIRTUAL_BASE + pointer_offset, 2, 2)
    struct.pack_into("<II", virtual, hash_offset, 0x11111111, 0x22222222)

    first_pointer = VIRTUAL_BASE + texture_offset
    if bad_texture_pointer:
        first_pointer = VIRTUAL_BASE + virtual_size - 20
    struct.pack_into(
        "<II",
        virtual,
        pointer_offset,
        first_pointer,
        VIRTUAL_BASE + texture_offset + 80,
    )

    dxt1_data = b"\x10" * 8
    rgba_data = bytes(range(16))
    physical[0:8] = dxt1_data
    physical[8:24] = rgba_data

    virtual[texture_offset : texture_offset + 80] = _make_texture_struct(
        name_pointer=VIRTUAL_BASE + name_offsets[0],
        width=4,
        height=4,
        format_code=0x31545844,
        mip_count=1,
        data_pointer=PHYSICAL_BASE,
    )
    virtual[texture_offset + 80 : texture_offset + 160] = _make_texture_struct(
        name_pointer=VIRTUAL_BASE + name_offsets[1],
        width=2,
        height=2,
        format_code=0xDEADBEEF if unsupported_second else 21,
        mip_count=1,
        data_pointer=PHYSICAL_BASE + 8,
    )

    flags = _encode_size(virtual_size, 0, 11) | _encode_size(physical_size, 15, 26)
    payload = zlib.compress(bytes(virtual + physical))
    path.write_bytes(struct.pack("<III", RSC5_MAGIC, 8, flags) + payload)
    return path


def test_read_wtd_lists_textures(tmp_path):
    archive = read_wtd(build_wtd(tmp_path / "radio_hud.wtd"))

    assert archive.header.resource_type == 8
    assert archive.header.virtual_size == 0x1000
    assert archive.header.physical_size == 0x1000
    assert archive.header.texture_count == 2
    assert [texture.name for texture in archive.textures] == [
        "radio_alpha",
        "radio_beta",
    ]


def test_read_wtd_preserves_texture_metadata(tmp_path):
    archive = read_wtd(build_wtd(tmp_path / "radio_hud.wtd"))
    first, second = archive.textures

    assert first.hash == 0x11111111
    assert first.width == 4
    assert first.height == 4
    assert first.format_name == "DXT1"
    assert first.data_size == 8
    assert first.data == b"\x10" * 8

    assert second.format_name == "A8R8G8B8"
    assert second.data_size == 16
    assert second.data == bytes(range(16))


def test_read_wtd_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_wtd(tmp_path / "missing.wtd")


def test_read_wtd_rejects_truncated_header(tmp_path):
    path = tmp_path / "bad.wtd"
    path.write_bytes(b"RSC")

    with pytest.raises(WTDParseError, match="header is truncated"):
        read_wtd(path)


def test_read_wtd_rejects_invalid_magic(tmp_path):
    path = build_wtd(tmp_path / "bad.wtd")
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, 0, 0x12345678)
    path.write_bytes(data)

    with pytest.raises(WTDParseError, match="invalid RSC5 magic"):
        read_wtd(path)


def test_read_wtd_rejects_invalid_compression(tmp_path):
    path = build_wtd(tmp_path / "bad.wtd")
    path.write_bytes(path.read_bytes()[:12] + b"not zlib")

    with pytest.raises(WTDParseError, match="cannot decompress"):
        read_wtd(path)


def test_read_wtd_rejects_out_of_bounds_texture_pointer(tmp_path):
    path = build_wtd(tmp_path / "bad.wtd", bad_texture_pointer=True)

    with pytest.raises(WTDParseError, match="invalid virtual pointer"):
        read_wtd(path)


def test_unsupported_texture_remains_visible_but_not_extractable(tmp_path):
    archive = read_wtd(
        build_wtd(tmp_path / "radio_hud.wtd", unsupported_second=True)
    )

    second = archive.textures[1]
    assert second.format_name == "UNKNOWN_0xDEADBEEF"
    assert second.data is None
    assert second.data_size is None
    assert not second.extractable


def test_texture_to_dds_wraps_dxt1_payload(tmp_path):
    texture = read_wtd(build_wtd(tmp_path / "radio_hud.wtd")).textures[0]

    dds = texture_to_dds(texture)

    assert dds[:4] == b"DDS "
    assert len(dds) == 128 + 8
    assert dds[84:88] == b"DXT1"
    assert dds[-8:] == b"\x10" * 8


def test_texture_to_dds_writes_a8r8g8b8_masks(tmp_path):
    texture = read_wtd(build_wtd(tmp_path / "radio_hud.wtd")).textures[1]

    dds = texture_to_dds(texture)

    assert dds[:4] == b"DDS "
    assert len(dds) == 128 + 16
    assert struct.unpack_from("<I", dds, 88)[0] == 32
    assert struct.unpack_from("<IIII", dds, 92) == (
        0x00FF0000,
        0x0000FF00,
        0x000000FF,
        0xFF000000,
    )


def test_extract_wtd_writes_supported_textures(tmp_path):
    source = build_wtd(tmp_path / "radio_hud.wtd")
    output = tmp_path / "textures"

    written = extract_wtd(source, output)

    assert [path.name for path in written] == [
        "radio_alpha.dds",
        "radio_beta.dds",
    ]
    assert all(path.read_bytes().startswith(b"DDS ") for path in written)


def test_extract_wtd_skips_unsupported_textures(tmp_path):
    source = build_wtd(
        tmp_path / "radio_hud.wtd",
        unsupported_second=True,
    )

    written = extract_wtd(source, tmp_path / "textures")

    assert [path.name for path in written] == ["radio_alpha.dds"]


def test_extract_wtd_refuses_to_overwrite_by_default(tmp_path):
    source = build_wtd(tmp_path / "radio_hud.wtd")
    output = tmp_path / "textures"
    output.mkdir()
    (output / "radio_alpha.dds").write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        extract_wtd(source, output)

    assert (output / "radio_alpha.dds").read_bytes() == b"existing"


def test_extract_wtd_can_overwrite(tmp_path):
    source = build_wtd(tmp_path / "radio_hud.wtd")
    output = tmp_path / "textures"
    output.mkdir()
    existing = output / "radio_alpha.dds"
    existing.write_bytes(b"existing")

    extract_wtd(source, output, overwrite=True)

    assert existing.read_bytes().startswith(b"DDS ")


def test_inspect_cli_outputs_json(tmp_path, capsys):
    source = build_wtd(tmp_path / "radio_hud.wtd")

    result = main(["inspect", str(source), "--json"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["header"]["texture_count"] == 2
    assert payload["textures"][0]["name"] == "radio_alpha"
