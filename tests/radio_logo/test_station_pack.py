from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import core.radio_logo.station_pack as station_pack
from core.radio_logo.installer import RadioLogoTarget
from core.radio_logo.station_pack import (
    StationLogoPackError,
    build_station_logo_pack,
    create_station_logo_plan,
    list_station_logo_bases,
    main,
    normalise_station_base,
)
from core.radio_logo.wtd import WTDArchive, WTDHeader, WTDTexture


def make_texture(
    name: str,
    *,
    width: int | None = None,
    height: int | None = None,
    format_name: str | None = None,
) -> WTDTexture:
    is_color = name.casefold().endswith("_col")
    width = width if width is not None else (256 if is_color else 128)
    height = height if height is not None else (128 if is_color else 64)
    format_name = format_name or ("DXT1" if is_color else "DXT5")
    format_code = 0x31545844 if format_name == "DXT1" else 0x35545844
    return WTDTexture(
        index=0,
        hash=1,
        name=name,
        raw_name=f"pack:/{name}.dds",
        width=width,
        height=height,
        format_code=format_code,
        format_name=format_name,
        stride=0,
        texture_type=0,
        mip_count=1,
        data_offset=0,
        data_size=8,
        data=b"12345678",
    )


def make_archive(path: Path, *textures: WTDTexture) -> WTDArchive:
    return WTDArchive(
        path=path,
        header=WTDHeader(
            resource_type=8,
            flags=0,
            virtual_size=4096,
            physical_size=4096,
            texture_count=len(textures),
        ),
        textures=tuple(textures),
    )


def game_layout(tmp_path: Path):
    root = tmp_path / "game"
    original = root / "pc" / "textures"
    update = root / "update" / "pc" / "textures"
    original.mkdir(parents=True)
    update.mkdir(parents=True)
    return root, original, update


def write_image(path: Path) -> Path:
    image = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for x in range(5, 35):
        for y in range(3, 17):
            image.putpixel((x, y), (200, 80, 20, 180))
    image.save(path)
    return path


def install_archive_map(monkeypatch, archives: dict[Path, WTDArchive]):
    def fake_read(path):
        resolved = Path(path).resolve()
        try:
            return archives[resolved]
        except KeyError as exc:
            raise AssertionError(f"unexpected WTD read: {resolved}") from exc

    monkeypatch.setattr(station_pack, "read_wtd", fake_read)


def fake_replacer_calls(monkeypatch, calls: list[tuple]):
    def fake_replace(source, output, replacements, **kwargs):
        source_path = Path(source)
        output_path = Path(output)
        normalised = {name: Path(image) for name, image in replacements.items()}
        calls.append((source_path, output_path, normalised, kwargs))
        suffix = "".join(f"|{name}" for name in normalised).encode()
        output_path.write_bytes(source_path.read_bytes() + suffix)
        return SimpleNamespace()

    monkeypatch.setattr(
        station_pack,
        "replace_texture_payloads_from_images",
        fake_replace,
    )


def test_normalise_station_base_accepts_texture_names():
    assert normalise_station_base("  Vladivostok_COL ") == "vladivostok"
    assert normalise_station_base("vladivostok_bw") == "vladivostok"


@pytest.mark.parametrize("value", ["", "../radio", "radio-name", "a/b"])
def test_normalise_station_base_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        normalise_station_base(value)


def test_create_plan_finds_combined_wtd(monkeypatch, tmp_path):
    root, original, _ = game_layout(tmp_path)
    source = original / "radio_hud.wtd"
    source.write_bytes(b"source")
    install_archive_map(
        monkeypatch,
        {
            source.resolve(): make_archive(
                source,
                make_texture("vladivostok_col"),
                make_texture("vladivostok_bw"),
            )
        },
    )

    plan = create_station_logo_plan(root, RadioLogoTarget.GTA_IV, "vladivostok")

    assert plan.color_canvas.width == 256
    assert plan.noncolored_canvas.width == 128
    assert plan.wtd_files[0].texture_names == (
        "vladivostok_col",
        "vladivostok_bw",
    )


def test_create_plan_prefers_update_override(monkeypatch, tmp_path):
    root, original, update = game_layout(tmp_path)
    vanilla = original / "radio_hud_colored.wtd"
    override = update / "radio_hud_colored.wtd"
    bw = original / "radio_hud_noncolored.wtd"
    for path in (vanilla, override, bw):
        path.write_bytes(path.name.encode())
    install_archive_map(
        monkeypatch,
        {
            override.resolve(): make_archive(override, make_texture("beat_col")),
            bw.resolve(): make_archive(bw, make_texture("beat_bw")),
        },
    )

    plan = create_station_logo_plan(root, "gta_iv", "beat")

    assert {item.source_path for item in plan.wtd_files} == {override, bw}


