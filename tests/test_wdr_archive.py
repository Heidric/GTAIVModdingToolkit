from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from core.wdr_archive import (
    WDRParseError,
    export_wdr_texture,
    inspect_wdr_archive,
    read_wdr,
    render_wdr_texture_preview,
)


RSC5_MAGIC = 0x05435352
VIRTUAL_BASE = 0x50000000
PHYSICAL_BASE = 0x60000000


def _resource_flags(virtual_size: int, physical_size: int) -> int:
    assert virtual_size % 256 == 0
    assert physical_size % 256 == 0
    virtual_base = virtual_size // 256
    physical_base = physical_size // 256
    assert 0 < virtual_base <= 0x7FF
    assert 0 < physical_base <= 0x7FF
    return (
        virtual_base
        | (physical_base << 15)
        | (1 << 30)
        | (1 << 31)
    )


def _write_wdr(path: Path, *, dictionary_pointer: int = VIRTUAL_BASE + 0x200) -> Path:
    virtual_size = 0x1000
    physical_size = 0x1000
    virtual = bytearray(virtual_size)
    physical = bytearray(physical_size)

    shader_group_pointer = VIRTUAL_BASE + 0x100
    struct.pack_into("<I", virtual, 8, shader_group_pointer)
    struct.pack_into("<I", virtual, 0x100 + 4, dictionary_pointer)

    if dictionary_pointer and dictionary_pointer < VIRTUAL_BASE + virtual_size:
        dictionary_offset = dictionary_pointer - VIRTUAL_BASE
        hash_pointer = VIRTUAL_BASE + 0x300
        pointer_table = VIRTUAL_BASE + 0x310
        texture_pointer = VIRTUAL_BASE + 0x400
        name_pointer = VIRTUAL_BASE + 0x500

        struct.pack_into("<I", virtual, dictionary_offset + 16, hash_pointer)
        struct.pack_into("<HH", virtual, dictionary_offset + 20, 1, 1)
        struct.pack_into("<I", virtual, dictionary_offset + 24, pointer_table)
        struct.pack_into("<HH", virtual, dictionary_offset + 28, 1, 1)
        struct.pack_into("<I", virtual, 0x300, 0x12345678)
        struct.pack_into("<I", virtual, 0x310, texture_pointer)

        texture_offset = texture_pointer - VIRTUAL_BASE
        struct.pack_into("<I", virtual, texture_offset + 20, name_pointer)
        struct.pack_into("<HH", virtual, texture_offset + 28, 4, 4)
        struct.pack_into("<I", virtual, texture_offset + 32, 0x31545844)
        struct.pack_into("<H", virtual, texture_offset + 36, 8)
        virtual[texture_offset + 38] = 0
        virtual[texture_offset + 39] = 1
        struct.pack_into("<I", virtual, texture_offset + 72, PHYSICAL_BASE)
        virtual[0x500 : 0x500 + len(b"shop_front\0")] = b"shop_front\0"

        # One opaque-white DXT1 block.
        physical[:8] = b"\xff\xff\x00\x00\x00\x00\x00\x00"

    flags = _resource_flags(virtual_size, physical_size)
    payload = zlib.compress(bytes(virtual + physical), level=9)
    path.write_bytes(struct.pack("<III", RSC5_MAGIC, 110, flags) + payload)
    return path


def test_read_wdr_finds_embedded_texture_dictionary(tmp_path):
    archive = read_wdr(_write_wdr(tmp_path / "shop.wdr"))

    assert archive.header.resource_type == 110
    assert archive.header.texture_count == 1
    texture = archive.textures[0]
    assert texture.name == "shop_front"
    assert texture.hash == 0x12345678
    assert (texture.width, texture.height) == (4, 4)
    assert texture.format_name == "DXT1"
    assert texture.mip_count == 1
    assert texture.data == b"\xff\xff\x00\x00\x00\x00\x00\x00"


def test_read_wdr_accepts_drawable_without_embedded_dictionary(tmp_path):
    archive = read_wdr(
        _write_wdr(tmp_path / "untextured.wdr", dictionary_pointer=0)
    )

    assert archive.header.texture_count == 0
    assert archive.textures == ()


def test_read_wdr_rejects_out_of_bounds_dictionary_pointer(tmp_path):
    path = _write_wdr(
        tmp_path / "broken.wdr",
        dictionary_pointer=VIRTUAL_BASE + 0x2000,
    )

    with pytest.raises(WDRParseError, match="invalid virtual pointer"):
        read_wdr(path)


def test_inspect_wdr_marks_supported_embedded_textures_replaceable(tmp_path):
    snapshot = inspect_wdr_archive(_write_wdr(tmp_path / "shop.wdr"))

    assert snapshot.texture(0).extractable
    assert snapshot.texture(0).replaceable


def test_wdr_texture_preview_and_photoshop_friendly_png_export(tmp_path):
    source = _write_wdr(tmp_path / "shop.wdr")

    preview = render_wdr_texture_preview(source, 0, max_dimension=4)
    assert (preview.width, preview.height) == (4, 4)
    assert preview.png_data.startswith(b"\x89PNG\r\n\x1a\n")

    destination = tmp_path / "shop_front.png"
    result = export_wdr_texture(source, 0, destination)
    assert result == destination.resolve()
    with Image.open(result) as image:
        image.load()
        assert image.mode == "RGBA"
        assert image.size == (4, 4)


def test_wdr_texture_can_still_be_exported_as_exact_dds(tmp_path):
    source = _write_wdr(tmp_path / "shop.wdr")
    destination = tmp_path / "shop_front.dds"

    export_wdr_texture(source, 0, destination)

    assert destination.read_bytes().startswith(b"DDS ")


def test_replace_wdr_texture_preserves_metadata_and_unrelated_bytes(
    monkeypatch, tmp_path
):
    import core.wdr_archive as module

    source = _write_wdr(tmp_path / "shop.wdr")
    destination = tmp_path / "shop-patched.wdr"
    image = tmp_path / "replacement.png"
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(image)

    class FormatValue:
        name = "BC1"

    class BCFormat:
        BC1 = FormatValue()

    class Encoded:
        width = 4
        height = 4
        format = FormatValue()
        mip_count = 1
        data = b"\x11\x22\x33\x44\x55\x66\x77\x88"

    class Texture:
        @staticmethod
        def from_image(*_args, **_kwargs):
            return Encoded()

    monkeypatch.setattr(
        module,
        "_load_texfury_encoder",
        lambda: type("Encoder", (), {"BCFormat": BCFormat, "Texture": Texture})(),
    )

    result = module.replace_wdr_texture_from_image(
        source,
        0,
        image,
        destination,
    )

    original = read_wdr(source)
    patched = read_wdr(destination)
    assert result.texture.name == "shop_front"
    assert patched.textures[0].data == Encoded.data
    assert patched.textures[0].data != original.textures[0].data
    assert patched.textures[0].name == original.textures[0].name
    assert patched.header == original.header
