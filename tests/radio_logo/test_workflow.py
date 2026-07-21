from __future__ import annotations

from pathlib import Path

import pytest

import core.radio_logo.workflow as workflow
from core.radio_logo.installer import InstalledRadioLogo, RadioLogoTarget
from core.radio_logo.station_pack import (
    BuiltStationLogoWtd,
    StationLogoPackResult,
    StationLogoPlan,
    TextureCanvas,
    WtdPatchPlan,
)


def _fake_package(
    output_directory: Path,
    *,
    target: RadioLogoTarget = RadioLogoTarget.GTA_IV,
    station: str = "vladivostok",
    wtd_names: tuple[str, ...] = ("radio_hud.wtd", "radio_hud_colored.wtd"),
) -> StationLogoPackResult:
    output_directory.mkdir(parents=True, exist_ok=True)
    preview = output_directory / "preview"
    preview.mkdir(parents=True, exist_ok=True)
    color_preview = preview / f"{station}_col.png"
    noncolored_preview = preview / f"{station}_bw.png"
    color_preview.write_bytes(b"color")
    noncolored_preview.write_bytes(b"bw")

    color = TextureCanvas(256, 128, "DXT1", 1)
    noncolored = TextureCanvas(128, 64, "DXT5", 1)
    plans = []
    built = []
    for name in wtd_names:
        source = output_directory / "source" / name
        output = output_directory / name
        output.write_bytes(name.encode("ascii"))
        plans.append(WtdPatchPlan(source, name, (f"{station}_col",)))
        built.append(
            BuiltStationLogoWtd(
                source_path=source,
                output_path=output,
                replaced_textures=(f"{station}_col",),
                sha256="hash",
                size=output.stat().st_size,
            )
        )

    plan = StationLogoPlan(
        game_root=output_directory.parent,
        target=target,
        station_base=station,
        color_texture_name=f"{station}_col",
        noncolored_texture_name=f"{station}_bw",
        color_canvas=color,
        noncolored_canvas=noncolored,
        wtd_files=tuple(plans),
    )
    return StationLogoPackResult(
        plan=plan,
        output_directory=output_directory,
        color_preview_path=color_preview,
        noncolored_preview_path=noncolored_preview,
        wtd_files=tuple(built),
    )


def _install_result(source: Path, destination: Path) -> InstalledRadioLogo:
    return InstalledRadioLogo(str(source), str(destination), None)


def test_update_install_builds_from_active_sources(tmp_path, monkeypatch):
    captured = {}

    def fake_build(game_root, target, station, image, output, **kwargs):
        captured["build"] = (game_root, target, station, image, Path(output), kwargs)
        return _fake_package(Path(output))

    def fake_install(game_root, sources, target, *, use_direct):
        source_paths = tuple(Path(item) for item in sources)
        captured["install"] = (game_root, source_paths, target, use_direct)
        return [
            _install_result(item, tmp_path / "game" / "update" / item.name)
            for item in source_paths
        ]

    monkeypatch.setattr(workflow, "build_station_logo_pack", fake_build)
    monkeypatch.setattr(workflow, "install_radio_logo_pack", fake_install)

    result = workflow.install_station_logo_from_image(
        tmp_path / "game", "gta_iv", "vladivostok", tmp_path / "logo.png"
    )

    assert captured["build"][5]["direct_source"] is False
    assert captured["install"][3] is False
    assert result.use_direct is False
    assert result.package_directory is None
    assert len(result.installed_files) == 2


def test_direct_install_builds_from_direct_sources(tmp_path, monkeypatch):
    captured = {}

    def fake_build(*args, **kwargs):
        captured["direct_source"] = kwargs["direct_source"]
        return _fake_package(Path(args[4]))

    def fake_install(game_root, sources, target, *, use_direct):
        captured["use_direct"] = use_direct
        return [
            _install_result(Path(item), tmp_path / "game" / "pc" / "textures" / Path(item).name)
            for item in sources
        ]

    monkeypatch.setattr(workflow, "build_station_logo_pack", fake_build)
    monkeypatch.setattr(workflow, "install_radio_logo_pack", fake_install)

    result = workflow.install_station_logo_from_image(
        tmp_path / "game",
        RadioLogoTarget.GTA_IV,
        "vladivostok",
        tmp_path / "logo.png",
        use_direct=True,
    )

    assert captured == {"direct_source": True, "use_direct": True}
    assert result.use_direct is True


