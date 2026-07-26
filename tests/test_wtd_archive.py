import io
from pathlib import Path

import pytest
from PIL import Image

import core.wtd_archive as wtd_archive
from core.radio_logo.payload_patcher import IndexedPayloadPatchResult
from core.radio_logo.wtd import WTDArchive, WTDHeader, WTDParseError, WTDTexture
from core.wtd_archive import (
    WTDArchiveSnapshot,
    WTDTextureEntry,
    WTDTextureNotFoundError,
    WTDTexturePreviewError,
    WTDTextureReplacementError,
    export_wtd_texture,
    inspect_wtd_archive,
    render_wtd_texture_preview,
    replace_wtd_texture_from_image,
)


def _texture(
    *,
    index: int,
    name: str,
    format_code: int = 21,
    format_name: str = "A8R8G8B8",
    width: int = 2,
    height: int = 2,
    data: bytes | None = None,
) -> WTDTexture:
    if data is None and format_code == 21:
        data = bytes(
            [
                0,
                0,
                255,
                255,
                0,
                255,
                0,
                255,
                255,
                0,
                0,
                255,
                255,
                255,
                255,
                255,
            ]
        )
    return WTDTexture(
        index=index,
        hash=0x11111111 + index,
        name=name,
        raw_name=f"pack:/{name}.dds",
        width=width,
        height=height,
        format_code=format_code,
        format_name=format_name,
        stride=width * 4,
        texture_type=1,
        mip_count=1,
        data_offset=0 if data is not None else None,
        data_size=len(data) if data is not None else None,
        data=data,
    )


def _archive(path: Path, *textures: WTDTexture) -> WTDArchive:
    return WTDArchive(
        path=path.resolve(),
        header=WTDHeader(
            resource_type=8,
            flags=0x1234,
            virtual_size=0x1000,
            physical_size=0x2000,
            texture_count=len(textures),
        ),
        textures=tuple(textures),
    )


@pytest.fixture
def source(tmp_path: Path, monkeypatch):
    path = tmp_path / "textures.wtd"
    path.write_bytes(b"synthetic")
    archive = _archive(
        path,
        _texture(index=0, name="hud_icon"),
        _texture(
            index=1,
            name="unsupported",
            format_code=0xDEADBEEF,
            format_name="UNKNOWN_0xDEADBEEF",
            data=None,
        ),
        _texture(index=2, name="HUD_ICON"),
    )
    monkeypatch.setattr(wtd_archive, "read_wtd", lambda candidate: archive)
    return path, archive


def test_inspect_wtd_archive_returns_payload_free_metadata(source):
    path, _archive_value = source

    snapshot = inspect_wtd_archive(path)

    assert snapshot == WTDArchiveSnapshot(
        path=path.resolve(),
        resource_type=8,
        flags=0x1234,
        virtual_size=0x1000,
        physical_size=0x2000,
        textures=(
            WTDTextureEntry(
                index=0,
                hash=0x11111111,
                name="hud_icon",
                raw_name="pack:/hud_icon.dds",
                width=2,
                height=2,
                format_code=21,
                format_name="A8R8G8B8",
                stride=8,
                texture_type=1,
                mip_count=1,
                data_size=16,
                extractable=True,
                replaceable=True,
            ),
            WTDTextureEntry(
                index=1,
                hash=0x11111112,
                name="unsupported",
                raw_name="pack:/unsupported.dds",
                width=2,
                height=2,
                format_code=0xDEADBEEF,
                format_name="UNKNOWN_0xDEADBEEF",
                stride=8,
                texture_type=1,
                mip_count=1,
                data_size=None,
                extractable=False,
                replaceable=False,
            ),
            WTDTextureEntry(
                index=2,
                hash=0x11111113,
                name="HUD_ICON",
                raw_name="pack:/HUD_ICON.dds",
                width=2,
                height=2,
                format_code=21,
                format_name="A8R8G8B8",
                stride=8,
                texture_type=1,
                mip_count=1,
                data_size=16,
                extractable=True,
                replaceable=True,
            ),
        ),
    )