def test_create_plan_direct_source_ignores_update(monkeypatch, tmp_path):
    root, original, update = game_layout(tmp_path)
    vanilla_col = original / "radio_hud_colored.wtd"
    override_col = update / "radio_hud_colored.wtd"
    vanilla_bw = original / "radio_hud_noncolored.wtd"
    for path in (vanilla_col, override_col, vanilla_bw):
        path.write_bytes(b"x")
    install_archive_map(
        monkeypatch,
        {
            vanilla_col.resolve(): make_archive(vanilla_col, make_texture("beat_col")),
            vanilla_bw.resolve(): make_archive(vanilla_bw, make_texture("beat_bw")),
        },
    )

    plan = create_station_logo_plan(
        root,
        "gta_iv",
        "beat",
        direct_source=True,
    )

    assert {item.source_path for item in plan.wtd_files} == {
        vanilla_col,
        vanilla_bw,
    }


def test_create_plan_requires_both_variants(monkeypatch, tmp_path):
    root, original, _ = game_layout(tmp_path)
    source = original / "radio_hud_colored.wtd"
    source.write_bytes(b"x")
    install_archive_map(
        monkeypatch,
        {source.resolve(): make_archive(source, make_texture("beat_col"))},
    )

    with pytest.raises(StationLogoPackError, match="beat_bw"):
        create_station_logo_plan(root, "gta_iv", "beat")


def test_create_plan_rejects_inconsistent_metadata(monkeypatch, tmp_path):
    root, original, _ = game_layout(tmp_path)
    combined = original / "radio_hud.wtd"
    colored = original / "radio_hud_colored.wtd"
    noncolored = original / "radio_hud_noncolored.wtd"
    for path in (combined, colored, noncolored):
        path.write_bytes(b"x")
    install_archive_map(
        monkeypatch,
        {
            combined.resolve(): make_archive(
                combined,
                make_texture("beat_col", width=256),
                make_texture("beat_bw"),
            ),
            colored.resolve(): make_archive(
                colored,
                make_texture("beat_col", width=512),
            ),
            noncolored.resolve(): make_archive(noncolored, make_texture("beat_bw")),
        },
    )

    with pytest.raises(StationLogoPackError, match="inconsistent metadata"):
        create_station_logo_plan(root, "gta_iv", "beat")


def test_list_station_logo_bases_returns_intersection(monkeypatch, tmp_path):
    root, original, _ = game_layout(tmp_path)
    source = original / "radio_hud.wtd"
    source.write_bytes(b"x")
    install_archive_map(
        monkeypatch,
        {
            source.resolve(): make_archive(
                source,
                make_texture("beat_col"),
                make_texture("beat_bw"),
                make_texture("fusion_col"),
                make_texture("orphan_bw"),
            )
        },
    )

    assert list_station_logo_bases(root, "gta_iv") == ("beat",)


def test_color_variant_is_opaque_on_black_background(tmp_path):
    source = write_image(tmp_path / "logo.png")
    output = tmp_path / "color.png"
    station_pack._prepare_color_variant(
        source,
        output,
        station_pack.TextureCanvas(256, 128, "DXT1", 1),
        fit_mode="fit",
        padding_ratio=0.0,
    )

    with Image.open(output) as image:
        rgba = image.convert("RGBA")
        colored_pixel = rgba.getpixel((128, 64))
        background_pixel = rgba.getpixel((0, 0))

    assert colored_pixel[:3] != (0, 0, 0)
    assert colored_pixel[3] == 255
    assert background_pixel == (0, 0, 0, 255)


def test_noncolored_variant_is_grayscale_and_preserves_alpha(tmp_path):
    source = write_image(tmp_path / "logo.png")
    output = tmp_path / "bw.png"
    station_pack._prepare_noncolored_variant(
        source,
        output,
        station_pack.TextureCanvas(128, 64, "DXT5", 1),
        fit_mode="fit",
        padding_ratio=0.0,
    )

    with Image.open(output) as image:
        rgba = image.convert("RGBA")
        colored_pixel = rgba.getpixel((64, 32))
        transparent_pixel = rgba.getpixel((0, 0))

    assert colored_pixel[0] == colored_pixel[1] == colored_pixel[2]
    assert colored_pixel[3] == 180
    assert transparent_pixel[3] == 0


def test_build_combined_wtd_replaces_both_textures(monkeypatch, tmp_path):
    root, original, _ = game_layout(tmp_path)
    source_wtd = original / "radio_hud.wtd"
    source_wtd.write_bytes(b"wtd")
    source_image = write_image(tmp_path / "logo.png")
    output = tmp_path / "pack"
    install_archive_map(
        monkeypatch,
        {
            source_wtd.resolve(): make_archive(
                source_wtd,
                make_texture("beat_col"),
                make_texture("beat_bw"),
            )
        },
    )
    calls = []
    fake_replacer_calls(monkeypatch, calls)

    result = build_station_logo_pack(
        root,
        "gta_iv",
        "beat",
        source_image,
        output,
    )

    assert len(calls) == 1
    assert tuple(calls[0][2]) == ("beat_col", "beat_bw")
    assert (output / "radio_hud.wtd").read_bytes().endswith(
        b"|beat_col|beat_bw"
    )
    assert result.wtd_files[0].replaced_textures == ("beat_col", "beat_bw")
    assert result.color_preview_path.is_file()
    assert result.noncolored_preview_path.is_file()


