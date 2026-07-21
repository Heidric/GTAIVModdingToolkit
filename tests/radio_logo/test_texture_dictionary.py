import builtins
import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.radio_logo.texture_dictionary as texture_dictionary
from core.radio_logo.experimental import (
    EXPERIMENTAL_WTD_ENVIRONMENT_VARIABLE,
    ExperimentalWtdRebuildDisabledError,
)
from core.radio_logo.texture_dictionary import (
    TexfuryUnavailableError,
    TextureDictionaryError,
    TextureDictionaryValidationError,
    archive_signatures,
    compare_wtd_files,
    main,
    replace_texture_from_image,
    round_trip_wtd,
)
from core.radio_logo.wtd import PHYSICAL_BASE, RSC5_MAGIC, VIRTUAL_BASE, read_wtd


def _encode_size(size: int, base_shift: int, exponent_shift: int) -> int:
    for exponent in range(15, -1, -1):
        unit = 1 << (exponent + 8)
        if size % unit == 0 and size // unit <= 0x7FF:
            return (size // unit) << base_shift | exponent << exponent_shift
    raise ValueError(size)


def _texture_data_size(width: int, height: int, format_code: int) -> int:
    block_bytes = 8 if format_code == 0x31545844 else 16
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_bytes


def build_wtd(path: Path, textures: list[dict]) -> Path:
    virtual_size = 0x1000
    physical_size = 0x1000
    virtual = bytearray(virtual_size)
    physical = bytearray(physical_size)

    count = len(textures)
    hash_offset = 32
    pointer_offset = hash_offset + count * 4
    texture_offset = pointer_offset + count * 4
    names_offset = texture_offset + count * 80

    name_offsets = []
    cursor = names_offset
    for texture in textures:
        raw_name = f"pack:/{texture['name']}.dds".encode() + b"\0"
        name_offsets.append(cursor)
        virtual[cursor : cursor + len(raw_name)] = raw_name
        cursor += len(raw_name)

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

    physical_cursor = 0
    for index, texture in enumerate(textures):
        struct.pack_into("<I", virtual, hash_offset + index * 4, index + 1)
        struct.pack_into(
            "<I",
            virtual,
            pointer_offset + index * 4,
            VIRTUAL_BASE + texture_offset + index * 80,
        )

        width = texture.get("width", 4)
        height = texture.get("height", 4)
        format_code = texture.get("format_code", 0x31545844)
        data_size = _texture_data_size(width, height, format_code)
        payload = texture.get("payload", bytes([index + 1]) * data_size)
        assert len(payload) == data_size
        physical[physical_cursor : physical_cursor + data_size] = payload

        entry_offset = texture_offset + index * 80
        struct.pack_into("<I", virtual, entry_offset, 0x00D50104)
        struct.pack_into(
            "<I", virtual, entry_offset + 20, VIRTUAL_BASE + name_offsets[index]
        )
        struct.pack_into("<HH", virtual, entry_offset + 28, width, height)
        struct.pack_into("<I", virtual, entry_offset + 32, format_code)
        struct.pack_into("<H", virtual, entry_offset + 36, width // 2)
        virtual[entry_offset + 38] = 1
        virtual[entry_offset + 39] = 1
        struct.pack_into(
            "<I", virtual, entry_offset + 72, PHYSICAL_BASE + physical_cursor
        )
        physical_cursor += data_size

    flags = _encode_size(virtual_size, 0, 11) | _encode_size(
        physical_size, 15, 26
    )
    payload = zlib.compress(bytes(virtual + physical))
    path.write_bytes(struct.pack("<III", RSC5_MAGIC, 8, flags) + payload)
    return path


@dataclass(frozen=True)
class FakeFormat:
    name: str


class FakeTexture:
    def __init__(
        self,
        name: str,
        *,
        width: int = 4,
        height: int = 4,
        format_name: str = "BC1",
        mip_count: int = 1,
    ):
        self.name = name
        self.width = width
        self.height = height
        self.format = FakeFormat(format_name)
        self.mip_count = mip_count


class FakeGame:
    GTA4 = "gta4"


def make_backend(
    source: Path,
    *,
    roundtrip_candidate: Path | None = None,
    replacement_candidate: Path | None = None,
    textures: list[FakeTexture] | None = None,
    save_error: Exception | None = None,
    replacement_factory=None,
):
    captures = {}
    source_textures = textures or [FakeTexture("radio_alpha")]

    class FakeDictionary:
        game = FakeGame.GTA4

        def __init__(self):
            self._textures = list(source_textures)
            self.replaced = False

        def get(self, name):
            for texture in self._textures:
                if texture.name.casefold() == name.casefold():
                    return texture
            raise KeyError(name)

        def names(self):
            return [texture.name for texture in self._textures]

        def replace(self, name, replacement):
            original = self.get(name)
            replacement.name = original.name
            self._textures[self._textures.index(original)] = replacement
            self.replaced = True

        def save(self, path):
            if save_error is not None:
                raise save_error
            candidate = (
                replacement_candidate if self.replaced else roundtrip_candidate
            ) or source
            Path(path).write_bytes(candidate.read_bytes())

    class FakeITD:
        @staticmethod
        def load(path):
            assert Path(path) == source.resolve()
            return FakeDictionary()

    class FakeTextureFactory:
        @staticmethod
        def from_image(path, **kwargs):
            captures["image"] = Path(path)
            captures["kwargs"] = kwargs
            if replacement_factory is not None:
                return replacement_factory(kwargs)
            return FakeTexture(
                kwargs["name"],
                width=kwargs["resize"][0],
                height=kwargs["resize"][1],
                format_name=kwargs["format"].name,
                mip_count=1 if not kwargs["generate_mipmaps"] else 2,
            )

    return (
        SimpleNamespace(Game=FakeGame, ITD=FakeITD, Texture=FakeTextureFactory),
        captures,
    )


@pytest.fixture(autouse=True)
def _enable_experimental_wtd_rebuild(monkeypatch):
    monkeypatch.setenv(EXPERIMENTAL_WTD_ENVIRONMENT_VARIABLE, "1")


def test_round_trip_requires_explicit_experimental_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv(EXPERIMENTAL_WTD_ENVIRONMENT_VARIABLE, raising=False)
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])

    with pytest.raises(ExperimentalWtdRebuildDisabledError, match="experimental"):
        round_trip_wtd(source, tmp_path / "output.wtd")

    assert not (tmp_path / "output.wtd").exists()


