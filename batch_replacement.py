"""Transactional replacement for multiple existing GTA IV radio-track slots."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from pydub import AudioSegment

from audio_utils import replace_special_audio, update_song_duration
from core.audio_history import capture_audio_state, discard_audio_snapshot
from core.rpf import RPFParser

ProgressCallback = Callable[[int], None]
CancellationCallback = Callable[[], bool]


class BatchReplacementCancelled(RuntimeError):
    """Raised when a batch transaction is cancelled before commit."""


@dataclass(frozen=True)
class BatchReplacementResult:
    rpf_path: Path
    dat15_path: Path
    replaced_count: int
    rpf_backup_path: Path | None
    dat15_backup_path: Path | None


def _emit_progress(callback: ProgressCallback | None, value: int) -> None:
    if callback is not None:
        callback(value)


def _check_cancelled(callback: CancellationCallback | None) -> None:
    if callback is not None and callback():
        raise BatchReplacementCancelled("Batch replacement cancelled before commit.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_if_exists(path: Path | None) -> None:
    if path is not None:
        path.unlink(missing_ok=True)


def _staged_copy(source_path: Path, target_path: Path, suffix: str) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        prefix=".gtaiv_toolkit_batch_",
        suffix=suffix,
        dir=target_path.parent,
    )
    os.close(descriptor)
    staged_path = Path(staged_name)
    shutil.copy2(source_path, staged_path)
    return staged_path


def _unique_backup_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = Path(f"{path}.backup-{timestamp}")
    counter = 1
    while candidate.exists():
        candidate = Path(f"{path}.backup-{timestamp}-{counter}")
        counter += 1
    return candidate


def _create_direct_backups(rpf_path: Path, dat15_path: Path) -> tuple[Path, Path]:
    rpf_backup = _unique_backup_path(rpf_path)
    dat15_backup = _unique_backup_path(dat15_path)
    try:
        shutil.copy2(rpf_path, rpf_backup)
        shutil.copy2(dat15_path, dat15_backup)
    except Exception:
        _remove_if_exists(rpf_backup)
        _remove_if_exists(dat15_backup)
        raise
    return rpf_backup, dat15_backup


def _resolve_paths(
    gtaiv_path: Path,
    selected_radio: str,
    use_direct: bool,
) -> tuple[Path, Path, Path, Path]:
    original_rpf = gtaiv_path / "pc" / "audio" / "sfx" / selected_radio
    original_dat15 = gtaiv_path / "pc" / "audio" / "config" / "sounds.dat15"

    if use_direct:
        return original_rpf, original_dat15, original_rpf, original_dat15

    target_rpf = gtaiv_path / "update" / "pc" / "audio" / "sfx" / selected_radio
    target_dat15 = gtaiv_path / "update" / "pc" / "audio" / "config" / "sounds.dat15"
    source_rpf = target_rpf if target_rpf.is_file() else original_rpf
    source_dat15 = target_dat15 if target_dat15.is_file() else original_dat15
    return source_rpf, source_dat15, target_rpf, target_dat15


def _default_duration_reader(path: Path) -> int:
    return len(AudioSegment.from_file(str(path)))


def _validated_mappings(
    mappings: Iterable[tuple[str, str | os.PathLike[str]]],
) -> list[tuple[str, Path]]:
    prepared: list[tuple[str, Path]] = []
    seen_targets: set[str] = set()

    for raw_song_name, raw_audio_path in mappings:
        song_name = str(raw_song_name).strip()
        if not song_name or Path(song_name).name != song_name:
            raise ValueError("Every target track must be a filename without directories.")

        identity = song_name.casefold()
        if identity in seen_targets:
            raise ValueError(f"Duplicate target track in batch: {song_name}")
        seen_targets.add(identity)

        audio_path = Path(raw_audio_path).expanduser().resolve()
        if not audio_path.is_file():
            raise FileNotFoundError(f"Replacement audio not found: {audio_path}")
        prepared.append((song_name, audio_path))

    if not prepared:
        raise ValueError("No track mappings were provided.")
    return prepared


def replace_batch_transactional(
    gtaiv_path: str | os.PathLike[str],
    selected_radio: str,
    mappings: Iterable[tuple[str, str | os.PathLike[str]]],
    use_direct: bool,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation_callback: CancellationCallback | None = None,
    parser_factory=None,
    audio_replacer=None,
    duration_updater=None,
    duration_reader=None,
    replace_file=None,
) -> BatchReplacementResult:
    """Stage, verify, and atomically commit a multi-track replacement batch."""

    root = Path(gtaiv_path).expanduser().resolve()
    radio_file = selected_radio.strip()
    if not radio_file or Path(radio_file).name != radio_file:
        raise ValueError("selected_radio must be an RPF filename without directories")
    if not radio_file.casefold().endswith(".rpf"):
        raise ValueError("selected_radio must name an RPF file")

    prepared_mappings = _validated_mappings(mappings)
    exe_path = root / "GTAIV.exe"
    if not exe_path.is_file():
        raise FileNotFoundError(f"GTAIV.exe not found: {exe_path}")

    source_rpf, source_dat15, target_rpf, target_dat15 = _resolve_paths(
        root,
        radio_file,
        use_direct,
    )
    if not source_rpf.is_file():
        raise FileNotFoundError(f"Radio archive not found: {source_rpf}")
    if not source_dat15.is_file():
        raise FileNotFoundError(f"sounds.dat15 not found: {source_dat15}")

    parser_factory = RPFParser if parser_factory is None else parser_factory
    audio_replacer = (
        replace_special_audio if audio_replacer is None else audio_replacer
    )
    duration_updater = (
        update_song_duration if duration_updater is None else duration_updater
    )
    duration_reader = (
        _default_duration_reader if duration_reader is None else duration_reader
    )
    replace_file = os.replace if replace_file is None else replace_file

    staged_rpf: Path | None = None
    staged_dat15: Path | None = None
    staged_dat15_backup: Path | None = None
    rollback_rpf: Path | None = None
    rollback_dat15: Path | None = None
    rpf_backup: Path | None = None
    dat15_backup: Path | None = None
    history_snapshot = None
    rpf_existed_before = target_rpf.is_file()
    dat15_existed_before = target_dat15.is_file()
    committed = False

    try:
        _check_cancelled(cancellation_callback)
        staged_rpf = _staged_copy(source_rpf, target_rpf, ".rpf")
        staged_dat15 = _staged_copy(source_dat15, target_dat15, ".dat15")
        staged_dat15_backup = Path(f"{staged_dat15}_backup")
        _emit_progress(progress_callback, 5)

        parser = parser_factory(str(staged_rpf), str(exe_path))
        radio_name = radio_file[:-4].upper()
        total = len(prepared_mappings)
        prepared_tracks: list[tuple[str, str, Path, int, str]] = []

        with tempfile.TemporaryDirectory(prefix="gtaiv_batch_replace_") as work_dir_name:
            work_dir = Path(work_dir_name)
            for index, (song_name, audio_path) in enumerate(prepared_mappings):
                _check_cancelled(cancellation_callback)
                track_dir = work_dir / f"track_{index:03d}"
                track_dir.mkdir()
                full_song_path = f"{radio_name}/{song_name}"
                parser.extract_file(full_song_path, str(track_dir))

                extracted_file = track_dir / song_name
                if not extracted_file.is_file():
                    raise FileNotFoundError(
                        f"Selected track was not extracted: {extracted_file}"
                    )
                audio_replacer(str(extracted_file), str(audio_path))

                capacity = parser.get_file_capacity(full_song_path)
                replacement_size = extracted_file.stat().st_size
                if replacement_size > capacity:
                    print(
                        f"Replacement for {song_name} exceeds its current slot by "
                        f"{replacement_size - capacity} bytes; it will be relocated "
                        "inside the staged RPF."
                    )

                processed_wav = Path(f"{extracted_file}.wav")
                if not processed_wav.is_file():
                    raise FileNotFoundError(
                        f"Processed WAV was not generated for {song_name}: {processed_wav}"
                    )
                duration_ms = int(duration_reader(processed_wav))
                if duration_ms <= 0:
                    raise ValueError(
                        f"Replacement audio duration must be positive: {song_name}"
                    )
                prepared_tracks.append(
                    (
                        song_name,
                        full_song_path,
                        extracted_file,
                        duration_ms,
                        _sha256(extracted_file),
                    )
                )
                _emit_progress(
                    progress_callback,
                    5 + int(((index + 1) / total) * 50),
                )

            _check_cancelled(cancellation_callback)
            for index, prepared_track in enumerate(prepared_tracks):
                song_name, full_song_path, extracted_file, duration_ms, _ = prepared_track
                duration_updater(
                    str(root),
                    radio_name,
                    song_name,
                    duration_ms,
                    dat15_path=str(staged_dat15),
                )
                _remove_if_exists(staged_dat15_backup)
                parser.add_file(str(extracted_file), full_song_path)
                _emit_progress(
                    progress_callback,
                    55 + int(((index + 1) / total) * 25),
                )

            verification_parser = parser_factory(str(staged_rpf), str(exe_path))
            verification_dir = work_dir / "verification"
            verification_dir.mkdir()
            for index, prepared_track in enumerate(prepared_tracks):
                song_name, full_song_path, _, _, expected_sha256 = prepared_track
                verification_parser.extract_file(full_song_path, str(verification_dir))
                verified_file = verification_dir / song_name
                if not verified_file.is_file():
                    raise FileNotFoundError(
                        f"Staged verification extract was not generated: {verified_file}"
                    )
                if _sha256(verified_file) != expected_sha256:
                    raise RuntimeError(
                        f"Staged RPF verification failed for {song_name}: "
                        "the extracted bytes do not match the packed replacement."
                    )
                _emit_progress(
                    progress_callback,
                    80 + int(((index + 1) / total) * 10),
                )

        _check_cancelled(cancellation_callback)

        history_snapshot = capture_audio_state(
            root,
            radio_file,
            use_direct,
            reason="batch replacement",
        )

        if use_direct:
            rpf_backup, dat15_backup = _create_direct_backups(
                target_rpf,
                target_dat15,
            )
            rollback_rpf = rpf_backup
            rollback_dat15 = dat15_backup
        else:
            if rpf_existed_before:
                rollback_rpf = _staged_copy(target_rpf, target_rpf, ".rollback.rpf")
            if dat15_existed_before:
                rollback_dat15 = _staged_copy(
                    target_dat15,
                    target_dat15,
                    ".rollback.dat15",
                )

        try:
            replace_file(str(staged_rpf), str(target_rpf))
            staged_rpf = None
            replace_file(str(staged_dat15), str(target_dat15))
            staged_dat15 = None
            committed = True
        except Exception:
            if rpf_existed_before and rollback_rpf is not None:
                shutil.copy2(rollback_rpf, target_rpf)
            else:
                _remove_if_exists(target_rpf)
            if dat15_existed_before and rollback_dat15 is not None:
                shutil.copy2(rollback_dat15, target_dat15)
            else:
                _remove_if_exists(target_dat15)
            if use_direct:
                _remove_if_exists(rpf_backup)
                _remove_if_exists(dat15_backup)
                rpf_backup = None
                dat15_backup = None
            raise

        _emit_progress(progress_callback, 100)
        return BatchReplacementResult(
            rpf_path=target_rpf.resolve(),
            dat15_path=target_dat15.resolve(),
            replaced_count=total,
            rpf_backup_path=rpf_backup.resolve() if rpf_backup else None,
            dat15_backup_path=dat15_backup.resolve() if dat15_backup else None,
        )
    finally:
        _remove_if_exists(staged_rpf)
        _remove_if_exists(staged_dat15)
        _remove_if_exists(staged_dat15_backup)
        if not use_direct:
            _remove_if_exists(rollback_rpf)
            _remove_if_exists(rollback_dat15)
        if not committed:
            discard_audio_snapshot(history_snapshot)