def test_build_split_wtds_produces_two_outputs(monkeypatch, tmp_path):
    root, original, _ = game_layout(tmp_path)
    colored = original / "radio_hud_colored.wtd"
    noncolored = original / "radio_hud_noncolored.wtd"
    colored.write_bytes(b"color")
    noncolored.write_bytes(b"bw")
    source_image = write_image(tmp_path / "logo.png")
    output = tmp_path / "pack"
    install_archive_map(
        monkeypatch,
        {
            colored.resolve(): make_archive(colored, make_texture("beat_col")),
            noncolored.resolve(): make_archive(
                noncolored,
                make_texture("beat_bw"),
            ),
        },
    )
    calls = []
    fake_replacer_calls(monkeypatch, calls)

    result = build_station_logo_pack(
        root,
        "gta_iv",
        "beat",
        source_image,
        output,
    )

    assert {item.output_path.name for item in result.wtd_files} == {
        "radio_hud_colored.wtd",
        "radio_hud_noncolored.wtd",
    }
    assert len(calls) == 2


def test_build_refuses_existing_output_without_overwrite(monkeypatch, tmp_path):
    root, original, _ = game_layout(tmp_path)
    source_wtd = original / "radio_hud.wtd"
    source_wtd.write_bytes(b"wtd")
    source_image = write_image(tmp_path / "logo.png")
    output = tmp_path / "pack"
    output.mkdir()
    (output / "radio_hud.wtd").write_bytes(b"existing")
    install_archive_map(
        monkeypatch,
        {
            source_wtd.resolve(): make_archive(
                source_wtd,
                make_texture("beat_col"),
                make_texture("beat_bw"),
            )
        },
    )

    with pytest.raises(FileExistsError):
        build_station_logo_pack(root, "gta_iv", "beat", source_image, output)


def test_build_failure_publishes_no_partial_files(monkeypatch, tmp_path):
    root, original, _ = game_layout(tmp_path)
    colored = original / "radio_hud_colored.wtd"
    noncolored = original / "radio_hud_noncolored.wtd"
    colored.write_bytes(b"color")
    noncolored.write_bytes(b"bw")
    source_image = write_image(tmp_path / "logo.png")
    output = tmp_path / "pack"
    install_archive_map(
        monkeypatch,
        {
            colored.resolve(): make_archive(colored, make_texture("beat_col")),
            noncolored.resolve(): make_archive(noncolored, make_texture("beat_bw")),
        },
    )

    counter = 0

    def fail_second(source, output_path, replacements, **kwargs):
        nonlocal counter
        counter += 1
        if counter == 2:
            raise RuntimeError("encode failed")
        Path(output_path).write_bytes(Path(source).read_bytes() + b"|patched")

    monkeypatch.setattr(
        station_pack,
        "replace_texture_payloads_from_images",
        fail_second,
    )

    with pytest.raises(RuntimeError, match="encode failed"):
        build_station_logo_pack(root, "gta_iv", "beat", source_image, output)

    assert not (output / "radio_hud_colored.wtd").exists()
    assert not (output / "radio_hud_noncolored.wtd").exists()
    assert not (output / "preview" / "beat_col.png").exists()


def test_build_rejects_invalid_quality(tmp_path):
    with pytest.raises(ValueError, match="quality"):
        build_station_logo_pack(
            tmp_path,
            "gta_iv",
            "beat",
            tmp_path / "missing.png",
            tmp_path / "out",
            quality=2.0,
        )


def test_plan_cli_prints_resolved_files(monkeypatch, tmp_path, capsys):
    root, original, _ = game_layout(tmp_path)
    source = original / "radio_hud.wtd"
    source.write_bytes(b"x")
    install_archive_map(
        monkeypatch,
        {
            source.resolve(): make_archive(
                source,
                make_texture("beat_col"),
                make_texture("beat_bw"),
            )
        },
    )

    assert main(["plan", str(root), "gta_iv", "beat"]) == 0
    output = capsys.readouterr().out
    assert "Station: beat" in output
    assert "radio_hud.wtd" in output


def test_list_cli_prints_station_bases(monkeypatch, tmp_path, capsys):
    root, original, _ = game_layout(tmp_path)
    source = original / "radio_hud.wtd"
    source.write_bytes(b"x")
    install_archive_map(
        monkeypatch,
        {
            source.resolve(): make_archive(
                source,
                make_texture("beat_col"),
                make_texture("beat_bw"),
            )
        },
    )

    assert main(["list", str(root), "gta_iv"]) == 0
    assert capsys.readouterr().out.strip() == "beat"
