"""Build validated GTA IV radio-logo WTD packs from one user image."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .images import LogoFitMode, prepare_logo_image
from .installer import (
    KNOWN_RADIO_LOGO_WTD_NAMES,
    RadioLogoTarget,
    get_radio_logo_destination_dir,
)
from .payload_patcher import replace_texture_payloads_from_images
from .wtd import WTDArchive, WTDTexture, read_wtd


_STATION_BASE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class StationLogoPackError(RuntimeError):
    """Raised when a station logo pack cannot be planned or built safely."""


@dataclass(frozen=True)
class TextureCanvas:
    """Metadata that must agree for one texture variant across all WTD files."""

    width: int
    height: int
    format_name: str
    mip_count: int


@dataclass(frozen=True)
class WtdPatchPlan:
    """One source WTD and the texture names that must be replaced in it."""

    source_path: Path
    output_name: str
    texture_names: tuple[str, ...]


@dataclass(frozen=True)
class StationLogoPlan:
    """Resolved source WTD files and target texture metadata for one station."""

    game_root: Path
    target: RadioLogoTarget
    station_base: str
    color_texture_name: str
    noncolored_texture_name: str
    color_canvas: TextureCanvas
    noncolored_canvas: TextureCanvas
    wtd_files: tuple[WtdPatchPlan, ...]


@dataclass(frozen=True)
class BuiltStationLogoWtd:
    """One generated WTD in a station logo pack."""

    source_path: Path
    output_path: Path
    replaced_textures: tuple[str, ...]
    sha256: str
    size: int


@dataclass(frozen=True)
class StationLogoPackResult:
    """Result of generating all WTD files and previews for one station."""

    plan: StationLogoPlan
    output_directory: Path
    color_preview_path: Path
    noncolored_preview_path: Path
    wtd_files: tuple[BuiltStationLogoWtd, ...]


@dataclass(frozen=True)
class StationLogoPreviewResult:
    """Prepared color and noncolored preview images for one station."""

    plan: StationLogoPlan
    color_preview_path: Path
    noncolored_preview_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_station_base(value: str) -> str:
    """Normalise a station texture base such as ``vladivostok``."""

    if not isinstance(value, str):
        raise TypeError("station_base must be a string")
    result = value.strip().casefold()
    for suffix in ("_col", "_bw"):
        if result.endswith(suffix):
            result = result[: -len(suffix)]
            break
    if not result or not _STATION_BASE_PATTERN.fullmatch(result):
        raise ValueError(
            "station_base must contain lowercase letters, digits, or underscores "
            "and must not contain path separators"
        )
    return result


def _coerce_target(target: RadioLogoTarget | str) -> RadioLogoTarget:
    try:
        return target if isinstance(target, RadioLogoTarget) else RadioLogoTarget(target)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in RadioLogoTarget)
        raise ValueError(
            f"unknown radio-logo target {target!r}; expected one of: {allowed}"
        ) from exc


def _texture_canvas(texture: WTDTexture) -> TextureCanvas:
    return TextureCanvas(
        width=texture.width,
        height=texture.height,
        format_name=texture.format_name,
        mip_count=texture.mip_count,
    )


def _find_texture(archive: WTDArchive, name: str) -> WTDTexture | None:
    matches = [texture for texture in archive.textures if texture.name.casefold() == name]
    if len(matches) > 1:
        raise StationLogoPackError(
            f"{archive.path.name} contains duplicate texture {name!r}"
        )
    return matches[0] if matches else None


def _active_source_path(
    original_directory: Path,
    update_directory: Path,
    filename: str,
    *,
    direct_source: bool,
) -> Path | None:
    original = original_directory / filename
    if direct_source:
        return original if original.is_file() else None
    override = update_directory / filename
    if override.is_file():
        return override
    return original if original.is_file() else None


def _single_canvas(
    texture_name: str,
    occurrences: list[tuple[Path, WTDTexture]],
) -> TextureCanvas:
    if not occurrences:
        raise StationLogoPackError(
            f"texture {texture_name!r} was not found in the selected game target"
        )
    canvases = {_texture_canvas(texture) for _, texture in occurrences}
    if len(canvases) != 1:
        details = ", ".join(
            f"{path.name}={texture.width}x{texture.height}/"
            f"{texture.format_name}/mips={texture.mip_count}"
            for path, texture in occurrences
        )
        raise StationLogoPackError(
            f"texture {texture_name!r} has inconsistent metadata: {details}"
        )
    return next(iter(canvases))


def create_station_logo_plan(
    gtaiv_path: str | os.PathLike[str],
    target: RadioLogoTarget | str,
    station_base: str,
    *,
    direct_source: bool = False,
) -> StationLogoPlan:
    """Locate every active WTD containing a station's color or noncolored logo."""

    game_root = Path(gtaiv_path).expanduser().resolve()
    if not game_root.is_dir():
        raise FileNotFoundError(game_root)

    resolved_target = _coerce_target(target)
    base = normalise_station_base(station_base)
    color_name = f"{base}_col"
    noncolored_name = f"{base}_bw"

    original_directory = get_radio_logo_destination_dir(
        game_root,
        resolved_target,
        use_direct=True,
    )
    if not original_directory.is_dir():
        raise FileNotFoundError(
            f"the selected GTA IV target is not installed: {original_directory}"
        )
    update_directory = get_radio_logo_destination_dir(
        game_root,
        resolved_target,
        use_direct=False,
    )

    color_occurrences: list[tuple[Path, WTDTexture]] = []
    noncolored_occurrences: list[tuple[Path, WTDTexture]] = []
    file_plans: list[WtdPatchPlan] = []

    for filename in sorted(KNOWN_RADIO_LOGO_WTD_NAMES):
        source_path = _active_source_path(
            original_directory,
            update_directory,
            filename,
            direct_source=direct_source,
        )
        if source_path is None:
            continue

        archive = read_wtd(source_path)
        texture_names: list[str] = []

        color = _find_texture(archive, color_name)
        if color is not None:
            color_occurrences.append((source_path, color))
            texture_names.append(color.name)

        noncolored = _find_texture(archive, noncolored_name)
        if noncolored is not None:
            noncolored_occurrences.append((source_path, noncolored))
            texture_names.append(noncolored.name)

        if texture_names:
            file_plans.append(
                WtdPatchPlan(
                    source_path=source_path,
                    output_name=source_path.name.casefold(),
                    texture_names=tuple(texture_names),
                )
            )

    color_canvas = _single_canvas(color_name, color_occurrences)
    noncolored_canvas = _single_canvas(noncolored_name, noncolored_occurrences)

    return StationLogoPlan(
        game_root=game_root,
        target=resolved_target,
        station_base=base,
        color_texture_name=color_name,
        noncolored_texture_name=noncolored_name,
        color_canvas=color_canvas,
        noncolored_canvas=noncolored_canvas,
        wtd_files=tuple(file_plans),
    )


