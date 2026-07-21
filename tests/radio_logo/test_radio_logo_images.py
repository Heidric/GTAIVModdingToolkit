from pathlib import Path

import pytest
from PIL import Image

from core.radio_logo.images import (
    LogoFitMode,
    LogoImageError,
    format_logo_requirements,
    inspect_logo_image,
    prepare_logo_image,
    reduced_aspect_ratio,
)


def _save(path: Path, size=(64, 32), color=(255, 0, 0, 255), mode="RGBA") -> Path:
    Image.new(mode, size, color).save(path)
    return path


def test_inspect_logo_image_reports_dimensions_and_transparency(tmp_path):
    source = _save(tmp_path / "logo.png", color=(255, 0, 0, 128))

    info = inspect_logo_image(source)

    assert (info.width, info.height) == (64, 32)
    assert info.mode == "RGBA"
    assert info.has_transparency is True
    assert info.aspect_ratio == 2.0
    assert info.file_size > 0


def test_inspect_logo_image_rejects_unsupported_extension(tmp_path):
    source = tmp_path / "logo.txt"
    source.write_text("not an image", encoding="utf-8")

    with pytest.raises(LogoImageError, match="Unsupported logo image extension"):
        inspect_logo_image(source)


def test_inspect_logo_image_rejects_invalid_image_bytes(tmp_path):
    source = tmp_path / "logo.png"
    source.write_bytes(b"not a png")

    with pytest.raises(LogoImageError, match="Could not decode"):
        inspect_logo_image(source)


def test_reduced_aspect_ratio():
    assert reduced_aspect_ratio(256, 128) == (2, 1)
    assert reduced_aspect_ratio(128, 128) == (1, 1)


def test_format_logo_requirements_contains_dynamic_target_data():
    text = format_logo_requirements(256, 128)

    assert "Required canvas: 256 x 128 px" in text
    assert "Aspect ratio: 2:1" in text
    assert "PNG or WebP with transparency" in text
    assert "at least 256 x 128 px" in text


def test_fit_preserves_aspect_ratio_and_adds_transparent_padding(tmp_path):
    source = _save(tmp_path / "wide.png", size=(100, 50))
    output = tmp_path / "prepared.png"

    result = prepare_logo_image(source, output, 100, 100, fit_mode=LogoFitMode.FIT)

    with Image.open(output) as image:
        assert image.mode == "RGBA"
        assert image.size == (100, 100)
        assert image.getpixel((50, 50)) == (255, 0, 0, 255)
        assert image.getpixel((50, 0))[3] == 0
        assert image.getpixel((50, 99))[3] == 0
    assert result.fit_mode is LogoFitMode.FIT
    assert len(result.sha256) == 64


def test_fit_applies_requested_safe_padding(tmp_path):
    source = _save(tmp_path / "square.png", size=(100, 100))
    output = tmp_path / "prepared.png"

    prepare_logo_image(source, output, 100, 100, padding_ratio=0.1)

    with Image.open(output) as image:
        assert image.getpixel((5, 50))[3] == 0
        assert image.getpixel((10, 50))[3] == 255
        assert image.getpixel((89, 50))[3] == 255
        assert image.getpixel((95, 50))[3] == 0


def test_fill_preserves_aspect_ratio_and_crops_center(tmp_path):
    source = Image.new("RGBA", (100, 50), (255, 0, 0, 255))
    source.paste((0, 0, 255, 255), (0, 0, 25, 50))
    source_path = tmp_path / "wide.png"
    source.save(source_path)
    output = tmp_path / "prepared.png"

    prepare_logo_image(source_path, output, 50, 50, fit_mode="fill")

    with Image.open(output) as image:
        assert image.size == (50, 50)
        assert image.getpixel((25, 25)) == (255, 0, 0, 255)
        assert image.getpixel((0, 25)) == (255, 0, 0, 255)


def test_stretch_resizes_directly_to_target(tmp_path):
    source = _save(tmp_path / "wide.png", size=(100, 50))
    output = tmp_path / "prepared.png"

    result = prepare_logo_image(source, output, 32, 64, fit_mode="stretch")

    with Image.open(output) as image:
        assert image.size == (32, 64)
    assert result.width == 32
    assert result.height == 64


def test_prepare_creates_output_parent_and_preserves_alpha(tmp_path):
    source = _save(tmp_path / "alpha.webp", size=(16, 16), color=(10, 20, 30, 0))
    output = tmp_path / "nested" / "logo.png"

    prepare_logo_image(source, output, 32, 32)

    with Image.open(output) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((16, 16))[3] == 0


def test_prepare_rejects_non_png_output(tmp_path):
    source = _save(tmp_path / "logo.png")

    with pytest.raises(ValueError, match="must use the .png extension"):
        prepare_logo_image(source, tmp_path / "logo.dds", 64, 64)


def test_prepare_rejects_invalid_dimensions(tmp_path):
    source = _save(tmp_path / "logo.png")

    with pytest.raises(ValueError, match="width must be a positive integer"):
        prepare_logo_image(source, tmp_path / "prepared.png", 0, 64)


def test_prepare_rejects_padding_outside_fit_mode(tmp_path):
    source = _save(tmp_path / "logo.png")

    with pytest.raises(ValueError, match="only supported with fit mode"):
        prepare_logo_image(
            source,
            tmp_path / "prepared.png",
            64,
            64,
            fit_mode="fill",
            padding_ratio=0.1,
        )


def test_prepare_rejects_source_overwrite(tmp_path):
    source = _save(tmp_path / "logo.png")

    with pytest.raises(ValueError, match="must not overwrite"):
        prepare_logo_image(source, source, 64, 64)
