from PySide6.QtWidgets import QMainWindow, QStackedWidget, QMessageBox
from pydub import AudioSegment

from ui.pages.intro import IntroPage
from ui.pages.radio_select import RadioSelectPage
from ui.pages.song_select import SongSelectPage
from ui.pages.replace import ReplacePage
from ui.pages.progress import ProgressPage
from ui.pages.batch_replace import BatchReplacePage
from ui.pages.radio_logo_install import RadioLogoInstallPage
from audio_utils import replace_special_audio, update_song_duration
from utils import install_ffmpeg, check_ffmpeg
from replacement_strategy import DirectReplacementStrategy, FusionFixReplacementStrategy
from core.rpf import RPFParser
from batch_replacement import BatchReplaceWorker
import os
import shutil
import tempfile
from PySide6.QtCore import QThread, Signal


class ReplaceSongWorker(QThread):
    progress = Signal(int)
    finished = Signal()
    error = Signal(str)

    def __init__(self, gtaiv_path, selected_radio, selected_song, new_song_path, use_direct):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.selected_radio = selected_radio
        self.selected_song = selected_song
        self.new_song_path = new_song_path
        self.use_direct = use_direct

    def run(self):
        try:
            print("Worker started")

            # Select strategy
            if self.use_direct:
                strategy = DirectReplacementStrategy(self.gtaiv_path)
                print("Using Direct Replacement Strategy")
            else:
                strategy = FusionFixReplacementStrategy(self.gtaiv_path)
                print("Using FusionFix Replacement Strategy")

            # Prepare files (copy if needed for FusionFix)
            strategy.prepare_rpf(self.selected_radio)
            strategy.prepare_dat15()

            rpf_path = strategy.get_rpf_path(self.selected_radio)
            dat15_path = strategy.get_dat15_path()

            radio_name = self.selected_radio[:-4].upper()
            full_song_path = f"{radio_name}/{self.selected_song}"
            print(f"RPF Path: {rpf_path}")
            print(f"Dat15 Path: {dat15_path}")
            print(f"Full Song Path: {full_song_path}")

            parser = RPFParser(rpf_path, os.path.join(self.gtaiv_path, "GTAIV.exe"))
            output_folder = tempfile.mkdtemp(prefix="gtaiv_radio_replace_")
            try:
                parser.extract_file(full_song_path, output_folder)

                extracted_file = os.path.join(output_folder, self.selected_song)

                self.progress.emit(25)
                print("Progress 25%")

                replace_special_audio(extracted_file, self.new_song_path)
                self.progress.emit(50)
                print("Progress 50%")

                audio = AudioSegment.from_file(self.new_song_path)
                new_song_length = int(audio.duration_seconds * 1000)

                # Pass the explicit dat15 path to update_song_duration
                update_song_duration(self.gtaiv_path, radio_name, self.selected_song, new_song_length, dat15_path=dat15_path)

                self.progress.emit(75)
                print("Progress 75%")

                parser.add_file(extracted_file, full_song_path)
            finally:
                shutil.rmtree(output_folder, ignore_errors=True)
            self.progress.emit(100)
            print("Progress 100%")
            self.finished.emit()
        except Exception as e:
            print(f"Worker encountered an error: {e}")
            self.error.emit(str(e))


class GTAIVEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.gtaiv_path = ""
        self.use_direct = False
        self.selected_radio = ""
        self.selected_song = ""
        self.new_song_path = ""
        self.worker = None

        self.song_select_page = None
        self.radio_select_page = None
        self.intro_page = IntroPage(on_next=self.goto_radio_select)
        self.replace_page = None
        self.batch_replace_page = None
        self.radio_logo_install_page = None
        self.progress_page = ProgressPage(on_cancel=self.cancel_replace)

        self.setWindowTitle("GTA IV Modding Toolkit")
        self.setMinimumSize(800, 600)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.init_ui()

    def init_ui(self):
        self.stack.addWidget(self.intro_page)
        self.stack.addWidget(self.progress_page)
        self.stack.setCurrentWidget(self.intro_page)

    def start_replace(self, new_song_path):
        self.new_song_path = new_song_path

        if not check_ffmpeg():
            if not install_ffmpeg(self):
                QMessageBox.critical(
                    self,
                    "Error",
                    "FFmpeg is required for audio processing. The operation cannot continue without it.",
                    QMessageBox.Ok
                )
                return

        self.worker = ReplaceSongWorker(
            self.gtaiv_path, self.selected_radio, self.selected_song, self.new_song_path, self.use_direct
        )

        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.on_replace_finished)
        self.worker.error.connect(self.on_replace_error)

        self.worker.start()
        self.stack.setCurrentWidget(self.progress_page)

    def goto_radio_select(self, gtaiv_path, use_direct=False):
        self.gtaiv_path = gtaiv_path
        self.use_direct = use_direct
        self.radio_select_page = RadioSelectPage(
            gtaiv_path=self.gtaiv_path,
            on_next=self.goto_song_select,
            on_back=self.goto_intro,
            on_install_logos=self.goto_radio_logo_install,
        )
        self.stack.addWidget(self.radio_select_page)
        self.stack.setCurrentWidget(self.radio_select_page)

    def goto_intro(self):
        self.stack.setCurrentWidget(self.intro_page)

    def goto_song_select(self, selected_radio):
        self.selected_radio = selected_radio
        self._discard_page(self.song_select_page)
        self.song_select_page = SongSelectPage(
            gtaiv_path=self.gtaiv_path,
            selected_radio=self.selected_radio,
            on_next=self.goto_replace,
            on_back=self.goto_radio_select_back,
            on_batch=self.goto_batch_replace,
        )
        self.stack.addWidget(self.song_select_page)
        self.stack.setCurrentWidget(self.song_select_page)

    def goto_radio_select_back(self):
        self.stack.setCurrentWidget(self.radio_select_page)

    def goto_radio_logo_install(self):
        self._discard_page(self.radio_logo_install_page)
        self.radio_logo_install_page = RadioLogoInstallPage(
            gtaiv_path=self.gtaiv_path,
            use_direct=self.use_direct,
            on_back=self.goto_radio_logo_install_back,
        )
        self.stack.addWidget(self.radio_logo_install_page)
        self.stack.setCurrentWidget(self.radio_logo_install_page)

    def goto_radio_logo_install_back(self):
        self.stack.setCurrentWidget(self.radio_select_page)

    def goto_batch_replace(self, songs):
        self._discard_page(self.batch_replace_page)
        self.batch_replace_page = BatchReplacePage(
            selected_radio=self.selected_radio,
            songs=songs,
            on_replace=self.start_batch_replace,
            on_back=self.goto_batch_replace_back,
        )
        self.stack.addWidget(self.batch_replace_page)
        self.stack.setCurrentWidget(self.batch_replace_page)

    def goto_batch_replace_back(self):
        self.stack.setCurrentWidget(self.song_select_page)

    def start_batch_replace(self, mappings):
        if not check_ffmpeg():
            if not install_ffmpeg(self):
                QMessageBox.critical(
                    self,
                    "Error",
                    "FFmpeg is required for audio processing. The operation cannot continue without it.",
                    QMessageBox.StandardButton.Ok,
                )
                return

        self.progress_page.update_progress(0)
        worker = BatchReplaceWorker(
            self.gtaiv_path,
            self.selected_radio,
            mappings,
            self.use_direct,
        )
        self.worker = worker
        worker.progress.connect(self.update_progress)
        worker.completed.connect(self.on_batch_replace_finished)
        worker.cancelled.connect(self.on_batch_replace_cancelled)
        worker.error.connect(self.on_batch_replace_error)
        worker.finished.connect(
            lambda current_worker=worker: self._on_batch_worker_thread_finished(current_worker)
        )
        worker.start()
        self.stack.setCurrentWidget(self.progress_page)

    def goto_replace(self, selected_song):
        self.selected_song = selected_song
        self._discard_page(self.replace_page)
        self.replace_page = ReplacePage(
            selected_song=self.selected_song,
            on_replace=self.start_replace,
            on_back=self.goto_song_select_back
        )
        self.stack.addWidget(self.replace_page)
        self.stack.setCurrentWidget(self.replace_page)

    def goto_song_select_back(self):
        self.stack.setCurrentWidget(self.song_select_page)

    def update_progress(self, value):
        self.progress_page.update_progress(value)

    def _discard_page(self, page):
        if page is None:
            return
        self.stack.removeWidget(page)
        page.deleteLater()

    def on_replace_finished(self):
        self.worker = None
        self.progress_page.update_progress(100)
        QMessageBox.information(self, "Success", "The song was successfully replaced!")
        self.goto_song_select(self.selected_radio)

    def on_replace_error(self, message):
        self.worker = None
        QMessageBox.critical(self, "Error", f"An error occurred: {message}", QMessageBox.StandardButton.Ok,
                             QMessageBox.StandardButton.NoButton)
        self.stack.setCurrentWidget(self.replace_page)

    def on_batch_replace_finished(self, count):
        self.progress_page.update_progress(100)
        QMessageBox.information(self, "Success", f"Successfully replaced {count} track(s).")
        self._discard_page(self.batch_replace_page)
        self.batch_replace_page = None
        self.goto_song_select(self.selected_radio)

    def on_batch_replace_cancelled(self):
        QMessageBox.information(self, "Cancelled", "Batch replacement was cancelled before commit.")
        self.stack.setCurrentWidget(self.batch_replace_page)

    def on_batch_replace_error(self, message):
        QMessageBox.critical(
            self,
            "Batch Replacement Error",
            f"No staged batch changes were committed.\n\n{message}",
            QMessageBox.StandardButton.Ok,
        )
        self.stack.setCurrentWidget(self.batch_replace_page)

    def _on_batch_worker_thread_finished(self, worker):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()

    def cancel_replace(self):
        if isinstance(self.worker, BatchReplaceWorker):
            self.worker.request_cancel()
            QMessageBox.information(
                self,
                "Cancellation Requested",
                "The batch will stop after the current conversion and before commit.",
            )
            return

        QMessageBox.information(self, "Cancelled", "The replacement operation has been cancelled.")
        if self.worker is not None:
            self.worker.terminate()
            self.worker = None
        self.stack.setCurrentWidget(self.intro_page)