def test_temporary_package_is_removed_after_install(tmp_path, monkeypatch):
    observed = {}

    def fake_build(*args, **kwargs):
        output = Path(args[4])
        observed["output"] = output
        return _fake_package(output)

    monkeypatch.setattr(workflow, "build_station_logo_pack", fake_build)
    monkeypatch.setattr(
        workflow,
        "install_radio_logo_pack",
        lambda game_root, sources, target, *, use_direct: [
            _install_result(Path(item), tmp_path / "installed" / Path(item).name)
            for item in sources
        ],
    )

    result = workflow.install_station_logo_from_image(
        tmp_path / "game", "gta_iv", "vladivostok", tmp_path / "logo.png"
    )

    assert result.package_directory is None
    assert not observed["output"].exists()


def test_explicit_package_is_preserved(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"

    monkeypatch.setattr(
        workflow,
        "build_station_logo_pack",
        lambda *args, **kwargs: _fake_package(Path(args[4])),
    )
    monkeypatch.setattr(
        workflow,
        "install_radio_logo_pack",
        lambda game_root, sources, target, *, use_direct: [
            _install_result(Path(item), tmp_path / "installed" / Path(item).name)
            for item in sources
        ],
    )

    result = workflow.install_station_logo_from_image(
        tmp_path / "game",
        "gta_iv",
        "vladivostok",
        tmp_path / "logo.png",
        package_directory=package_dir,
    )

    assert result.package_directory == package_dir.resolve()
    assert result.color_preview_path is not None
    assert result.noncolored_preview_path is not None
    assert package_dir.exists()


def test_build_options_are_forwarded(tmp_path, monkeypatch):
    captured = {}

    def fake_build(*args, **kwargs):
        captured.update(kwargs)
        return _fake_package(Path(args[4]))

    monkeypatch.setattr(workflow, "build_station_logo_pack", fake_build)
    monkeypatch.setattr(
        workflow,
        "install_radio_logo_pack",
        lambda game_root, sources, target, *, use_direct: [
            _install_result(Path(item), tmp_path / "installed" / Path(item).name)
            for item in sources
        ],
    )

    workflow.install_station_logo_from_image(
        tmp_path / "game",
        "tlad",
        "station",
        tmp_path / "logo.png",
        fit_mode="fill",
        padding_ratio=0.125,
        quality=0.75,
        overwrite_package=True,
    )

    assert captured["fit_mode"] == "fill"
    assert captured["padding_ratio"] == 0.125
    assert captured["quality"] == 0.75
    assert captured["overwrite"] is True


def test_empty_package_is_rejected_before_install(tmp_path, monkeypatch):
    package = _fake_package(tmp_path / "package", wtd_names=())
    install_called = False

    monkeypatch.setattr(workflow, "build_station_logo_pack", lambda *a, **k: package)

    def fake_install(*args, **kwargs):
        nonlocal install_called
        install_called = True
        return []

    monkeypatch.setattr(workflow, "install_radio_logo_pack", fake_install)

    with pytest.raises(workflow.StationLogoWorkflowError, match="no WTD files"):
        workflow.install_station_logo_from_image(
            tmp_path / "game", "gta_iv", "station", tmp_path / "logo.png"
        )

    assert install_called is False


def test_duplicate_package_filenames_are_rejected(tmp_path, monkeypatch):
    package = _fake_package(
        tmp_path / "package",
        wtd_names=("radio_hud.wtd", "RADIO_HUD.WTD"),
    )
    monkeypatch.setattr(workflow, "build_station_logo_pack", lambda *a, **k: package)

    with pytest.raises(workflow.StationLogoWorkflowError, match="duplicate"):
        workflow.install_station_logo_from_image(
            tmp_path / "game", "gta_iv", "station", tmp_path / "logo.png"
        )


def test_build_failure_does_not_run_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        workflow,
        "build_station_logo_pack",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("build failed")),
    )
    monkeypatch.setattr(
        workflow,
        "install_radio_logo_pack",
        lambda *args, **kwargs: pytest.fail("installer must not run"),
    )

    with pytest.raises(RuntimeError, match="build failed"):
        workflow.install_station_logo_from_image(
            tmp_path / "game", "gta_iv", "station", tmp_path / "logo.png"
        )