def list_station_logo_bases(
    gtaiv_path: str | os.PathLike[str],
    target: RadioLogoTarget | str,
    *,
    direct_source: bool = False,
) -> tuple[str, ...]:
    """List station bases that have both ``_col`` and ``_bw`` textures."""

    game_root = Path(gtaiv_path).expanduser().resolve()
    if not game_root.is_dir():
        raise FileNotFoundError(game_root)
    resolved_target = _coerce_target(target)
    original_directory = get_radio_logo_destination_dir(
        game_root, resolved_target, use_direct=True
    )
    update_directory = get_radio_logo_destination_dir(
        game_root, resolved_target, use_direct=False
    )

    color: set[str] = set()
    noncolored: set[str] = set()
    for filename in sorted(KNOWN_RADIO_LOGO_WTD_NAMES):
        source_path = _active_source_path(
            original_directory,
            update_directory,
            filename,
            direct_source=direct_source,
        )
        if source_path is None:
            continue
        for texture in read_wtd(source_path).textures:
            folded = texture.name.casefold()
            if folded.endswith("_col"):
                color.add(folded[:-4])
            elif folded.endswith("_bw"):
                noncolored.add(folded[:-3])
    return tuple(sorted(color & noncolored))


def _prepare_color_variant(
    source_image: Path,
    output_path: Path,
    canvas: TextureCanvas,
    *,
    fit_mode: LogoFitMode | str,
    padding_ratio: float,
) -> None:
    """Prepare the selected-station texture using GTA IV's HUD convention.

    Original ``*_col`` textures are opaque DXT1 images with a black
    background. Transparency for the radio wheel is supplied by the matching
    ``*_bw`` texture, so the color payload must remain ordinary opaque BC1.
    """

    prepare_logo_image(
        source_image,
        output_path,
        canvas.width,
        canvas.height,
        fit_mode=fit_mode,
        padding_ratio=padding_ratio,
    )

    temporary = output_path.with_name(f".{output_path.name}.opaque.tmp")
    try:
        with Image.open(output_path) as opened:
            rgba = opened.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
            result = Image.alpha_composite(background, rgba).convert("RGB")
            result.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _prepare_noncolored_variant(
    source_image: Path,
    output_path: Path,
    canvas: TextureCanvas,
    *,
    fit_mode: LogoFitMode | str,
    padding_ratio: float,
) -> None:
    prepare_logo_image(
        source_image,
        output_path,
        canvas.width,
        canvas.height,
        fit_mode=fit_mode,
        padding_ratio=padding_ratio,
    )

    temporary = output_path.with_name(f".{output_path.name}.grayscale.tmp")
    try:
        with Image.open(output_path) as opened:
            rgba = opened.convert("RGBA")
            gray = ImageOps.grayscale(rgba)
            alpha = rgba.getchannel("A")
            result = Image.merge("RGBA", (gray, gray, gray, alpha))
            result.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def prepare_station_logo_previews(
    plan: StationLogoPlan,
    source_image: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
    *,
    fit_mode: LogoFitMode | str = LogoFitMode.FIT,
    padding_ratio: float = 0.0,
    overwrite: bool = False,
) -> StationLogoPreviewResult:
    """Prepare the exact color and noncolored images used by the WTD workflow."""

    if not isinstance(plan, StationLogoPlan):
        raise TypeError("plan must be a StationLogoPlan")

    image_path = Path(source_image).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    output_dir = Path(output_directory).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    color_output = output_dir / f"{plan.color_texture_name}.png"
    noncolored_output = output_dir / f"{plan.noncolored_texture_name}.png"

    source_resolved = image_path.resolve()
    for destination in (color_output, noncolored_output):
        if destination.resolve() == source_resolved:
            raise ValueError("preview output must not overwrite the source image")
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)

    with tempfile.TemporaryDirectory(
        prefix=".radio-logo-preview-",
        dir=output_dir,
    ) as raw_workspace:
        workspace = Path(raw_workspace)
        staged_color = workspace / color_output.name
        staged_noncolored = workspace / noncolored_output.name

        _prepare_color_variant(
            image_path,
            staged_color,
            plan.color_canvas,
            fit_mode=fit_mode,
            padding_ratio=padding_ratio,
        )
        _prepare_noncolored_variant(
            image_path,
            staged_noncolored,
            plan.noncolored_canvas,
            fit_mode=fit_mode,
            padding_ratio=padding_ratio,
        )

        staged = {
            color_output: staged_color,
            noncolored_output: staged_noncolored,
        }
        _commit_staged_files(staged, workspace)

    return StationLogoPreviewResult(
        plan=plan,
        color_preview_path=color_output,
        noncolored_preview_path=noncolored_output,
    )