def test_inspection_distinguishes_extractable_from_replaceable(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "textures.wtd"
    path.write_bytes(b"synthetic")
    archive = _archive(
        path,
        _texture(
            index=0,
            name="dxt3_texture",
            format_code=0x33545844,
            format_name="DXT3",
            width=4,
            height=4,
            data=b"X" * 16,
        ),
    )
    monkeypatch.setattr(wtd_archive, "read_wtd", lambda candidate: archive)

    texture = inspect_wtd_archive(path).texture(0)

    assert texture.extractable is True
    assert texture.replaceable is False


def test_snapshot_lookup_uses_stable_index_and_allows_duplicate_names(source):
    path, _archive_value = source
    snapshot = inspect_wtd_archive(path)

    assert snapshot.texture(2).name == "HUD_ICON"
    assert [item.index for item in snapshot.textures_named("hud_icon")] == [0, 2]

    with pytest.raises(WTDTextureNotFoundError):
        snapshot.texture(99)
    with pytest.raises(TypeError, match="index must be an integer"):
        snapshot.texture(True)


def test_export_wtd_texture_writes_selected_dds(source, tmp_path):
    path, _archive_value = source
    destination = tmp_path / "export" / "custom.dds"

    result = export_wtd_texture(path, 0, destination)

    assert result == destination.resolve()
    assert destination.read_bytes().startswith(b"DDS ")


def test_export_wtd_texture_refuses_overwrite_by_default(source, tmp_path):
    path, _archive_value = source
    destination = tmp_path / "existing.dds"
    destination.write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        export_wtd_texture(path, 0, destination)

    assert destination.read_bytes() == b"keep"


def test_export_wtd_texture_overwrites_atomically(source, tmp_path):
    path, _archive_value = source
    destination = tmp_path / "existing.dds"
    destination.write_bytes(b"old")

    export_wtd_texture(path, 0, destination, overwrite=True)

    assert destination.read_bytes().startswith(b"DDS ")
    assert not list(tmp_path.glob(".existing.dds.*.tmp"))


def test_export_wtd_texture_rejects_unsupported_payload(source, tmp_path):
    path, _archive_value = source

    with pytest.raises(WTDParseError, match="unsupported format"):
        export_wtd_texture(path, 1, tmp_path / "unsupported.dds")


def test_render_wtd_texture_preview_returns_bounded_png(source):
    path, _archive_value = source

    preview = render_wtd_texture_preview(path, 0, max_dimension=1)

    assert preview.texture.index == 0
    assert preview.png_data.startswith(b"\x89PNG\r\n\x1a\n")
    assert (preview.width, preview.height) == (1, 1)
    with Image.open(io.BytesIO(preview.png_data)) as image:
        assert image.mode == "RGBA"
        assert image.size == (1, 1)


def test_render_wtd_texture_preview_wraps_decoder_errors(source, monkeypatch):
    path, _archive_value = source

    def fail_open(_stream):
        raise OSError("decoder failed")

    monkeypatch.setattr(wtd_archive.Image, "open", fail_open)

    with pytest.raises(WTDTexturePreviewError, match="cannot be decoded"):
        render_wtd_texture_preview(path, 0)


def test_render_wtd_texture_preview_validates_size(source):
    path, _archive_value = source

    with pytest.raises(ValueError, match="greater than zero"):
        render_wtd_texture_preview(path, 0, max_dimension=0)


def test_operations_reject_missing_wtd(tmp_path):
    missing = tmp_path / "missing.wtd"

    with pytest.raises(FileNotFoundError):
        inspect_wtd_archive(missing)


def test_replace_wtd_texture_from_image_uses_stable_index(source, tmp_path, monkeypatch):
    path, archive = source
    image = tmp_path / "replacement.png"
    image.write_bytes(b"image")
    destination = tmp_path / "patched.wtd"
    captured = {}

    def fake_replace(source_path, output_path, texture_index, image_path, **kwargs):
        captured.update(
            source=Path(source_path),
            output=Path(output_path),
            texture_index=texture_index,
            image=Path(image_path),
            kwargs=kwargs,
        )
        destination.write_bytes(b"patched")
        return IndexedPayloadPatchResult(
            source_path=path.resolve(),
            output_path=destination.resolve(),
            texture_index=texture_index,
            texture_name=archive.textures[texture_index].name,
            texture_count=len(archive.textures),
            output_size=destination.stat().st_size,
            output_sha256="output-sha256",
            virtual_sha256="virtual-sha256",
        )

    monkeypatch.setattr(
        wtd_archive,
        "replace_texture_payload_by_index_from_image",
        fake_replace,
    )

    result = replace_wtd_texture_from_image(
        path,
        2,
        image,
        destination,
        quality=0.75,
    )

    assert captured["texture_index"] == 2
    assert captured["source"] == path.resolve()
    assert captured["output"] == destination.resolve()
    assert captured["image"] == image.resolve()
    assert captured["kwargs"] == {"quality": 0.75, "overwrite": False}
    assert result.texture.index == 2
    assert result.texture.name == "HUD_ICON"
    assert result.replacement_image_path == image.resolve()
    assert result.output_path == destination.resolve()
    assert result.output_sha256 == "output-sha256"


def test_replace_wtd_texture_from_image_rejects_unsupported_format(
    source,
    tmp_path,
):
    path, _archive_value = source
    image = tmp_path / "replacement.png"
    image.write_bytes(b"image")

    with pytest.raises(WTDTextureReplacementError, match="cannot be replaced"):
        replace_wtd_texture_from_image(
            path,
            1,
            image,
            tmp_path / "patched.wtd",
        )


def test_replace_wtd_texture_from_image_rejects_missing_image(source, tmp_path):
    path, _archive_value = source

    with pytest.raises(FileNotFoundError, match="replacement image"):
        replace_wtd_texture_from_image(
            path,
            0,
            tmp_path / "missing.png",
            tmp_path / "patched.wtd",
        )
