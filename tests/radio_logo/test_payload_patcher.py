from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.radio_logo.payload_patcher as payload_patcher
from core.radio_logo.payload_patcher import (
    TextureDictionaryError,
    TextureDictionaryValidationError,
    replace_texture_payload_from_image,
    replace_texture_payloads_from_images,
)
from core.radio_logo.wtd import PHYSICAL_BASE, RSC5_MAGIC, VIRTUAL_BASE, read_wtd


def _encode_size(size: int, base_shift: int, exponent_shift: int) -> int:
    for exponent in range(15, -1, -1):
        unit = 1 << (exponent + 8)
        if size % unit == 0 and size // unit <= 0x7FF:
            return (size // unit) << base_shift | exponent << exponent_shift
    raise ValueError(size)


def _data_size(width: int, height: int, format_code: int) -> int:
    if format_code == 0x31545844:
        block_bytes = 8
    elif format_code in (0x33545844, 0x35545844):
        block_bytes = 16
    elif format_code == 21:
        return width * height * 4
    else:
        block_bytes = 8
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_bytes


def build_wtd(path: Path, textures: list[dict]) -> Path:
    virtual_size = 0x1000
    physical_size = 0x1000
    virtual = bytearray([0xA5]) * virtual_size
    physical = bytearray([0xCC]) * physical_size

    count = len(textures)
    hash_offset = 32
    pointer_offset = hash_offset + count * 4
    texture_offset = pointer_offset + count * 4
    names_offset = texture_offset + count * 80

    name_offsets: list[int] = []
    cursor = names_offset
    for texture in textures:
        raw_name = f"pack:/{texture['name']}.dds".encode() + b"\0"
        name_offsets.append(cursor)
        virtual[cursor : cursor + len(raw_name)] = raw_name
        cursor += len(raw_name)

    struct.pack_into("<IIII", virtual, 0, 0x11223344, 0x55667788, 0, 7)
    struct.pack_into(
        "<IHHIHH",
        virtual,
        16,
        VIRTUAL_BASE + hash_offset,
        count,
        count,
        VIRTUAL_BASE + pointer_offset,
        count,
        count,
    )

    physical_cursor = 64
    for index, texture in enumerate(textures):
        struct.pack_into("<I", virtual, hash_offset + index * 4, 0x1000 + index)
        entry_offset = texture_offset + index * 80
        struct.pack_into(
            "<I",
            virtual,
            pointer_offset + index * 4,
            VIRTUAL_BASE + entry_offset,
        )

        width = texture.get("width", 4)
        height = texture.get("height", 4)
        format_code = texture.get("format_code", 0x31545844)
        size = _data_size(width, height, format_code)
        payload = texture.get("payload", bytes([index + 1]) * size)
        assert len(payload) == size
        physical[physical_cursor : physical_cursor + size] = payload

        struct.pack_into("<I", virtual, entry_offset, 0x00D50104)
        struct.pack_into("<I", virtual, entry_offset + 4, 0xCAFEBABE)
        struct.pack_into("<I", virtual, entry_offset + 20, VIRTUAL_BASE + name_offsets[index])
        struct.pack_into("<HH", virtual, entry_offset + 28, width, height)
        struct.pack_into("<I", virtual, entry_offset + 32, format_code)
        struct.pack_into("<H", virtual, entry_offset + 36, width)
        virtual[entry_offset + 38] = 1
        virtual[entry_offset + 39] = 1
        struct.pack_into("<I", virtual, entry_offset + 72, PHYSICAL_BASE + physical_cursor)
        struct.pack_into("<I", virtual, entry_offset + 76, 0x12345678)
        physical_cursor += size + 16

    flags = _encode_size(virtual_size, 0, 11) | _encode_size(
        physical_size, 15, 26
    )
    resource = bytes(virtual + physical)
    path.write_bytes(
        struct.pack("<III", RSC5_MAGIC, 8, flags) + zlib.compress(resource)
    )
    return path


def sections(path: Path) -> tuple[bytes, bytes, bytes]:
    data = path.read_bytes()
    _, _, flags = struct.unpack_from("<III", data, 0)
    virtual_size = ((flags >> 0) & 0x7FF) << (((flags >> 11) & 0xF) + 8)
    physical_size = ((flags >> 15) & 0x7FF) << (((flags >> 26) & 0xF) + 8)
    resource = zlib.decompress(data[12:])
    assert len(resource) == virtual_size + physical_size
    return data[:12], resource[:virtual_size], resource[virtual_size:]


class FakeFormat:
    def __init__(self, name: str):
        self.name = name


class FakeBCFormat:
    BC1 = FakeFormat("BC1")
    BC3 = FakeFormat("BC3")
    A8R8G8B8 = FakeFormat("A8R8G8B8")


def install_fake_encoder(monkeypatch, payloads: dict[str, bytes], *, mutate=None):
    captures: list[dict] = []

    class FakeTextureFactory:
        @staticmethod
        def from_image(path, **kwargs):
            captures.append({"path": Path(path), **kwargs})
            width, height = kwargs["resize"]
            result = SimpleNamespace(
                width=width,
                height=height,
                format=kwargs["format"],
                mip_count=1,
                data=payloads[Path(path).name],
            )
            if mutate is not None:
                mutate(result)
            return result

    monkeypatch.setattr(
        payload_patcher,
        "_load_texfury_encoder",
        lambda: SimpleNamespace(BCFormat=FakeBCFormat, Texture=FakeTextureFactory),
    )
    return captures


def write_image(path: Path) -> Path:
    path.write_bytes(b"image")
    return path


def test_single_payload_patch_preserves_header_and_virtual(monkeypatch, tmp_path):
    source = build_wtd(
        tmp_path / "source.wtd",
        [
            {"name": "beat_col", "payload": b"A" * 8},
            {"name": "other_col", "payload": b"B" * 8},
        ],
    )
    image = write_image(tmp_path / "color.png")
    output = tmp_path / "output.wtd"
    captures = install_fake_encoder(monkeypatch, {"color.png": b"N" * 8})

    result = replace_texture_payload_from_image(
        source,
        output,
        "BEAT_COL",
        image,
    )

    source_header, source_virtual, source_physical = sections(source)
    output_header, output_virtual, output_physical = sections(output)
    source_archive = read_wtd(source)
    output_archive = read_wtd(output)
    target = next(t for t in source_archive.textures if t.name == "beat_col")
    other_before = next(t for t in source_archive.textures if t.name == "other_col")
    other_after = next(t for t in output_archive.textures if t.name == "other_col")

    assert output_header == source_header
    assert output_virtual == source_virtual
    assert output_physical[: target.data_offset] == source_physical[: target.data_offset]
    assert output_physical[target.data_offset : target.data_offset + 8] == b"N" * 8
    assert output_physical[target.data_offset + 8 :] == source_physical[target.data_offset + 8 :]
    assert other_after.data == other_before.data
    assert result.replaced_textures == ("beat_col",)
    assert result.comparison.payload_changed == ("beat_col",)
    assert result.virtual_sha256 == hashlib.sha256(source_virtual).hexdigest()
    assert captures[0]["format"].name == "BC1"


def test_multiple_payloads_are_patched_in_one_resource(monkeypatch, tmp_path):
    source = build_wtd(
        tmp_path / "source.wtd",
        [
            {"name": "beat_col", "payload": b"A" * 8},
            {
                "name": "beat_bw",
                "format_code": 0x35545844,
                "payload": b"B" * 16,
            },
            {"name": "untouched", "payload": b"C" * 8},
        ],
    )
    color = write_image(tmp_path / "color.png")
    bw = write_image(tmp_path / "bw.png")
    output = tmp_path / "output.wtd"
    captures = install_fake_encoder(
        monkeypatch,
        {"color.png": b"X" * 8, "bw.png": b"Y" * 16},
    )

    result = replace_texture_payloads_from_images(
        source,
        output,
        {"beat_col": color, "beat_bw": bw},
    )

    textures = {texture.name: texture for texture in read_wtd(output).textures}
    assert textures["beat_col"].data == b"X" * 8
    assert textures["beat_bw"].data == b"Y" * 16
    assert textures["untouched"].data == b"C" * 8
    assert result.replaced_textures == ("beat_col", "beat_bw")
    assert result.comparison.payload_changed == ("beat_bw", "beat_col")
    assert [capture["format"].name for capture in captures] == ["BC1", "BC3"]


def test_patch_preserves_all_bytes_for_identical_payload(monkeypatch, tmp_path):
    source = build_wtd(
        tmp_path / "source.wtd",
        [{"name": "beat_col", "payload": b"A" * 8}],
    )
    image = write_image(tmp_path / "color.png")
    output = tmp_path / "output.wtd"
    install_fake_encoder(monkeypatch, {"color.png": b"A" * 8})

    result = replace_texture_payload_from_image(source, output, "beat_col", image)

    assert sections(output) == sections(source)
    assert result.comparison.payload_changed == ()


def test_patch_rejects_wrong_encoded_payload_size(monkeypatch, tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "beat_col"}])
    image = write_image(tmp_path / "color.png")
    install_fake_encoder(monkeypatch, {"color.png": b"too-long!"})

    with pytest.raises(TextureDictionaryValidationError, match="payload size"):
        replace_texture_payload_from_image(
            source,
            tmp_path / "output.wtd",
            "beat_col",
            image,
        )


