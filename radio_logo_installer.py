from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable


KNOWN_RADIO_LOGO_WTD_NAMES = frozenset(
    {
        "radio_hud.wtd",
        "radio_hud_colored.wtd",
        "radio_hud_colored_eflc.wtd",
        "radio_hud_noncolored.wtd",
        "radio_hud_noncolored_eflc.wtd",
    }
)


class RadioLogoTarget(str, Enum):
    GTA_IV = "gta_iv"
    TLAD = "tlad"
    TBOGT = "tbogt"


_TARGET_RELATIVE_DIRECTORIES = {
    RadioLogoTarget.GTA_IV: Path("pc") / "textures",
    RadioLogoTarget.TLAD: Path("TLAD") / "pc" / "textures",
    RadioLogoTarget.TBOGT: Path("TBoGT") / "pc" / "textures",
}


class RadioLogoInstallError(RuntimeError):
    """Raised when a radio-logo pack cannot be installed safely."""


@dataclass(frozen=True)
class InstalledRadioLogo:
    source_path: str
    destination_path: str
    backup_path: str | None


def _coerce_target(target: RadioLogoTarget | str) -> RadioLogoTarget:
    try:
        return target if isinstance(target, RadioLogoTarget) else RadioLogoTarget(target)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in RadioLogoTarget)
        raise ValueError(f"Unknown radio-logo target {target!r}; expected one of: {allowed}.") from exc


def get_radio_logo_destination_dir(
    gtaiv_path: str | os.PathLike[str],
    target: RadioLogoTarget | str,
    *,
    use_direct: bool,
) -> Path:
    """Return the destination directory for one game/episode target."""
    game_root = Path(gtaiv_path).expanduser().resolve()
    relative_directory = _TARGET_RELATIVE_DIRECTORIES[_coerce_target(target)]
    return game_root / relative_directory if use_direct else game_root / "update" / relative_directory


def _original_target_dir(
    gtaiv_path: str | os.PathLike[str],
    target: RadioLogoTarget | str,
) -> Path:
    game_root = Path(gtaiv_path).expanduser().resolve()
    return game_root / _TARGET_RELATIVE_DIRECTORIES[_coerce_target(target)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _timestamped_backup_path(destination: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return destination.with_name(f"{destination.name}.backup-{timestamp}")


def _temporary_copy(source: Path, directory: Path, *, prefix: str, suffix: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copy2(source, temporary_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _atomic_replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _validated_sources(source_files: Iterable[str | os.PathLike[str]], destination_dir: Path) -> list[Path]:
    sources = [Path(source).expanduser().resolve() for source in source_files]
    if not sources:
        raise ValueError("Select at least one WTD file to install.")

    validated: list[Path] = []
    seen_names: set[str] = set()
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"Radio-logo WTD file not found: {source}")
        if source.stat().st_size <= 0:
            raise ValueError(f"Radio-logo WTD file is empty: {source}")

        normalized_name = source.name.casefold()
        if normalized_name not in KNOWN_RADIO_LOGO_WTD_NAMES:
            allowed = ", ".join(sorted(KNOWN_RADIO_LOGO_WTD_NAMES))
            raise ValueError(
                f"Unsupported radio-logo WTD filename {source.name!r}. "
                f"Expected one of: {allowed}."
            )
        if normalized_name in seen_names:
            raise ValueError(f"Duplicate radio-logo WTD filename selected: {source.name}")

        destination = destination_dir / normalized_name
        if _same_path(source, destination):
            raise ValueError(f"Source file is already the selected destination: {source}")

        seen_names.add(normalized_name)
        validated.append(source)

    return validated


def install_radio_logo_pack(
    gtaiv_path: str | os.PathLike[str],
    source_files: Iterable[str | os.PathLike[str]],
    target: RadioLogoTarget | str,
    *,
    use_direct: bool,
) -> list[InstalledRadioLogo]:
    """Install one or more known radio HUD WTD files transactionally.

    Direct mode writes to the vanilla game/episode texture directory. FusionFix
    mode writes to the matching path under ``update``. All files are staged and
    byte-verified before the first destination is replaced. Existing destination
    files receive timestamped backups only after the whole commit verifies.
    """
    game_root = Path(gtaiv_path).expanduser().resolve()
    if not game_root.is_dir():
        raise FileNotFoundError(f"GTA IV directory not found: {game_root}")

    original_directory = _original_target_dir(game_root, target)
    if not original_directory.is_dir():
        raise FileNotFoundError(
            f"The selected GTA IV target is not installed: {original_directory}"
        )

    destination_dir = get_radio_logo_destination_dir(game_root, target, use_direct=use_direct)
    sources = _validated_sources(source_files, destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    staged: dict[Path, Path | None] = {}
    rollback: dict[Path, Path | None] = {}
    expected_hashes: dict[Path, str] = {}
    source_by_destination: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}

    try:
        for source in sources:
            destination = destination_dir / source.name.casefold()
            staged_path = _temporary_copy(
                source,
                destination_dir,
                prefix=".gtaiv_toolkit_logo_stage_",
                suffix=".wtd",
            )
            expected_hash = _sha256(source)
            if _sha256(staged_path) != expected_hash:
                raise RadioLogoInstallError(
                    f"Staged WTD verification failed before commit: {source.name}"
                )

            staged[destination] = staged_path
            expected_hashes[destination] = expected_hash
            source_by_destination[destination] = source

            rollback[destination] = (
                _temporary_copy(
                    destination,
                    destination_dir,
                    prefix=".gtaiv_toolkit_logo_rollback_",
                    suffix=".wtd",
                )
                if destination.exists()
                else None
            )

        try:
            for destination, staged_path in staged.items():
                if staged_path is None:
                    raise RadioLogoInstallError(f"Missing staged WTD before commit: {destination.name}")
                _atomic_replace(staged_path, destination)
                staged[destination] = None

            for destination, expected_hash in expected_hashes.items():
                if not destination.is_file() or _sha256(destination) != expected_hash:
                    raise RadioLogoInstallError(
                        f"Installed WTD verification failed: {destination.name}"
                    )

            for destination, rollback_path in rollback.items():
                if rollback_path is None:
                    backups[destination] = None
                    continue
                backup_path = _timestamped_backup_path(destination)
                shutil.copy2(rollback_path, backup_path)
                backups[destination] = backup_path
        except Exception as exc:
            for backup_path in backups.values():
                if backup_path is not None:
                    backup_path.unlink(missing_ok=True)

            rollback_errors = []
            for destination, rollback_path in reversed(list(rollback.items())):
                try:
                    if rollback_path is None:
                        destination.unlink(missing_ok=True)
                    elif rollback_path.exists():
                        _atomic_replace(rollback_path, destination)
                        rollback[destination] = None
                except Exception as rollback_exc:  # pragma: no cover - catastrophic filesystem failure
                    rollback_errors.append(f"{destination}: {rollback_exc}")

            detail = f"Radio-logo installation failed and was rolled back: {exc}"
            if rollback_errors:
                detail += "\nRollback errors: " + "; ".join(rollback_errors)
            raise RadioLogoInstallError(detail) from exc

        return [
            InstalledRadioLogo(
                source_path=str(source_by_destination[destination]),
                destination_path=str(destination),
                backup_path=str(backups[destination]) if backups[destination] else None,
            )
            for destination in staged
        ]
    finally:
        for path in staged.values():
            if path is not None:
                path.unlink(missing_ok=True)
        for path in rollback.values():
            if path is not None:
                path.unlink(missing_ok=True)
