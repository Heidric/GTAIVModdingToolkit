"""Persistent, transactional history for paired GTA IV radio-audio changes."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

_HISTORY_DIRECTORY = Path(".gtaiv_toolkit") / "audio-history"
_MANIFEST_NAME = "manifest.json"
_RPF_PAYLOAD_NAME = "radio.rpf"
_DAT15_PAYLOAD_NAME = "sounds.dat15"
_DEFAULT_RETENTION = 8

ReplaceFile = Callable[[str, str], None]


@dataclass(frozen=True)
class AudioHistorySnapshot:
    snapshot_id: str
    directory: Path
    created_at_utc: str
    station_file: str
    use_direct: bool
    reason: str
    rpf_was_present: bool
    dat15_was_present: bool
    rpf_size: int | None
    dat15_size: int | None

    @property
    def mode_name(self) -> str:
        return "Direct" if self.use_direct else "FusionFix"

    @property
    def rpf_payload_path(self) -> Path:
        return self.directory / _RPF_PAYLOAD_NAME

    @property
    def dat15_payload_path(self) -> Path:
        return self.directory / _DAT15_PAYLOAD_NAME


@dataclass(frozen=True)
class AudioRecoveryResult:
    restored_snapshot: AudioHistorySnapshot
    displaced_snapshot: AudioHistorySnapshot
    rpf_path: Path
    dat15_path: Path


def _validate_station_file(station_file: str) -> str:
    value = station_file.strip()
    if not value or Path(value).name != value or not value.casefold().endswith(".rpf"):
        raise ValueError("station_file must be an RPF filename without directories")
    return value


def _history_mode_directory(root: Path, use_direct: bool) -> Path:
    return root / _HISTORY_DIRECTORY / ("direct" if use_direct else "fusionfix")


def resolve_audio_target_paths(
    gtaiv_path: str | os.PathLike[str],
    station_file: str,
    use_direct: bool,
) -> tuple[Path, Path]:
    root = Path(gtaiv_path).expanduser().resolve()
    station = _validate_station_file(station_file)
    prefix = Path() if use_direct else Path("update")
    return (
        root / prefix / "pc" / "audio" / "sfx" / station,
        root / prefix / "pc" / "audio" / "config" / "sounds.dat15",
    )


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _snapshot_from_manifest(directory: Path) -> AudioHistorySnapshot:
    manifest_path = directory / _MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return AudioHistorySnapshot(
        snapshot_id=str(data["snapshot_id"]),
        directory=directory.resolve(),
        created_at_utc=str(data["created_at_utc"]),
        station_file=_validate_station_file(str(data["station_file"])),
        use_direct=bool(data["use_direct"]),
        reason=str(data.get("reason", "replacement")),
        rpf_was_present=bool(data["rpf_was_present"]),
        dat15_was_present=bool(data["dat15_was_present"]),
        rpf_size=int(data["rpf_size"]) if data.get("rpf_size") is not None else None,
        dat15_size=(
            int(data["dat15_size"]) if data.get("dat15_size") is not None else None
        ),
    )


def list_audio_snapshots(
    gtaiv_path: str | os.PathLike[str],
    use_direct: bool,
) -> tuple[AudioHistorySnapshot, ...]:
    root = Path(gtaiv_path).expanduser().resolve()
    history_directory = _history_mode_directory(root, use_direct)
    if not history_directory.is_dir():
        return ()

    snapshots: list[AudioHistorySnapshot] = []
    for candidate in history_directory.iterdir():
        if not candidate.is_dir() or candidate.name.startswith(".pending-"):
            continue
        try:
            snapshot = _snapshot_from_manifest(candidate)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if snapshot.use_direct == use_direct:
            snapshots.append(snapshot)

    snapshots.sort(
        key=lambda snapshot: (snapshot.created_at_utc, snapshot.snapshot_id),
        reverse=True,
    )
    return tuple(snapshots)


def latest_audio_snapshot(
    gtaiv_path: str | os.PathLike[str],
    use_direct: bool,
) -> AudioHistorySnapshot | None:
    snapshots = list_audio_snapshots(gtaiv_path, use_direct)
    return snapshots[0] if snapshots else None


def _prune_audio_snapshots(
    gtaiv_path: str | os.PathLike[str],
    use_direct: bool,
    retention: int,
) -> None:
    if retention <= 0:
        raise ValueError("retention must be positive")
    for snapshot in list_audio_snapshots(gtaiv_path, use_direct)[retention:]:
        shutil.rmtree(snapshot.directory, ignore_errors=True)


def capture_audio_state(
    gtaiv_path: str | os.PathLike[str],
    station_file: str,
    use_direct: bool,
    *,
    reason: str = "replacement",
    retention: int = _DEFAULT_RETENTION,
    prune: bool = True,
) -> AudioHistorySnapshot:
    """Capture the active station RPF and global ``sounds.dat15`` as one state."""

    root = Path(gtaiv_path).expanduser().resolve()
    station = _validate_station_file(station_file)
    rpf_path, dat15_path = resolve_audio_target_paths(root, station, use_direct)
    created_at = datetime.now(timezone.utc)
    snapshot_id = f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}"
    mode_directory = _history_mode_directory(root, use_direct)
    mode_directory.mkdir(parents=True, exist_ok=True)
    pending_directory = mode_directory / f".pending-{snapshot_id}"
    final_directory = mode_directory / snapshot_id
    pending_directory.mkdir()

    rpf_present = rpf_path.is_file()
    dat15_present = dat15_path.is_file()
    try:
        if rpf_present:
            _link_or_copy(rpf_path, pending_directory / _RPF_PAYLOAD_NAME)
        if dat15_present:
            _link_or_copy(dat15_path, pending_directory / _DAT15_PAYLOAD_NAME)

        manifest = {
            "snapshot_id": snapshot_id,
            "created_at_utc": created_at.isoformat(),
            "station_file": station,
            "use_direct": use_direct,
            "reason": reason.strip() or "replacement",
            "rpf_was_present": rpf_present,
            "dat15_was_present": dat15_present,
            "rpf_size": rpf_path.stat().st_size if rpf_present else None,
            "dat15_size": dat15_path.stat().st_size if dat15_present else None,
        }
        (pending_directory / _MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pending_directory.replace(final_directory)
    except Exception:
        shutil.rmtree(pending_directory, ignore_errors=True)
        raise

    snapshot = _snapshot_from_manifest(final_directory)
    if prune:
        _prune_audio_snapshots(root, use_direct, retention)
    return snapshot


def discard_audio_snapshot(snapshot: AudioHistorySnapshot | None) -> None:
    if snapshot is not None:
        shutil.rmtree(snapshot.directory, ignore_errors=True)


def _validate_snapshot_payloads(snapshot: AudioHistorySnapshot) -> None:
    if snapshot.rpf_was_present and not snapshot.rpf_payload_path.is_file():
        raise FileNotFoundError(
            f"Audio-history RPF payload is missing: {snapshot.rpf_payload_path}"
        )
    if snapshot.dat15_was_present and not snapshot.dat15_payload_path.is_file():
        raise FileNotFoundError(
            f"Audio-history sounds.dat15 payload is missing: {snapshot.dat15_payload_path}"
        )


def _staged_payload(payload: Path, target: Path, suffix: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        prefix=".gtaiv_toolkit_audio_recovery_",
        suffix=suffix,
        dir=target.parent,
    )
    os.close(descriptor)
    staged = Path(staged_name)
    shutil.copy2(payload, staged)
    return staged


def _restore_snapshot_without_history(
    snapshot: AudioHistorySnapshot,
    rpf_path: Path,
    dat15_path: Path,
) -> None:
    for was_present, payload, target, suffix in (
        (snapshot.rpf_was_present, snapshot.rpf_payload_path, rpf_path, ".rpf"),
        (
            snapshot.dat15_was_present,
            snapshot.dat15_payload_path,
            dat15_path,
            ".dat15",
        ),
    ):
        if not was_present:
            target.unlink(missing_ok=True)
            continue
        staged = _staged_payload(payload, target, suffix)
        try:
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)


def restore_latest_audio_snapshot(
    gtaiv_path: str | os.PathLike[str],
    use_direct: bool,
    *,
    replace_file: ReplaceFile | None = None,
    retention: int = _DEFAULT_RETENTION,
) -> AudioRecoveryResult:
    """Restore the newest paired audio state and retain the displaced state for redo."""

    root = Path(gtaiv_path).expanduser().resolve()
    snapshot = latest_audio_snapshot(root, use_direct)
    if snapshot is None:
        raise FileNotFoundError("No audio recovery history is available for this mode")
    _validate_snapshot_payloads(snapshot)

    rpf_path, dat15_path = resolve_audio_target_paths(
        root,
        snapshot.station_file,
        use_direct,
    )
    displaced = capture_audio_state(
        root,
        snapshot.station_file,
        use_direct,
        reason="recovery-displaced-state",
        retention=retention,
        prune=False,
    )
    replace_file = os.replace if replace_file is None else replace_file
    staged_rpf: Path | None = None
    staged_dat15: Path | None = None

    try:
        if snapshot.rpf_was_present:
            staged_rpf = _staged_payload(snapshot.rpf_payload_path, rpf_path, ".rpf")
        if snapshot.dat15_was_present:
            staged_dat15 = _staged_payload(
                snapshot.dat15_payload_path,
                dat15_path,
                ".dat15",
            )

        try:
            if snapshot.rpf_was_present:
                replace_file(str(staged_rpf), str(rpf_path))
                staged_rpf = None
            else:
                rpf_path.unlink(missing_ok=True)

            if snapshot.dat15_was_present:
                replace_file(str(staged_dat15), str(dat15_path))
                staged_dat15 = None
            else:
                dat15_path.unlink(missing_ok=True)
        except Exception:
            _restore_snapshot_without_history(displaced, rpf_path, dat15_path)
            discard_audio_snapshot(displaced)
            raise

        discard_audio_snapshot(snapshot)
        _prune_audio_snapshots(root, use_direct, retention)
        return AudioRecoveryResult(
            restored_snapshot=snapshot,
            displaced_snapshot=displaced,
            rpf_path=rpf_path.resolve(),
            dat15_path=dat15_path.resolve(),
        )
    finally:
        if staged_rpf is not None:
            staged_rpf.unlink(missing_ok=True)
        if staged_dat15 is not None:
            staged_dat15.unlink(missing_ok=True)
