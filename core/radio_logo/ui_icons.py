"""Render the active in-game radio-logo textures for the station picker."""

from __future__ import annotations

import hashlib
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageChops

from .installer import (
    KNOWN_RADIO_LOGO_WTD_NAMES,
    RadioLogoTarget,
    get_radio_logo_destination_dir,
)
from .wtd import WTDTexture, read_wtd, texture_to_dds

_RADIO_TO_TEXTURE_ALIASES = {
    "radio_broker": "radiobroker",
    "radio_jazz_nation": "jnr",
    "radio_liberty_rock": "lrr",
    "radio_ramjam": "ramjamfm",
    "radio_san_juan": "sanjuan",
    "radio_self_actualization": "selfactualizationfm",
    "radio_the_classics": "theclassics",
    "radio_the_journey": "thejourney",
    "radio_the_vibe": "thevibe",
    "radio_vcfm": "vicecityfm",
}


def _default_cache_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "GTAIVModdingToolkit" / "radio-icons"
    return Path(tempfile.gettempdir()) / "GTAIVModdingToolkit" / "radio-icons"


def _cache_directory(
    gtaiv_path: str | os.PathLike[str],
    *,
    use_direct: bool,
    cache_root: str | os.PathLike[str] | None,
) -> Path:
    game_root = Path(gtaiv_path).expanduser().resolve()
    installation_id = hashlib.sha256(
        os.path.normcase(str(game_root)).encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:16]
    root = Path(cache_root).expanduser().resolve() if cache_root else _default_cache_root()
    return root / installation_id / ("direct" if use_direct else "fusionfix")


def _active_wtd_paths(
    gtaiv_path: str | os.PathLike[str],
    *,
    use_direct: bool,
) -> tuple[Path, ...]:
    original_directory = get_radio_logo_destination_dir(
        gtaiv_path,
        RadioLogoTarget.GTA_IV,
        use_direct=True,
    )
    update_directory = get_radio_logo_destination_dir(
        gtaiv_path,
        RadioLogoTarget.GTA_IV,
        use_direct=False,
    )

    paths: list[Path] = []
    for filename in sorted(KNOWN_RADIO_LOGO_WTD_NAMES):
        original = original_directory / filename
        override = update_directory / filename
        source = original if use_direct else override if override.is_file() else original
        if source.is_file():
            paths.append(source)
    return tuple(paths)


def _display_icon_image(texture: WTDTexture) -> Image.Image:
    """Decode one color texture and mask its opaque black HUD background."""

    with Image.open(BytesIO(texture_to_dds(texture))) as opened:
        rgba = opened.convert("RGBA")
        rgba.load()

    red, green, blue, _ = rgba.split()
    alpha = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    rgba.putalpha(alpha)
    return rgba


def build_active_station_icon_cache(
    gtaiv_path: str | os.PathLike[str],
    *,
    use_direct: bool,
    cache_root: str | os.PathLike[str] | None = None,
) -> dict[str, Path]:
    """Render every active ``*_col`` texture to a cached transparent PNG."""

    output_directory = _cache_directory(
        gtaiv_path,
        use_direct=use_direct,
        cache_root=cache_root,
    )
    output_directory.mkdir(parents=True, exist_ok=True)

    rendered: dict[str, Path] = {}
    for wtd_path in _active_wtd_paths(gtaiv_path, use_direct=use_direct):
        archive = read_wtd(wtd_path)
        for texture in archive.textures:
            folded_name = texture.name.casefold()
            if not folded_name.endswith("_col") or not texture.extractable:
                continue

            station_base = folded_name[:-4]
            output_path = output_directory / f"{station_base}.png"
            temporary_path = output_path.with_name(f".{output_path.name}.tmp")
            try:
                image = _display_icon_image(texture)
                image.save(temporary_path, format="PNG", optimize=True)
                os.replace(temporary_path, output_path)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                continue
            rendered[station_base] = output_path

    for stale in output_directory.glob("*.png"):
        if stale.stem.casefold() not in rendered:
            stale.unlink(missing_ok=True)
    return rendered


def station_icon_candidates(radio_name: str) -> tuple[str, ...]:
    normalized = radio_name.strip().casefold()
    stem = normalized.removeprefix("radio_")
    compact = stem.replace("_", "")

    ordered = [
        _RADIO_TO_TEXTURE_ALIASES.get(normalized),
        stem,
        compact,
        f"radio{compact}",
    ]
    result: list[str] = []
    for candidate in ordered:
        if candidate and candidate not in result:
            result.append(candidate)
    return tuple(result)


def resolve_station_icon_path(
    radio_name: str,
    dynamic_icons: Mapping[str, Path],
) -> Path | None:
    normalized_icons = {key.casefold(): Path(value) for key, value in dynamic_icons.items()}
    for candidate in station_icon_candidates(radio_name):
        path = normalized_icons.get(candidate)
        if path is not None and path.is_file():
            return path
    return None
