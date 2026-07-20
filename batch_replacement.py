import hashlib
import os
import shutil
import tempfile

from pydub import AudioSegment
from PySide6.QtCore import QThread, Signal

from audio_utils import replace_special_audio, update_song_duration
from core.rpf import RPFParser
from replacement_strategy import DirectReplacementStrategy, FusionFixReplacementStrategy


class BatchReplacementCancelled(RuntimeError):
    pass


def _staged_copy(source_path: str, suffix: str) -> str:
    directory = os.path.dirname(source_path)
    os.makedirs(directory, exist_ok=True)
    descriptor, staged_path = tempfile.mkstemp(
        prefix=".gtaiv_toolkit_batch_",
        suffix=suffix,
        dir=directory,
    )
    os.close(descriptor)
    shutil.copy2(source_path, staged_path)
    return staged_path


def _remove_if_exists(path: str | None) -> None:
    if path and os.path.exists(path):
        os.remove(path)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BatchReplaceWorker(QThread):
    progress = Signal(int)
    completed = Signal(int)
    cancelled = Signal()
    error = Signal(str)

    def __init__(self, gtaiv_path, selected_radio, mappings, use_direct):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.selected_radio = selected_radio
        self.mappings = list(mappings)
        self.use_direct = use_direct
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def _check_cancelled(self):
        if self._cancel_requested:
            raise BatchReplacementCancelled("Batch replacement cancelled before commit.")

    def run(self):
        staged_rpf = None
        staged_dat15 = None
        rollback_rpf = None
        rollback_dat15 = None
        staged_dat15_backup = None
        committed = False

        strategy = (
            DirectReplacementStrategy(self.gtaiv_path)
            if self.use_direct
            else FusionFixReplacementStrategy(self.gtaiv_path)
        )
        target_rpf = strategy.get_rpf_path(self.selected_radio)
        target_dat15 = strategy.get_dat15_path()
        rpf_existed_before = os.path.exists(target_rpf)
        dat15_existed_before = os.path.exists(target_dat15)

        try:
            if not self.mappings:
                raise ValueError("No track mappings were provided.")

            strategy.prepare_rpf(self.selected_radio)
            strategy.prepare_dat15()

            staged_rpf = _staged_copy(target_rpf, ".rpf")
            staged_dat15 = _staged_copy(target_dat15, ".dat15")
            staged_dat15_backup = f"{staged_dat15}_backup"

            exe_path = os.path.join(self.gtaiv_path, "GTAIV.exe")
            parser = RPFParser(staged_rpf, exe_path)
            radio_name = self.selected_radio[:-4].upper()
            total = len(self.mappings)
            prepared_tracks = []

            with tempfile.TemporaryDirectory(prefix="gtaiv_batch_replace_") as work_dir:
                for index, (song_name, audio_path) in enumerate(self.mappings):
                    self._check_cancelled()
                    if not os.path.isfile(audio_path):
                        raise FileNotFoundError(f"Replacement audio not found: {audio_path}")

                    track_dir = os.path.join(work_dir, f"track_{index:03d}")
                    os.makedirs(track_dir, exist_ok=True)
                    full_song_path = f"{radio_name}/{song_name}"
                    parser.extract_file(full_song_path, track_dir)

                    extracted_file = os.path.join(track_dir, song_name)
                    replace_special_audio(extracted_file, audio_path)

                    capacity = parser.get_file_capacity(full_song_path)
                    replacement_size = os.path.getsize(extracted_file)
                    if replacement_size > capacity:
                        print(
                            f"Replacement for {song_name} exceeds its current slot by "
                            f"{replacement_size - capacity} bytes; it will be relocated "
                            "inside the staged RPF."
                        )

                    processed_wav = f"{extracted_file}.wav"
                    if not os.path.isfile(processed_wav):
                        raise FileNotFoundError(
                            f"Processed WAV was not generated for {song_name}: {processed_wav}"
                        )
                    duration_ms = len(AudioSegment.from_file(processed_wav))
                    prepared_tracks.append(
                        (
                            song_name,
                            full_song_path,
                            extracted_file,
                            duration_ms,
                            _sha256(extracted_file),
                        )
                    )
                    self.progress.emit(5 + int(((index + 1) / total) * 55))

                self._check_cancelled()

                for index, prepared_track in enumerate(prepared_tracks):
                    song_name, full_song_path, extracted_file, duration_ms, _ = prepared_track
                    update_song_duration(
                        self.gtaiv_path,
                        radio_name,
                        song_name,
                        duration_ms,
                        dat15_path=staged_dat15,
                    )
                    _remove_if_exists(staged_dat15_backup)
                    parser.add_file(extracted_file, full_song_path)
                    self.progress.emit(60 + int(((index + 1) / total) * 25))

                # Reopen and byte-verify every staged replacement before commit.
                verification_parser = RPFParser(staged_rpf, exe_path)
                verification_dir = os.path.join(work_dir, "verification")
                os.makedirs(verification_dir, exist_ok=True)
                for index, prepared_track in enumerate(prepared_tracks):
                    song_name, full_song_path, _, _, expected_sha256 = prepared_track
                    verification_parser.extract_file(full_song_path, verification_dir)
                    verified_path = os.path.join(verification_dir, song_name)
                    actual_sha256 = _sha256(verified_path)
                    if actual_sha256 != expected_sha256:
                        raise RuntimeError(
                            f"Staged RPF verification failed for {song_name}: "
                            "the extracted bytes do not match the packed replacement."
                        )
                    self.progress.emit(85 + int(((index + 1) / total) * 5))

            self._check_cancelled()

            rollback_rpf = _staged_copy(target_rpf, ".rollback.rpf")
            rollback_dat15 = _staged_copy(target_dat15, ".rollback.dat15")

            try:
                os.replace(staged_rpf, target_rpf)
                staged_rpf = None
                os.replace(staged_dat15, target_dat15)
                staged_dat15 = None
                committed = True
            except Exception:
                shutil.copy2(rollback_rpf, target_rpf)
                shutil.copy2(rollback_dat15, target_dat15)
                raise

            self.progress.emit(100)
            self.completed.emit(total)
        except BatchReplacementCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            _remove_if_exists(staged_rpf)
            _remove_if_exists(staged_dat15)
            _remove_if_exists(rollback_rpf)
            _remove_if_exists(rollback_dat15)
            _remove_if_exists(staged_dat15_backup)

            if not committed and not self.use_direct:
                if not rpf_existed_before:
                    _remove_if_exists(target_rpf)
                if not dat15_existed_before:
                    _remove_if_exists(target_dat15)