def test_archive_signatures_rejects_case_only_duplicates(tmp_path):
    archive = read_wtd(
        build_wtd(
            tmp_path / "duplicate.wtd",
            [{"name": "Logo"}, {"name": "logo"}],
        )
    )

    with pytest.raises(TextureDictionaryValidationError, match="differ only by case"):
        archive_signatures(archive)


def test_compare_identical_wtds(tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])
    candidate = tmp_path / "candidate.wtd"
    candidate.write_bytes(source.read_bytes())

    comparison = compare_wtd_files(source, candidate)

    assert comparison.valid
    assert comparison.identical
    assert comparison.payload_changed == ()


def test_compare_allows_selected_payload_change(tmp_path):
    source = build_wtd(
        tmp_path / "source.wtd",
        [{"name": "radio_alpha", "payload": b"\x10" * 8}],
    )
    candidate = build_wtd(
        tmp_path / "candidate.wtd",
        [{"name": "radio_alpha", "payload": b"\x20" * 8}],
    )

    comparison = compare_wtd_files(
        source,
        candidate,
        allowed_payload_changes=["RADIO_ALPHA"],
    )

    assert comparison.valid
    assert not comparison.identical
    assert comparison.payload_changed == ("radio_alpha",)
    assert comparison.unexpected_payload_changed == ()


def test_compare_reports_missing_and_extra_names(tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "old"}])
    candidate = build_wtd(tmp_path / "candidate.wtd", [{"name": "new"}])

    comparison = compare_wtd_files(source, candidate)

    assert comparison.missing == ("old",)
    assert comparison.extra == ("new",)
    assert not comparison.valid


def test_compare_reports_metadata_changes(tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "logo", "width": 4}])
    candidate = build_wtd(
        tmp_path / "candidate.wtd",
        [{"name": "logo", "width": 8, "payload": b"\x01" * 16}],
    )

    comparison = compare_wtd_files(source, candidate)

    assert comparison.metadata_changed == ("logo",)
    assert not comparison.valid


def test_load_texfury_reports_missing_dependency(monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "texfury":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(TexfuryUnavailableError, match="texfury 1.6.2"):
        texture_dictionary._load_texfury()


def test_round_trip_writes_validated_output(tmp_path, monkeypatch):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])
    output = tmp_path / "output.wtd"
    backend, _ = make_backend(source, roundtrip_candidate=source)
    monkeypatch.setattr(texture_dictionary, "_load_texfury", lambda: backend)

    result = round_trip_wtd(source, output)

    assert output.read_bytes() == source.read_bytes()
    assert result.texture_count == 1
    assert result.replaced_texture is None
    assert result.comparison.identical
    assert result.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()


def test_round_trip_refuses_existing_output(tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])
    output = tmp_path / "output.wtd"
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        round_trip_wtd(source, output)

    assert output.read_bytes() == b"existing"


def test_round_trip_rejects_same_source_and_output(tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])

    with pytest.raises(ValueError, match="must be different"):
        round_trip_wtd(source, source, overwrite=True)