def _validate_output_paths(
    plan: StationLogoPlan,
    output_directory: Path,
    *,
    overwrite: bool,
) -> tuple[Path, Path, dict[WtdPatchPlan, Path]]:
    output_directory.mkdir(parents=True, exist_ok=True)
    preview_directory = output_directory / "preview"
    color_preview = preview_directory / f"{plan.color_texture_name}.png"
    noncolored_preview = preview_directory / f"{plan.noncolored_texture_name}.png"
    wtd_destinations = {
        file_plan: output_directory / file_plan.output_name
        for file_plan in plan.wtd_files
    }

    all_destinations = [color_preview, noncolored_preview, *wtd_destinations.values()]
    source_paths = {item.source_path.resolve() for item in plan.wtd_files}
    for destination in all_destinations:
        resolved = destination.resolve()
        if resolved in source_paths:
            raise ValueError(
                f"output pack must not overwrite an active source WTD: {resolved}"
            )
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)

    return color_preview, noncolored_preview, wtd_destinations


def _commit_staged_files(
    staged: dict[Path, Path],
    workspace: Path,
) -> None:
    rollback: dict[Path, Path | None] = {}
    committed: list[Path] = []
    try:
        for index, destination in enumerate(staged):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = workspace / f"rollback-{index}{destination.suffix}"
                shutil.copy2(destination, backup)
                rollback[destination] = backup
            else:
                rollback[destination] = None

        for destination, source in staged.items():
            os.replace(source, destination)
            committed.append(destination)
    except Exception:
        for destination in reversed(committed):
            backup = rollback[destination]
            if backup is None:
                destination.unlink(missing_ok=True)
            elif backup.exists():
                os.replace(backup, destination)
        raise


