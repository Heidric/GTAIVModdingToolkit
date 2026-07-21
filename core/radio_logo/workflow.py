"""End-to-end radio-logo build and transactional installation workflow."""

from __future__ import annotations

import argparse
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .images import LogoFitMode
from .installer import (
    InstalledRadioLogo,
    RadioLogoTarget,
    get_radio_logo_destination_dir,
    install_radio_logo_pack,
)
from .station_pack import (
    StationLogoPackResult,
    TextureCanvas,
    build_station_logo_pack,
)


class StationLogoWorkflowError(RuntimeError):
    """Raised when the combined build-and-install workflow is invalid."""


@dataclass(frozen=True)
class InstalledStationLogoResult:
    """Stable result of building and installing one station logo."""

    target: RadioLogoTarget
    station_base: str
    use_direct: bool
    destination_directory: Path
    package_directory: Path | None
    color_preview_path: Path | None
    noncolored_preview_path: Path | None
    color_canvas: TextureCanvas
    noncolored_canvas: TextureCanvas
    installed_files: tuple[InstalledRadioLogo, ...]


@contextmanager
def _package_workspace(
    package_directory: str | os.PathLike[str] | None,
) -> Iterator[tuple[Path, bool]]:
    if package_directory is not None:
        resolved = Path(package_directory).expanduser().resolve()
        yield resolved, True
        return

    with tempfile.TemporaryDirectory(prefix="gtaiv-toolkit-radio-logo-") as raw_path:
        yield Path(raw_path), False


def _package_sources(package: StationLogoPackResult) -> tuple[Path, ...]:
    sources = tuple(item.output_path for item in package.wtd_files)
    if not sources:
        raise StationLogoWorkflowError(
            "station logo build produced no WTD files to install"
        )

    folded_names = [path.name.casefold() for path in sources]
    if len(folded_names) != len(set(folded_names)):
        raise StationLogoWorkflowError(
            "station logo build produced duplicate WTD filenames"
        )
    return sources


def install_station_logo_from_image(
    gtaiv_path: str | os.PathLike[str],
    target: RadioLogoTarget | str,
    station_base: str,
    source_image: str | os.PathLike[str],
    *,
    use_direct: bool = False,
    fit_mode: LogoFitMode | str = LogoFitMode.FIT,
    padding_ratio: float = 0.0,
    quality: float = 0.9,
    package_directory: str | os.PathLike[str] | None = None,
    overwrite_package: bool = False,
) -> InstalledStationLogoResult:
    """Build all required WTD files and install them transactionally.

    FusionFix mode builds from the active WTD set, preferring existing files in
    ``update``. Direct mode builds from the original game/episode texture
    directory. An explicit *package_directory* preserves generated WTD files and
    previews; otherwise the intermediate package is removed after installation.
    """

    with _package_workspace(package_directory) as (workspace, preserve_package):
        package = build_station_logo_pack(
            gtaiv_path,
            target,
            station_base,
            source_image,
            workspace,
            direct_source=use_direct,
            fit_mode=fit_mode,
            padding_ratio=padding_ratio,
            quality=quality,
            overwrite=overwrite_package,
        )
        source_files = _package_sources(package)
        installed = tuple(
            install_radio_logo_pack(
                gtaiv_path,
                source_files,
                package.plan.target,
                use_direct=use_direct,
            )
        )

        destination = get_radio_logo_destination_dir(
            gtaiv_path,
            package.plan.target,
            use_direct=use_direct,
        ).resolve()

        return InstalledStationLogoResult(
            target=package.plan.target,
            station_base=package.plan.station_base,
            use_direct=use_direct,
            destination_directory=destination,
            package_directory=package.output_directory if preserve_package else None,
            color_preview_path=(
                package.color_preview_path if preserve_package else None
            ),
            noncolored_preview_path=(
                package.noncolored_preview_path if preserve_package else None
            ),
            color_canvas=package.plan.color_canvas,
            noncolored_canvas=package.plan.noncolored_canvas,
            installed_files=installed,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and install a GTA IV radio-station logo from one image."
    )
    parser.add_argument("game_root")
    parser.add_argument("target", choices=[item.value for item in RadioLogoTarget])
    parser.add_argument("station")
    parser.add_argument("image")
    parser.add_argument(
        "--fit",
        choices=[item.value for item in LogoFitMode],
        default=LogoFitMode.FIT.value,
    )
    parser.add_argument("--padding", type=float, default=0.0)
    parser.add_argument("--quality", type=float, default=0.9)
    parser.add_argument(
        "--direct",
        action="store_true",
        help="write to the original game directory instead of FusionFix update",
    )
    parser.add_argument("--package-directory")
    parser.add_argument("--overwrite-package", action="store_true")
    return parser


def _print_result(result: InstalledStationLogoResult) -> None:
    print(f"Station: {result.station_base}")
    print(f"Target: {result.target.value}")
    print(f"Destination: {result.destination_directory}")
    print(
        f"Color canvas: {result.color_canvas.width}x{result.color_canvas.height} "
        f"{result.color_canvas.format_name}"
    )
    print(
        "Noncolored canvas: "
        f"{result.noncolored_canvas.width}x{result.noncolored_canvas.height} "
        f"{result.noncolored_canvas.format_name}"
    )
    if result.package_directory is not None:
        print(f"Package: {result.package_directory}")
    for item in result.installed_files:
        print(f"Installed: {item.destination_path}")
        if item.backup_path is not None:
            print(f"Backup: {item.backup_path}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = install_station_logo_from_image(
        args.game_root,
        args.target,
        args.station,
        args.image,
        use_direct=args.direct,
        fit_mode=args.fit,
        padding_ratio=args.padding,
        quality=args.quality,
        package_directory=args.package_directory,
        overwrite_package=args.overwrite_package,
    )
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