def test_round_trip_cleans_staging_file_after_save_error(tmp_path, monkeypatch):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])
    output = tmp_path / "output.wtd"
    backend, _ = make_backend(source, save_error=RuntimeError("save failed"))
    monkeypatch.setattr(texture_dictionary, "_load_texfury", lambda: backend)

    with pytest.raises(RuntimeError, match="save failed"):
        round_trip_wtd(source, output)

    assert not output.exists()
    assert list(tmp_path.glob(".output.*.wtd.tmp")) == []


def test_round_trip_rejects_unexpected_payload_change(tmp_path, monkeypatch):
    source = build_wtd(
        tmp_path / "source.wtd",
        [{"name": "radio_alpha", "payload": b"\x10" * 8}],
    )
    changed = build_wtd(
        tmp_path / "changed.wtd",
        [{"name": "radio_alpha", "payload": b"\x20" * 8}],
    )
    output = tmp_path / "output.wtd"
    backend, _ = make_backend(source, roundtrip_candidate=changed)
    monkeypatch.setattr(texture_dictionary, "_load_texfury", lambda: backend)

    with pytest.raises(
        TextureDictionaryValidationError,
        match="unexpected_payload_changed",
    ):
        round_trip_wtd(source, output)

    assert not output.exists()


def test_replace_texture_preserves_target_metadata(tmp_path, monkeypatch):
    source = build_wtd(
        tmp_path / "source.wtd",
        [{"name": "radio_alpha", "payload": b"\x10" * 8}],
    )
    changed = build_wtd(
        tmp_path / "changed.wtd",
        [{"name": "radio_alpha", "payload": b"\x20" * 8}],
    )
    image = tmp_path / "logo.png"
    image.write_bytes(b"image")
    output = tmp_path / "output.wtd"
    backend, captures = make_backend(
        source,
        replacement_candidate=changed,
        textures=[FakeTexture("radio_alpha", format_name="BC1")],
    )
    monkeypatch.setattr(texture_dictionary, "_load_texfury", lambda: backend)

    result = replace_texture_from_image(
        source,
        output,
        "RADIO_ALPHA",
        image,
        quality=0.8,
    )

    kwargs = captures["kwargs"]
    assert kwargs["resize"] == (4, 4)
    assert kwargs["format"] == FakeFormat("BC1")
    assert kwargs["generate_mipmaps"] is False
    assert kwargs["name"] == "radio_alpha"
    assert result.replaced_texture == "radio_alpha"
    assert result.comparison.payload_changed == ("radio_alpha",)


def test_replace_texture_reports_missing_name(tmp_path, monkeypatch):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])
    image = tmp_path / "logo.png"
    image.write_bytes(b"image")
    backend, _ = make_backend(source)
    monkeypatch.setattr(texture_dictionary, "_load_texfury", lambda: backend)

    with pytest.raises(KeyError, match="available: radio_alpha"):
        replace_texture_from_image(
            source,
            tmp_path / "output.wtd",
            "missing",
            image,
        )


def test_replace_texture_rejects_unsupported_source_format(tmp_path, monkeypatch):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])
    image = tmp_path / "logo.png"
    image.write_bytes(b"image")
    backend, _ = make_backend(
        source,
        textures=[FakeTexture("radio_alpha", format_name="BC2")],
    )
    monkeypatch.setattr(texture_dictionary, "_load_texfury", lambda: backend)

    with pytest.raises(TextureDictionaryError, match="cannot encode replacement"):
        replace_texture_from_image(
            source,
            tmp_path / "output.wtd",
            "radio_alpha",
            image,
        )


def test_replace_texture_rejects_invalid_arguments(tmp_path):
    source = build_wtd(tmp_path / "source.wtd", [{"name": "radio_alpha"}])
    image = tmp_path / "logo.png"
    image.write_bytes(b"image")

    with pytest.raises(ValueError, match="must not be empty"):
        replace_texture_from_image(source, tmp_path / "a.wtd", " ", image)
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        replace_texture_from_image(
            source,
            tmp_path / "b.wtd",
            "radio_alpha",
            image,
            quality=1.1,
        )


def test_cli_roundtrip_prints_result(tmp_path, monkeypatch, capsys):
    output = tmp_path / "output.wtd"
    output.write_bytes(b"result")
    comparison = SimpleNamespace(payload_changed=())
    result = SimpleNamespace(
        output_path=output,
        texture_count=40,
        output_size=6,
        output_sha256="abc",
        replaced_texture=None,
        comparison=comparison,
    )
    monkeypatch.setattr(texture_dictionary, "round_trip_wtd", lambda *a, **k: result)

    exit_code = main(["roundtrip", "source.wtd", str(output), "--overwrite"])

    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "Textures: 40" in printed
    assert "Payload changes: []" in printed
