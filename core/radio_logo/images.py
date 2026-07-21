from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


SUPPORTED_LOGO_IMAGE_EXTENSIONS = frozenset(
    {".png", ".webp", ".jpg", ".jpeg", ".bmp", ".tga"}
)


class LogoImageError(ValueError):
    """Raised when a source logo image cannot be prepared safely."""


class LogoFitMode(str, Enum):
    FIT = "fit"
    FILL = "fill"
    STRETCH = "stretch"


@dataclass(frozen=True)
class LogoImageInfo:
    path: str
    width: int
    height: int
    mode: str
    has_transparency: bool
    aspect_ratio: float
    file_size: int


@dataclass(frozen=True)
class PreparedLogoImage:
    source_path: str
    output_path: str
    source_width: int
    source_height: int
    width: int
    height: int
    fit_mode: LogoFitMode
    padding_ratio: float
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coerce_dimension(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}.")
    return value


def _coerce_fit_mode(value: LogoFitMode | str) -> LogoFitMode:
    try:
        return value if isinstance(value, LogoFitMode) else LogoFitMode(value)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in LogoFitMode)
        raise ValueError(f"Unknown logo fit mode {value!r}; expected one of: {allowed}.") from exc


def _coerce_padding_ratio(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"padding_ratio must be a number, got {value!r}.")
    value = float(value)
    if not 0.0 <= value < 0.5:
        raise ValueError("padding_ratio must be in the range [0.0, 0.5).")
    return value


def _has_transparency(image: Image.Image) -> bool:
    if image.mode in {"RGBA", "LA"}:
        alpha = image.getchannel("A")
        minimum, _ = alpha.getextrema()
        return minimum < 255
    if image.mode == "P" and "transparency" in image.info:
        return True
    return False


def inspect_logo_image(source_path: str | os.PathLike[str]) -> LogoImageInfo:
    """Read basic source-image metadata without modifying the file."""
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Logo image not found: {source}")
    if source.stat().st_size <= 0:
        raise LogoImageError(f"Logo image is empty: {source}")
    if source.suffix.casefold() not in SUPPORTED_LOGO_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_LOGO_IMAGE_EXTENSIONS))
        raise LogoImageError(
            f"Unsupported logo image extension {source.suffix or '<none>'!r}; "
            f"expected one of: {allowed}."
        )

    try:
        with Image.open(source) as image:
            image.load()
            width, height = image.size
            mode = image.mode
            has_transparency = _has_transparency(image)
    except (UnidentifiedImageError, OSError) as exc:
        raise LogoImageError(f"Could not decode logo image: {source}") from exc

    if width <= 0 or height <= 0:
        raise LogoImageError(f"Logo image has invalid dimensions: {width}x{height}.")

    return LogoImageInfo(
        path=str(source),
        width=width,
        height=height,
        mode=mode,
        has_transparency=has_transparency,
        aspect_ratio=width / height,
        file_size=source.stat().st_size,
    )


def reduced_aspect_ratio(width: int, height: int) -> tuple[int, int]:
    width = _coerce_dimension(width, "width")
    height = _coerce_dimension(height, "height")
    divisor = math.gcd(width, height)
    return width // divisor, height // divisor


def format_logo_requirements(width: int, height: int) -> str:
    """Return user-facing source-image guidance for one target texture."""
    width = _coerce_dimension(width, "width")
    height = _coerce_dimension(height, "height")
    ratio_width, ratio_height = reduced_aspect_ratio(width, height)
    return (
        f"Required canvas: {width} x {height} px\n"
        f"Aspect ratio: {ratio_width}:{ratio_height}\n"
        "Recommended source: PNG or WebP with transparency, using the same aspect ratio.\n"
        f"Use at least {width} x {height} px; larger images will be downscaled."
    )


def _fit_image(
    image: Image.Image,
    target_size: tuple[int, int],
    padding_ratio: float,
) -> Image.Image:
    target_width, target_height = target_size
    content_width = max(1, round(target_width * (1.0 - 2.0 * padding_ratio)))
    content_height = max(1, round(target_height * (1.0 - 2.0 * padding_ratio)))
    resized = ImageOps.contain(
        image,
        (content_width, content_height),
        method=Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
    position = (
        (target_width - resized.width) // 2,
        (target_height - resized.height) // 2,
    )
    canvas.alpha_composite(resized, dest=position)
    return canvas


def _fill_image(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(
        image,
        target_size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def prepare_logo_image(
    source_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    width: int,
    height: int,
    *,
    fit_mode: LogoFitMode | str = LogoFitMode.FIT,
    padding_ratio: float = 0.0,
) -> PreparedLogoImage:
    """Normalize a user image to an RGBA PNG matching a target texture canvas.

    ``FIT`` preserves the complete image and pads with transparent pixels.
    ``FILL`` preserves aspect ratio while center-cropping to the canvas.
    ``STRETCH`` resizes directly and may distort the source.
    """
    width = _coerce_dimension(width, "width")
    height = _coerce_dimension(height, "height")
    mode = _coerce_fit_mode(fit_mode)
    padding = _coerce_padding_ratio(padding_ratio)
    if mode is not LogoFitMode.FIT and padding != 0.0:
        raise ValueError("padding_ratio is only supported with fit mode.")

    info = inspect_logo_image(source_path)
    source = Path(info.path)
    output = Path(output_path).expanduser().resolve()
    if output.suffix.casefold() != ".png":
        raise ValueError("Prepared logo output must use the .png extension.")
    if os.path.normcase(str(source)) == os.path.normcase(str(output)):
        raise ValueError("Prepared logo output must not overwrite the source image.")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")

    try:
        with Image.open(source) as opened:
            image = opened.convert("RGBA")

        target_size = (width, height)
        if mode is LogoFitMode.FIT:
            prepared = _fit_image(image, target_size, padding)
        elif mode is LogoFitMode.FILL:
            prepared = _fill_image(image, target_size)
        else:
            prepared = image.resize(target_size, Image.Resampling.LANCZOS)

        prepared.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    with Image.open(output) as verified:
        verified.load()
        if verified.mode != "RGBA" or verified.size != (width, height):
            raise LogoImageError(
                f"Prepared logo verification failed: expected RGBA {width}x{height}, "
                f"got {verified.mode} {verified.width}x{verified.height}."
            )

    return PreparedLogoImage(
        source_path=str(source),
        output_path=str(output),
        source_width=info.width,
        source_height=info.height,
        width=width,
        height=height,
        fit_mode=mode,
        padding_ratio=padding,
        sha256=_sha256(output),
    )
