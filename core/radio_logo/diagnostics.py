"""Preflight diagnostics for the production radio-logo workflow."""

from __future__ import annotations

import importlib
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from pathlib import Path

from .images import inspect_logo_image
from .installer import RadioLogoTarget, get_radio_logo_destination_dir
from .station_pack import create_station_logo_plan


PRODUCTION_WTD_WRITE_MODE = "surgical-payload-patch"


class RadioLogoDiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class RadioLogoDiagnostic:
    code: str
    severity: RadioLogoDiagnosticSeverity
    message: str


@dataclass(frozen=True)
class RadioLogoReadinessReport:
    diagnostics: tuple[RadioLogoDiagnostic, ...]
    production_mode: str = PRODUCTION_WTD_WRITE_MODE

    @property
    def ready(self) -> bool:
        return all(
            item.severity is not RadioLogoDiagnosticSeverity.ERROR
            for item in self.diagnostics
        )

    @property
    def errors(self) -> tuple[RadioLogoDiagnostic, ...]:
        return tuple(
            item
            for item in self.diagnostics
            if item.severity is RadioLogoDiagnosticSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[RadioLogoDiagnostic, ...]:
        return tuple(
            item
            for item in self.diagnostics
            if item.severity is RadioLogoDiagnosticSeverity.WARNING
        )


class RadioLogoPreflightError(RuntimeError):
    """Raised when the production radio-logo workflow is not ready."""


def _distribution_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unknown"


def _check_dependencies() -> list[RadioLogoDiagnostic]:
    result: list[RadioLogoDiagnostic] = []
    try:
        importlib.import_module("PIL.Image")
    except (ImportError, OSError) as exc:
        result.append(
            RadioLogoDiagnostic(
                "pillow-unavailable",
                RadioLogoDiagnosticSeverity.ERROR,
                f"Pillow is unavailable: {exc}. Install requirements.txt.",
            )
        )
    else:
        result.append(
            RadioLogoDiagnostic(
                "pillow-ready",
                RadioLogoDiagnosticSeverity.INFO,
                f"Pillow {_distribution_version('Pillow')} is available.",
            )
        )

    try:
        texfury = importlib.import_module("texfury")
        getattr(texfury, "BCFormat")
        getattr(texfury, "Texture")
    except (ImportError, OSError, AttributeError) as exc:
        result.append(
            RadioLogoDiagnostic(
                "texfury-encoder-unavailable",
                RadioLogoDiagnosticSeverity.ERROR,
                "The texfury texture encoder is unavailable or incomplete: "
                f"{exc}. Install requirements.txt.",
            )
        )
    else:
        result.append(
            RadioLogoDiagnostic(
                "texfury-encoder-ready",
                RadioLogoDiagnosticSeverity.INFO,
                f"texfury {_distribution_version('texfury')} encoder is available.",
            )
        )
    return result


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _check_workspace() -> RadioLogoDiagnostic:
    try:
        with tempfile.TemporaryDirectory(prefix="gtaiv-toolkit-preflight-") as directory:
            probe = Path(directory) / "write-probe"
            probe.write_bytes(b"ok")
            if probe.read_bytes() != b"ok":
                raise OSError("temporary write verification failed")
    except OSError as exc:
        return RadioLogoDiagnostic(
            "temporary-workspace-unavailable",
            RadioLogoDiagnosticSeverity.ERROR,
            f"A writable temporary workspace is unavailable: {exc}",
        )
    return RadioLogoDiagnostic(
        "temporary-workspace-ready",
        RadioLogoDiagnosticSeverity.INFO,
        "A writable temporary workspace is available.",
    )


def diagnose_station_logo_workflow(
    gtaiv_path: str | os.PathLike[str],
    target: RadioLogoTarget | str,
    station_base: str,
    *,
    direct_source: bool = False,
    source_image: str | os.PathLike[str] | None = None,
) -> RadioLogoReadinessReport:
    """Inspect dependencies, source WTDs, image input, and write locations."""

    diagnostics = _check_dependencies()
    game_root = Path(gtaiv_path).expanduser().resolve()
    if not game_root.is_dir():
        diagnostics.append(
            RadioLogoDiagnostic(
                "game-root-missing",
                RadioLogoDiagnosticSeverity.ERROR,
                f"GTA IV directory was not found: {game_root}",
            )
        )
        return RadioLogoReadinessReport(tuple(diagnostics))

    original_directory = get_radio_logo_destination_dir(
        game_root,
        target,
        use_direct=True,
    )
    if not original_directory.is_dir():
        diagnostics.append(
            RadioLogoDiagnostic(
                "target-textures-missing",
                RadioLogoDiagnosticSeverity.ERROR,
                f"The selected target texture directory was not found: {original_directory}",
            )
        )
    else:
        diagnostics.append(
            RadioLogoDiagnostic(
                "target-textures-ready",
                RadioLogoDiagnosticSeverity.INFO,
                f"Target textures are available in {original_directory}.",
            )
        )

    try:
        plan = create_station_logo_plan(
            game_root,
            target,
            station_base,
            direct_source=direct_source,
        )
    except Exception as exc:
        diagnostics.append(
            RadioLogoDiagnostic(
                "station-plan-invalid",
                RadioLogoDiagnosticSeverity.ERROR,
                f"Station WTD sources cannot be prepared safely: {exc}",
            )
        )
    else:
        missing_sources = [
            item.source_path for item in plan.wtd_files if not item.source_path.is_file()
        ]
        if missing_sources:
            diagnostics.append(
                RadioLogoDiagnostic(
                    "station-source-missing",
                    RadioLogoDiagnosticSeverity.ERROR,
                    "Station WTD source files disappeared: "
                    + ", ".join(str(path) for path in missing_sources),
                )
            )
        elif not plan.wtd_files:
            diagnostics.append(
                RadioLogoDiagnostic(
                    "station-source-empty",
                    RadioLogoDiagnosticSeverity.ERROR,
                    "No WTD files contain both required station logo variants.",
                )
            )
        else:
            diagnostics.append(
                RadioLogoDiagnostic(
                    "station-source-ready",
                    RadioLogoDiagnosticSeverity.INFO,
                    f"{len(plan.wtd_files)} WTD file(s) will be patched in place; "
                    "RSC5 metadata will not be rebuilt.",
                )
            )

    destination = get_radio_logo_destination_dir(
        game_root,
        target,
        use_direct=direct_source,
    )
    writable_parent = _nearest_existing_parent(destination)
    if not writable_parent.is_dir() or not os.access(writable_parent, os.W_OK):
        diagnostics.append(
            RadioLogoDiagnostic(
                "destination-not-writable",
                RadioLogoDiagnosticSeverity.ERROR,
                f"The destination parent is not writable: {writable_parent}",
            )
        )
    else:
        diagnostics.append(
            RadioLogoDiagnostic(
                "destination-ready",
                RadioLogoDiagnosticSeverity.INFO,
                f"Destination parent is writable: {writable_parent}",
            )
        )

    if source_image is not None:
        try:
            image_info = inspect_logo_image(source_image)
        except Exception as exc:
            diagnostics.append(
                RadioLogoDiagnostic(
                    "source-image-invalid",
                    RadioLogoDiagnosticSeverity.ERROR,
                    f"The source logo image cannot be decoded: {exc}",
                )
            )
        else:
            severity = (
                RadioLogoDiagnosticSeverity.INFO
                if image_info.has_transparency
                else RadioLogoDiagnosticSeverity.WARNING
            )
            transparency = (
                "contains transparency"
                if image_info.has_transparency
                else "has no transparent pixels; its inactive background may remain visible"
            )
            diagnostics.append(
                RadioLogoDiagnostic(
                    "source-image-ready",
                    severity,
                    f"Source image is {image_info.width}x{image_info.height} and {transparency}.",
                )
            )

    diagnostics.append(_check_workspace())
    diagnostics.append(
        RadioLogoDiagnostic(
            "production-write-mode",
            RadioLogoDiagnosticSeverity.INFO,
            "Production mode is surgical payload patching; full WTD reconstruction is disabled.",
        )
    )
    return RadioLogoReadinessReport(tuple(diagnostics))


def require_station_logo_workflow_ready(*args, **kwargs) -> RadioLogoReadinessReport:
    """Return a readiness report or raise one actionable preflight error."""

    report = diagnose_station_logo_workflow(*args, **kwargs)
    if report.ready:
        return report
    details = "\n".join(f"- {item.message}" for item in report.errors)
    raise RadioLogoPreflightError(
        "Radio-logo production preflight failed:\n" + details
    )