def build_station_logo_pack(
    gtaiv_path: str | os.PathLike[str],
    target: RadioLogoTarget | str,
    station_base: str,
    source_image: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
    *,
    direct_source: bool = False,
    fit_mode: LogoFitMode | str = LogoFitMode.FIT,
    padding_ratio: float = 0.0,
    quality: float = 0.9,
    overwrite: bool = False,
) -> StationLogoPackResult:
    """Create all WTD files needed to replace one station logo from one image."""

    if not 0.0 <= quality <= 1.0:
        raise ValueError("quality must be between 0.0 and 1.0")

    image_path = Path(source_image).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    plan = create_station_logo_plan(
        gtaiv_path,
        target,
        station_base,
        direct_source=direct_source,
    )
    output_dir = Path(output_directory).expanduser().resolve()
    color_preview, noncolored_preview, destinations = _validate_output_paths(
        plan,
        output_dir,
        overwrite=overwrite,
    )

    built: list[BuiltStationLogoWtd] = []
    with tempfile.TemporaryDirectory(
        prefix=".gtaiv-toolkit-station-logo-",
        dir=output_dir,
    ) as raw_workspace:
        workspace = Path(raw_workspace)
        prepared_color = workspace / f"{plan.color_texture_name}.png"
        prepared_noncolored = workspace / f"{plan.noncolored_texture_name}.png"

        _prepare_color_variant(
            image_path,
            prepared_color,
            plan.color_canvas,
            fit_mode=fit_mode,
            padding_ratio=padding_ratio,
        )
        _prepare_noncolored_variant(
            image_path,
            prepared_noncolored,
            plan.noncolored_canvas,
            fit_mode=fit_mode,
            padding_ratio=padding_ratio,
        )

        staged_files: dict[Path, Path] = {
            color_preview: workspace / "preview-color.png",
            noncolored_preview: workspace / "preview-noncolored.png",
        }
        shutil.copy2(prepared_color, staged_files[color_preview])
        shutil.copy2(prepared_noncolored, staged_files[noncolored_preview])

        for file_index, file_plan in enumerate(plan.wtd_files):
            if not file_plan.texture_names:  # defensive; plans never contain empty sets
                raise StationLogoPackError(
                    f"no textures were selected for {file_plan.source_path.name}"
                )

            replacement_images = {
                texture_name: (
                    prepared_color
                    if texture_name.casefold() == plan.color_texture_name
                    else prepared_noncolored
                )
                for texture_name in file_plan.texture_names
            }
            final_stage = workspace / f"wtd-{file_index}-{file_plan.output_name}"
            replace_texture_payloads_from_images(
                file_plan.source_path,
                final_stage,
                replacement_images,
                quality=quality,
                overwrite=True,
            )

            destination = destinations[file_plan]
            committed_stage = workspace / f"final-{file_index}-{file_plan.output_name}"
            shutil.copy2(final_stage, committed_stage)
            staged_files[destination] = committed_stage
            built.append(
                BuiltStationLogoWtd(
                    source_path=file_plan.source_path,
                    output_path=destination,
                    replaced_textures=file_plan.texture_names,
                    sha256=_sha256_file(committed_stage),
                    size=committed_stage.stat().st_size,
                )
            )

        _commit_staged_files(staged_files, workspace)

    return StationLogoPackResult(
        plan=plan,
        output_directory=output_dir,
        color_preview_path=color_preview,
        noncolored_preview_path=noncolored_preview,
        wtd_files=tuple(built),
    )