def test_patch_rejects_encoder_metadata_change(monkeypatch, tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "beat_col"}])
    image = write_image(tmp_path / "color.png")

    def mutate(texture):
        texture.width = 8

    install_fake_encoder(monkeypatch, {"color.png": b"N" * 8}, mutate=mutate)

    with pytest.raises(TextureDictionaryValidationError, match="metadata mismatch"):
        replace_texture_payload_from_image(
            source,
            tmp_path / "output.wtd",
            "beat_col",
            image,
        )


def test_patch_reports_missing_texture(monkeypatch, tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "beat_col"}])
    image = write_image(tmp_path / "color.png")
    install_fake_encoder(monkeypatch, {"color.png": b"N" * 8})

    with pytest.raises(KeyError, match="not present"):
        replace_texture_payload_from_image(
            source,
            tmp_path / "output.wtd",
            "missing",
            image,
        )


def test_patch_rejects_unsupported_texture_format(monkeypatch, tmp_path):
    source = build_wtd(
        tmp_path / "source.wtd",
        [{"name": "beat_col", "format_code": 0x33545844, "payload": b"A" * 16}],
    )
    image = write_image(tmp_path / "color.png")
    install_fake_encoder(monkeypatch, {"color.png": b"N" * 16})

    with pytest.raises(TextureDictionaryError, match="supported formats"):
        replace_texture_payload_from_image(
            source,
            tmp_path / "output.wtd",
            "beat_col",
            image,
        )


