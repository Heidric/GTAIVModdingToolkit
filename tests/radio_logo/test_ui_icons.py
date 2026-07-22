from pathlib import Path

from PIL import Image

from core.radio_logo.ui_icons import (
    _display_icon_image,
    resolve_station_icon_path,
    station_icon_candidates,
)
from core.radio_logo.wtd import WTDTexture


def _texture_from_dds_payload(data: bytes) -> WTDTexture:
    return WTDTexture(
        index=0,
        hash=0,
        name="vladivostok_col",
        raw_name="pack:/vladivostok_col.dds",
        width=4,
        height=4,
        format_code=21,
        format_name="A8R8G8B8",
        stride=16,
        texture_type=1,
        mip_count=1,
        data_offset=0,
        data_size=len(data),
        data=data,
    )


def test_display_icon_masks_black_background():
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
    image.putpixel((1, 1), (255, 255, 255, 255))
    bgra = image.tobytes("raw", "BGRA")

    result = _display_icon_image(_texture_from_dds_payload(bgra))

    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((1, 1))[3] == 255


def test_station_icon_candidates_cover_known_archive_aliases():
    assert "vladivostok" in station_icon_candidates("radio_vladivostok")
    assert station_icon_candidates("radio_broker")[0] == "radiobroker"
    assert station_icon_candidates("radio_jazz_nation")[0] == "jnr"
    assert station_icon_candidates("radio_vcfm")[0] == "vicecityfm"


def test_resolve_station_icon_path_uses_alias(tmp_path: Path):
    icon = tmp_path / "radiobroker.png"
    icon.write_bytes(b"png")

    assert resolve_station_icon_path("radio_broker", {"radiobroker": icon}) == icon