def test_result_uses_normalised_plan_metadata(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package = _fake_package(
        package_dir,
        target=RadioLogoTarget.TBOGT,
        station="vicecityfm",
        wtd_names=("radio_hud.wtd",),
    )
    monkeypatch.setattr(workflow, "build_station_logo_pack", lambda *a, **k: package)
    monkeypatch.setattr(
        workflow,
        "install_radio_logo_pack",
        lambda game_root, sources, target, *, use_direct: [
            _install_result(Path(next(iter(sources))), tmp_path / "installed" / "radio_hud.wtd")
        ],
    )

    result = workflow.install_station_logo_from_image(
        tmp_path / "game",
        "tbogt",
        "VICECITYFM_col",
        tmp_path / "logo.png",
        package_directory=package_dir,
    )

    assert result.target is RadioLogoTarget.TBOGT
    assert result.station_base == "vicecityfm"
    assert result.color_canvas == TextureCanvas(256, 128, "DXT1", 1)
    assert result.noncolored_canvas == TextureCanvas(128, 64, "DXT5", 1)


def test_cli_forwards_arguments_and_prints_results(tmp_path, monkeypatch, capsys):
    installed = InstalledRadioLogo(
        "package/radio_hud.wtd",
        "game/update/pc/textures/radio_hud.wtd",
        "game/update/pc/textures/radio_hud.wtd.backup",
    )
    result = workflow.InstalledStationLogoResult(
        target=RadioLogoTarget.GTA_IV,
        station_base="vladivostok",
        use_direct=False,
        destination_directory=tmp_path / "game" / "update" / "pc" / "textures",
        package_directory=tmp_path / "package",
        color_preview_path=tmp_path / "package" / "preview" / "vladivostok_col.png",
        noncolored_preview_path=tmp_path / "package" / "preview" / "vladivostok_bw.png",
        color_canvas=TextureCanvas(256, 128, "DXT1", 1),
        noncolored_canvas=TextureCanvas(128, 64, "DXT5", 1),
        installed_files=(installed,),
    )
    captured = {}

    def fake_install(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return result

    monkeypatch.setattr(workflow, "install_station_logo_from_image", fake_install)

    exit_code = workflow.main(
        [
            "game",
            "gta_iv",
            "vladivostok",
            "logo.png",
            "--fit",
            "fill",
            "--padding",
            "0.1",
            "--quality",
            "0.8",
            "--package-directory",
            "package",
            "--overwrite-package",
        ]
    )

    assert exit_code == 0
    assert captured["kwargs"] == {
        "use_direct": False,
        "fit_mode": "fill",
        "padding_ratio": 0.1,
        "quality": 0.8,
        "package_directory": "package",
        "overwrite_package": True,
    }
    output = capsys.readouterr().out
    assert "Station: vladivostok" in output
    assert "Installed:" in output
    assert "Backup:" in output


def test_cli_direct_flag_is_forwarded(monkeypatch, tmp_path):
    result = workflow.InstalledStationLogoResult(
        target=RadioLogoTarget.GTA_IV,
        station_base="station",
        use_direct=True,
        destination_directory=tmp_path,
        package_directory=None,
        color_preview_path=None,
        noncolored_preview_path=None,
        color_canvas=TextureCanvas(256, 128, "DXT1", 1),
        noncolored_canvas=TextureCanvas(128, 64, "DXT5", 1),
        installed_files=(),
    )
    captured = {}

    def fake_install(*args, **kwargs):
        captured.update(kwargs)
        return result

    monkeypatch.setattr(workflow, "install_station_logo_from_image", fake_install)

    assert workflow.main(["game", "gta_iv", "station", "logo.png", "--direct"]) == 0
    assert captured["use_direct"] is True
