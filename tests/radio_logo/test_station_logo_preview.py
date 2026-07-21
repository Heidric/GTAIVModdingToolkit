from pathlib import Path

import pytest
from PIL import Image

from core.radio_logo.images import LogoFitMode
from core.radio_logo.installer import RadioLogoTarget
from core.radio_logo.station_pack import (
    StationLogoPlan,
    TextureCanvas,
    prepare_station_logo_previews,
)


def _plan(tmp_path: Path) -> StationLogoPlan:
    return StationLogoPlan(
        game_root=tmp_path,
        target=RadioLogoTarget.GTA_IV,
        station_base="testfm",
        color_texture_name="testfm_col",
        noncolored_texture_name="testfm_bw",
        color_canvas=TextureCanvas(256, 128, "DXT1", 1),
        noncolored_canvas=TextureCanvas(128, 64, "DXT5", 1),
        wtd_files=(),
    )


def _transparent_source(path: Path) -> None:
    image = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    for x in range(100, 300):
        for y in range(50, 150):
            image.putpixel((x, y), (220, 60, 40, 255))
    image.save(path)


def test_prepare_station_logo_previews_matches_hud_semantics(tmp_path):
    source = tmp_path / "source.png"
    _transparent_source(source)

    result = prepare_station_logo_previews(
        _plan(tmp_path),
        source,
        tmp_path / "preview",
        fit_mode=LogoFitMode.FIT,
    )

    assert result.color_preview_path.name == "testfm_col.png"
    assert result.noncolored_preview_path.name == "testfm_bw.png"

    with Image.open(result.color_preview_path) as color:
        assert color.mode == "RGB"
        assert color.size == (256, 128)
        assert color.getpixel((0, 0)) == (0, 0, 0)
        assert color.getpixel((128, 64))[0] > 0

    with Image.open(result.noncolored_preview_path) as noncolored:
        rgba = noncolored.convert("RGBA")
        assert rgba.size == (128, 64)
        assert rgba.getpixel((0, 0))[3] == 0
        center = rgba.getpixel((64, 32))
        assert center[3] == 255
        assert center[0] == center[1] == center[2]


def test_prepare_station_logo_previews_rejects_existing_outputs(tmp_path):
    source = tmp_path / "source.png"
    _transparent_source(source)
    output = tmp_path / "preview"

    prepare_station_logo_previews(_plan(tmp_path), source, output)

    with pytest.raises(FileExistsError):
        prepare_station_logo_previews(_plan(tmp_path), source, output)


def test_prepare_station_logo_previews_can_overwrite_outputs(tmp_path):
    source = tmp_path / "source.png"
    _transparent_source(source)
    output = tmp_path / "preview"

    first = prepare_station_logo_previews(_plan(tmp_path), source, output)
    first_color = first.color_preview_path.read_bytes()

    with Image.open(source) as opened:
        replacement = opened.convert("RGBA")
    replacement.paste((40, 180, 220, 255), (100, 50, 300, 150))
    replacement.save(source)

    second = prepare_station_logo_previews(
        _plan(tmp_path),
        source,
        output,
        overwrite=True,
    )

    assert second.color_preview_path.read_bytes() != first_color
