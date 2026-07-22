"""Application and GTA IV installation readiness checks."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class CheckStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


@dataclass(frozen=True)
class SystemCheckItem:
    key: str
    label: str
    status: CheckStatus
    detail: str
    blocking: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status.value,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class SystemCheckReport:
    items: tuple[SystemCheckItem, ...]

    @property
    def has_failures(self) -> bool:
        return any(
            item.status is CheckStatus.FAIL and item.blocking for item in self.items
        )

    @property
    def exit_code(self) -> int:
        return 1 if self.has_failures else 0

    @property
    def summary(self) -> str:
        passed = sum(item.status is CheckStatus.PASS for item in self.items)
        warnings = sum(item.status is CheckStatus.WARNING for item in self.items)
        failures = sum(item.status is CheckStatus.FAIL for item in self.items)
        return f"{passed} passed, {warnings} warning(s), {failures} failure(s)"

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "exit_code": self.exit_code,
            "items": [item.to_dict() for item in self.items],
        }


Runner = Callable[..., object]
Which = Callable[[str], str | None]
DependencyLoader = Callable[[str], object]


def _resource_root(override: str | os.PathLike[str] | None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root).resolve()
    return Path(__file__).resolve().parents[1]


def _dependency_item(
    key: str,
    label: str,
    module_name: str,
    loader: DependencyLoader,
) -> SystemCheckItem:
    try:
        loader(module_name)
    except Exception as exc:
        return SystemCheckItem(
            key,
            label,
            CheckStatus.FAIL,
            f"Import failed: {exc}",
            blocking=True,
        )
    return SystemCheckItem(
        key,
        label,
        CheckStatus.PASS,
        f"Python module {module_name} is available.",
    )


def _resource_item(key: str, label: str, path: Path, *, directory: bool = False) -> SystemCheckItem:
    exists = path.is_dir() if directory else path.is_file()
    if exists:
        return SystemCheckItem(
            key,
            label,
            CheckStatus.PASS,
            f"Found: {path}",
        )
    expected_kind = "directory" if directory else "file"
    return SystemCheckItem(
        key,
        label,
        CheckStatus.FAIL,
        f"Required {expected_kind} is missing: {path}",
        blocking=True,
    )


def _tool_version(path: str, runner: Runner) -> tuple[bool, str]:
    try:
        result = runner(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)

    return_code = int(getattr(result, "returncode", 1))
    output = str(getattr(result, "stdout", "") or getattr(result, "stderr", ""))
    first_line = output.splitlines()[0].strip() if output.splitlines() else ""
    if return_code != 0:
        return False, first_line or f"exit code {return_code}"
    return True, first_line or "version command succeeded"


def _ffmpeg_items(which: Which, runner: Runner) -> list[SystemCheckItem]:
    items: list[SystemCheckItem] = []
    for executable, label in (("ffmpeg", "FFmpeg"), ("ffprobe", "FFprobe")):
        path = which(executable)
        if not path:
            items.append(
                SystemCheckItem(
                    f"audio.{executable}",
                    label,
                    CheckStatus.WARNING,
                    f"{label} was not found through PATH; audio replacement is unavailable.",
                )
            )
            continue

        healthy, detail = _tool_version(path, runner)
        if healthy:
            items.append(
                SystemCheckItem(
                    f"audio.{executable}",
                    label,
                    CheckStatus.PASS,
                    f"{path} — {detail}",
                )
            )
        else:
            items.append(
                SystemCheckItem(
                    f"audio.{executable}",
                    label,
                    CheckStatus.WARNING,
                    f"{path} could not be executed: {detail}",
                )
            )
    return items


def _nearest_existing_directory(path: Path) -> Path | None:
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else None


def _write_probe_item(key: str, label: str, target_directory: Path) -> SystemCheckItem:
    probe_directory = _nearest_existing_directory(target_directory)
    if probe_directory is None:
        return SystemCheckItem(
            key,
            label,
            CheckStatus.FAIL,
            f"No existing parent directory is available for: {target_directory}",
            blocking=True,
        )

    try:
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".gtaiv_toolkit_write_probe_",
            dir=probe_directory,
        )
        os.close(descriptor)
        Path(probe_name).unlink(missing_ok=True)
    except OSError as exc:
        return SystemCheckItem(
            key,
            label,
            CheckStatus.FAIL,
            f"Cannot write under {probe_directory}: {exc}",
            blocking=True,
        )

    return SystemCheckItem(
        key,
        label,
        CheckStatus.PASS,
        f"Temporary write probe succeeded under {probe_directory}.",
    )


def _game_items(
    gtaiv_path: str | os.PathLike[str] | None,
    *,
    use_direct: bool,
    probe_writes: bool,
) -> list[SystemCheckItem]:
    if gtaiv_path is None or not os.fspath(gtaiv_path).strip():
        return [
            SystemCheckItem(
                "game.installation",
                "GTA IV installation",
                CheckStatus.WARNING,
                "No GTA IV directory was supplied; installation-specific checks were skipped.",
            )
        ]

    root = Path(gtaiv_path).expanduser().resolve()
    items: list[SystemCheckItem] = []
    exe_path = root / "GTAIV.exe"
    sfx_path = root / "pc" / "audio" / "sfx"
    dat15_path = root / "pc" / "audio" / "config" / "sounds.dat15"
    textures_path = root / "pc" / "textures"

    for key, label, path, is_directory in (
        ("game.exe", "GTAIV.exe", exe_path, False),
        ("game.sfx", "Original radio directory", sfx_path, True),
        ("game.dat15", "Original sounds.dat15", dat15_path, False),
    ):
        exists = path.is_dir() if is_directory else path.is_file()
        if exists:
            items.append(
                SystemCheckItem(key, label, CheckStatus.PASS, f"Found: {path}")
            )
        else:
            items.append(
                SystemCheckItem(
                    key,
                    label,
                    CheckStatus.FAIL,
                    f"Required path is missing: {path}",
                    blocking=True,
                )
            )

    if sfx_path.is_dir():
        try:
            radio_count = sum(1 for _ in sfx_path.glob("radio_*.rpf"))
        except OSError as exc:
            items.append(
                SystemCheckItem(
                    "game.radio_archives",
                    "Radio archives",
                    CheckStatus.FAIL,
                    f"Could not enumerate radio archives: {exc}",
                    blocking=True,
                )
            )
        else:
            status = CheckStatus.PASS if radio_count else CheckStatus.FAIL
            items.append(
                SystemCheckItem(
                    "game.radio_archives",
                    "Radio archives",
                    status,
                    f"Found {radio_count} radio RPF archive(s).",
                    blocking=radio_count == 0,
                )
            )

    logo_count = 0
    if textures_path.is_dir():
        try:
            logo_count = sum(1 for _ in textures_path.glob("radio_hud*.wtd"))
        except OSError:
            logo_count = 0
    items.append(
        SystemCheckItem(
            "game.logo_textures",
            "Radio logo textures",
            CheckStatus.PASS if logo_count else CheckStatus.WARNING,
            (
                f"Found {logo_count} radio HUD texture archive(s)."
                if logo_count
                else "No radio_hud*.wtd files were found; logo replacement may be unavailable."
            ),
        )
    )

    if use_direct:
        items.append(
            SystemCheckItem(
                "mode.direct",
                "Replacement mode",
                CheckStatus.WARNING,
                "Direct mode modifies original game files; FusionFix mode is recommended.",
            )
        )
        write_targets = (sfx_path, dat15_path.parent)
    else:
        fusionfix_candidates = (
            root / "plugins" / "GTAIV.EFLC.FusionFix.asi",
            root / "plugins" / "FusionFix.asi",
        )
        fusionfix = next((path for path in fusionfix_candidates if path.is_file()), None)
        if fusionfix is None:
            items.append(
                SystemCheckItem(
                    "mode.fusionfix",
                    "FusionFix",
                    CheckStatus.FAIL,
                    "FusionFix was not found in the plugins directory.",
                    blocking=True,
                )
            )
        else:
            items.append(
                SystemCheckItem(
                    "mode.fusionfix",
                    "FusionFix",
                    CheckStatus.PASS,
                    f"Found: {fusionfix}",
                )
            )
        write_targets = (
            root / "update" / "pc" / "audio" / "sfx",
            root / "update" / "pc" / "audio" / "config",
        )

    if probe_writes:
        for index, target in enumerate(write_targets, start=1):
            items.append(
                _write_probe_item(
                    f"game.write_target_{index}",
                    f"Write access {index}",
                    target,
                )
            )

    return items


def run_system_check(
    gtaiv_path: str | os.PathLike[str] | None = None,
    *,
    use_direct: bool = False,
    packaged_only: bool = False,
    resource_root: str | os.PathLike[str] | None = None,
    which: Which | None = None,
    runner: Runner | None = None,
    dependency_loader: DependencyLoader | None = None,
    probe_writes: bool = True,
) -> SystemCheckReport:
    """Run deterministic packaged-resource, dependency, and game checks."""

    root = _resource_root(resource_root)
    which = shutil.which if which is None else which
    runner = subprocess.run if runner is None else runner
    dependency_loader = importlib.import_module if dependency_loader is None else dependency_loader

    items = [
        _resource_item("resource.assets", "Bundled assets", root / "assets", directory=True),
        _resource_item("resource.ivam", "ivam.exe", root / "tools" / "ivam.exe"),
        _resource_item(
            "resource.ivaudioconv",
            "IVAudioConv.exe",
            root / "tools" / "IVAudioConv.exe",
        ),
        _dependency_item("dependency.pillow", "Pillow", "PIL.Image", dependency_loader),
        _dependency_item("dependency.texfury", "texfury", "texfury", dependency_loader),
    ]
    items.extend(_ffmpeg_items(which, runner))

    if packaged_only:
        items.append(
            SystemCheckItem(
                "game.skipped",
                "GTA IV installation",
                CheckStatus.PASS,
                "Installation-specific checks were intentionally skipped.",
            )
        )
    else:
        items.extend(
            _game_items(
                gtaiv_path,
                use_direct=use_direct,
                probe_writes=probe_writes,
            )
        )

    return SystemCheckReport(tuple(items))


def format_system_check_report(report: SystemCheckReport) -> str:
    prefixes = {
        CheckStatus.PASS: "PASS",
        CheckStatus.WARNING: "WARN",
        CheckStatus.FAIL: "FAIL",
    }
    lines = [
        f"[{prefixes[item.status]}] {item.label}: {item.detail}"
        for item in report.items
    ]
    lines.append(f"Summary: {report.summary}")
    return "\n".join(lines)