def test_patch_refuses_existing_output(monkeypatch, tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "beat_col"}])
    image = write_image(tmp_path / "color.png")
    output = tmp_path / "output.wtd"
    output.write_bytes(b"existing")
    install_fake_encoder(monkeypatch, {"color.png": b"N" * 8})

    with pytest.raises(FileExistsError):
        replace_texture_payload_from_image(source, output, "beat_col", image)
    assert output.read_bytes() == b"existing"


def test_patch_rejects_same_source_and_output(monkeypatch, tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "beat_col"}])
    image = write_image(tmp_path / "color.png")
    install_fake_encoder(monkeypatch, {"color.png": b"N" * 8})

    with pytest.raises(ValueError, match="must be different"):
        replace_texture_payload_from_image(
            source,
            source,
            "beat_col",
            image,
            overwrite=True,
        )


def test_patch_rejects_empty_replacements(tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "beat_col"}])

    with pytest.raises(ValueError, match="at least one"):
        replace_texture_payloads_from_images(
            source,
            tmp_path / "output.wtd",
            {},
        )


def test_patch_rejects_case_duplicate_replacements(tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "beat_col"}])
    first = write_image(tmp_path / "first.png")
    second = write_image(tmp_path / "second.png")

    with pytest.raises(ValueError, match="differ only by case"):
        replace_texture_payloads_from_images(
            source,
            tmp_path / "output.wtd",
            {"beat_col": first, "BEAT_COL": second},
        )


def test_patch_rejects_invalid_quality(tmp_path):
    with pytest.raises(ValueError, match="quality"):
        replace_texture_payloads_from_images(
            tmp_path / "missing.wtd",
            tmp_path / "output.wtd",
            {"beat_col": tmp_path / "missing.png"},
            quality=1.1,
        )
