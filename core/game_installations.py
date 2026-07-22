"""Discover and validate local Grand Theft Auto IV installations."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

_STEAM_GAME_PATHS = (
    Path("steamapps/common/Grand Theft Auto IV/GTAIV"),
    Path("steamapps/common/Grand Theft Auto IV"),
    Path("steamapps/common/Grand Theft Auto IV Complete Edition/GTAIV"),
    Path("steamapps/common/Grand Theft Auto IV Complete Edition"),
)


@dataclass(frozen=True)
class DetectedInstallation:
    path: Path
    source: str

    @property
    def display_name(self) -> str:
        return f"{self.path} — {self.source}"


def is_gtaiv_installation(path: str | os.PathLike[str]) -> bool:
    root = Path(path).expanduser()
    return (
        root.is_dir()
        and (root / "GTAIV.exe").is_file()
        and (root / "pc" / "audio" / "sfx").is_dir()
    )


def _unescape_vdf(value: str) -> str:
    return value.replace(r"\\", "\\").replace(r'\"', '"')


def parse_steam_libraryfolders(text: str) -> tuple[Path, ...]:
    """Return library roots from modern and legacy ``libraryfolders.vdf`` text."""

    roots: list[Path] = []
    seen: set[str] = set()
    quoted = re.compile(r'"((?:\\.|[^"\\])*)"')

    for line in text.splitlines():
        fields = [_unescape_vdf(match) for match in quoted.findall(line)]
        if len(fields) < 2:
            continue

        key, value = fields[0].strip(), fields[1].strip()
        modern_path = key.casefold() == "path"
        legacy_path = key.isdigit() and bool(
            re.match(r"^[A-Za-z]:[\\/]", value) or "/" in value or "\\" in value
        )
        if not modern_path and not legacy_path:
            continue

        candidate = Path(value).expanduser()
        identity = os.path.normcase(str(candidate)).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        roots.append(candidate)

    return tuple(roots)


def _registry_steam_roots() -> tuple[Path, ...]:
    if os.name != "nt":
        return ()

    try:
        import winreg
    except ImportError:
        return ()

    lookups = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    roots: list[Path] = []
    for hive, key_name, value_name in lookups:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        if value:
            roots.append(Path(str(value)).expanduser())
    return tuple(roots)


def _default_steam_roots(environment: Mapping[str, str]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for key in ("STEAM_PATH", "PROGRAMFILES(X86)", "PROGRAMFILES"):
        value = environment.get(key, "").strip()
        if not value:
            continue
        root = Path(value).expanduser()
        if key != "STEAM_PATH":
            root /= "Steam"
        roots.append(root)
    roots.extend(_registry_steam_roots())
    return tuple(roots)


def _steam_library_roots(steam_root: Path) -> tuple[Path, ...]:
    roots = [steam_root]
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return tuple(roots)
    roots.extend(parse_steam_libraryfolders(text))
    return tuple(roots)


def _common_launcher_candidates(environment: Mapping[str, str]) -> tuple[tuple[Path, str], ...]:
    candidates: list[tuple[Path, str]] = []
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        value = environment.get(key, "").strip()
        if not value:
            continue
        root = Path(value).expanduser()
        candidates.extend(
            (
                (root / "Rockstar Games" / "Grand Theft Auto IV", "Rockstar Games Launcher"),
                (root / "Epic Games" / "GTAIV", "Epic Games Launcher"),
            )
        )
    return tuple(candidates)


def _expanded_candidate_paths(path: Path) -> tuple[Path, ...]:
    return (path, path / "GTAIV")


def discover_gtaiv_installations(
    *,
    additional_candidates: Iterable[str | os.PathLike[str]] = (),
    environment: Mapping[str, str] | None = None,
    steam_roots: Iterable[str | os.PathLike[str]] | None = None,
) -> tuple[DetectedInstallation, ...]:
    """Return valid installations in deterministic preference order."""

    env = os.environ if environment is None else environment
    raw_candidates: list[tuple[Path, str]] = []

    for candidate in additional_candidates:
        raw_candidates.append((Path(candidate).expanduser(), "Saved or selected path"))

    configured = env.get("GTAIV_PATH", "").strip()
    if configured:
        raw_candidates.append((Path(configured).expanduser(), "GTAIV_PATH"))

    configured_steam_roots = (
        tuple(Path(root).expanduser() for root in steam_roots)
        if steam_roots is not None
        else _default_steam_roots(env)
    )
    for steam_root in configured_steam_roots:
        for library_root in _steam_library_roots(steam_root):
            for relative_path in _STEAM_GAME_PATHS:
                raw_candidates.append((library_root / relative_path, "Steam"))

    raw_candidates.extend(_common_launcher_candidates(env))

    detected: list[DetectedInstallation] = []
    seen: set[str] = set()
    for raw_path, source in raw_candidates:
        for candidate in _expanded_candidate_paths(raw_path):
            if not is_gtaiv_installation(candidate):
                continue
            resolved = candidate.resolve()
            identity = os.path.normcase(str(resolved)).casefold()
            if identity in seen:
                break
            seen.add(identity)
            detected.append(DetectedInstallation(path=resolved, source=source))
            break

    return tuple(detected)