def _print_plan(plan: StationLogoPlan) -> None:
    print(f"Station: {plan.station_base}")
    print(
        f"Color: {plan.color_texture_name} — "
        f"{plan.color_canvas.width}x{plan.color_canvas.height} "
        f"{plan.color_canvas.format_name}, mips={plan.color_canvas.mip_count}"
    )
    print(
        f"Noncolored: {plan.noncolored_texture_name} — "
        f"{plan.noncolored_canvas.width}x{plan.noncolored_canvas.height} "
        f"{plan.noncolored_canvas.format_name}, "
        f"mips={plan.noncolored_canvas.mip_count}"
    )
    for file_plan in plan.wtd_files:
        print(
            f"{file_plan.source_path} -> {file_plan.output_name}: "
            f"{', '.join(file_plan.texture_names)}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a validated GTA IV radio-logo WTD pack from one image."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list replaceable station bases")
    list_parser.add_argument("game_root")
    list_parser.add_argument("target", choices=[item.value for item in RadioLogoTarget])
    list_parser.add_argument("--direct-source", action="store_true")

    plan_parser = subparsers.add_parser("plan", help="show WTD files for one station")
    plan_parser.add_argument("game_root")
    plan_parser.add_argument("target", choices=[item.value for item in RadioLogoTarget])
    plan_parser.add_argument("station")
    plan_parser.add_argument("--direct-source", action="store_true")

    build_parser = subparsers.add_parser("build", help="build a station WTD pack")
    build_parser.add_argument("game_root")
    build_parser.add_argument("target", choices=[item.value for item in RadioLogoTarget])
    build_parser.add_argument("station")
    build_parser.add_argument("image")
    build_parser.add_argument("output_directory")
    build_parser.add_argument(
        "--fit",
        choices=[item.value for item in LogoFitMode],
        default=LogoFitMode.FIT.value,
    )
    build_parser.add_argument("--padding", type=float, default=0.0)
    build_parser.add_argument("--quality", type=float, default=0.9)
    build_parser.add_argument("--direct-source", action="store_true")
    build_parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "list":
        for station in list_station_logo_bases(
            args.game_root,
            args.target,
            direct_source=args.direct_source,
        ):
            print(station)
        return 0

    if args.command == "plan":
        _print_plan(
            create_station_logo_plan(
                args.game_root,
                args.target,
                args.station,
                direct_source=args.direct_source,
            )
        )
        return 0

    result = build_station_logo_pack(
        args.game_root,
        args.target,
        args.station,
        args.image,
        args.output_directory,
        direct_source=args.direct_source,
        fit_mode=args.fit,
        padding_ratio=args.padding,
        quality=args.quality,
        overwrite=args.overwrite,
    )
    _print_plan(result.plan)
    print(f"Color preview: {result.color_preview_path}")
    print(f"Noncolored preview: {result.noncolored_preview_path}")
    for item in result.wtd_files:
        print(
            f"Built: {item.output_path} ({item.size} bytes, {item.sha256}, "
            f"textures={list(item.replaced_textures)})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
