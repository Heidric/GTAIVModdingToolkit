from pathlib import Path
from types import SimpleNamespace

import pytest

import core.radio_logo.diagnostics as diagnostics
from core.radio_logo.diagnostics import (
    PRODUCTION_WTD_WRITE_MODE,
    RadioLogoDiagnostic,
    RadioLogoDiagnosticSeverity,
    RadioLogoPreflightError,
    diagnose_station_logo_workflow,
    require_station_logo_workflow_ready,
)
from core.radio_logo.installer import RadioLogoTarget


def _game(tmp_path: Path) -> Path:
    game = tmp_path / "GTAIV"
    (game / "pc" / "textures").mkdir(parents=True)
    return game


def _ready_dependencies():
    return [
        RadioLogoDiagnostic(
            "dependencies-ready",
            RadioLogoDiagnosticSeverity.INFO,
            "dependencies ready",
        )
    ]


def test_ready_report_uses_surgical_payload_mode(tmp_path, monkeypatch):
    game = _game(tmp_path)
    source = game / "pc" / "textures" / "radio_hud.wtd"
    source.write_bytes(b"wtd")
    image = tmp_path / "logo.png"
    image.write_bytes(b"image")

    monkeypatch.setattr(diagnostics, "_check_dependencies", _ready_dependencies)
    monkeypatch.setattr(
        diagnostics,
        "create_station_logo_plan",
        lambda *args, **kwargs: SimpleNamespace(
            wtd_files=(SimpleNamespace(source_path=source),)
        ),
    )
    monkeypatch.setattr(
        diagnostics,
        "inspect_logo_image",
        lambda path: SimpleNamespace(
            width=256,
            height=128,
            has_transparency=True,
        ),
    )

    report = diagnose_station_logo_workflow(
        game,
        RadioLogoTarget.GTA_IV,
        "vladivostok",
        source_image=image,
    )

    assert report.ready
    assert report.production_mode == PRODUCTION_WTD_WRITE_MODE
    assert not report.errors
    assert any(item.code == "production-write-mode" for item in report.diagnostics)


def test_missing_encoder_blocks_preflight(monkeypatch):
    real_import = diagnostics.importlib.import_module

    def fake_import(name):
        if name == "texfury":
            raise ImportError("missing encoder")
        return real_import(name)

    monkeypatch.setattr(diagnostics.importlib, "import_module", fake_import)

    result = diagnostics._check_dependencies()

    assert any(
        item.code == "texfury-encoder-unavailable"
        and item.severity is RadioLogoDiagnosticSeverity.ERROR
        for item in result
    )


def test_invalid_station_plan_is_actionable(tmp_path, monkeypatch):
    game = _game(tmp_path)
    monkeypatch.setattr(diagnostics, "_check_dependencies", _ready_dependencies)
    monkeypatch.setattr(
        diagnostics,
        "create_station_logo_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("missing _bw")),
    )

    with pytest.raises(RadioLogoPreflightError, match="missing _bw"):
        require_station_logo_workflow_ready(
            game,
            RadioLogoTarget.GTA_IV,
            "broken",
        )


def test_opaque_image_is_warning_not_blocker(tmp_path, monkeypatch):
    game = _game(tmp_path)
    source = game / "pc" / "textures" / "radio_hud.wtd"
    source.write_bytes(b"wtd")
    image = tmp_path / "logo.jpg"
    image.write_bytes(b"image")

    monkeypatch.setattr(diagnostics, "_check_dependencies", _ready_dependencies)
    monkeypatch.setattr(
        diagnostics,
        "create_station_logo_plan",
        lambda *args, **kwargs: SimpleNamespace(
            wtd_files=(SimpleNamespace(source_path=source),)
        ),
    )
    monkeypatch.setattr(
        diagnostics,
        "inspect_logo_image",
        lambda path: SimpleNamespace(
            width=256,
            height=128,
            has_transparency=False,
        ),
    )

    report = diagnose_station_logo_workflow(
        game,
        RadioLogoTarget.GTA_IV,
        "vladivostok",
        source_image=image,
    )

    assert report.ready
    assert report.warnings
    assert report.warnings[0].code == "source-image-ready"
