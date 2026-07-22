from PySide6.QtWidgets import QMainWindow, QStackedWidget, QMessageBox

from build_info import application_title
from ui.pages.intro import IntroPage
from ui.pages.settings import SettingsPage
from ui.pages.radio_select import RadioSelectPage
from ui.pages.song_select import SongSelectPage
from ui.pages.replace import ReplacePage
from ui.pages.progress import ProgressPage
from ui.pages.batch_replace import BatchReplacePage
from ui.pages.radio_logo_install import RadioLogoInstallPage
from ui.pages.audio_recovery import AudioRecoveryPage
from utils import install_ffmpeg, check_ffmpeg
from ui.workers.batch_replacement import BatchReplaceWorker
from ui.workers.single_replacement import SingleTrackReplacementWorker


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
        self.intro_page = IntroPage(
            on_next=self.goto_radio_select,
            on_settings=self.goto_settings,
        )
        self.settings_page = SettingsPage(
            on_back=self.goto_intro,
            on_saved=self.on_settings_saved,
        )
        self.replace_page = None
        self.batch_replace_page = None
        self.radio_logo_install_page = None
        self.audio_recovery_page = None
        self.progress_page = ProgressPage(on_cancel=self.cancel_replace)

        self.setWindowTitle(application_title())
        self.setMinimumSize(800, 600)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.init_ui()

    def init_ui(self):
        self.stack.addWidget(self.intro_page)
        self.stack.addWidget(self.settings_page)
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

        self.progress_page.update_progress(0)
        worker = SingleTrackReplacementWorker(
            self.gtaiv_path,
            self.selected_radio,
            self.selected_song,
            self.new_song_path,
            self.use_direct,
        )
        self.worker = worker
        worker.progress.connect(self.update_progress)
        worker.completed.connect(self.on_replace_finished)
        worker.cancelled.connect(self.on_replace_cancelled)
        worker.error.connect(self.on_replace_error)
        worker.finished.connect(
            lambda current_worker=worker: self._on_single_worker_thread_finished(
                current_worker
            )
        )

        worker.start()
        self.stack.setCurrentWidget(self.progress_page)

    def goto_radio_select(self, gtaiv_path, use_direct=False):
        self.gtaiv_path = gtaiv_path
        self.use_direct = use_direct
        self.radio_select_page = RadioSelectPage(
            gtaiv_path=self.gtaiv_path,
            use_direct=self.use_direct,
            on_next=self.goto_song_select,
            on_back=self.goto_intro,
            on_install_logos=self.goto_radio_logo_install,
            on_recover_audio=self.goto_audio_recovery,
        )
        self.stack.addWidget(self.radio_select_page)
        self.stack.setCurrentWidget(self.radio_select_page)

    def goto_intro(self):
        self.stack.setCurrentWidget(self.intro_page)

    def goto_settings(self):
        self.settings_page.reload()
        self.stack.setCurrentWidget(self.settings_page)

    def on_settings_saved(self):
        self.intro_page.reload_preferences(auto_detect=False)
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
        if self.radio_select_page is not None:
            self.radio_select_page.refresh_icons()
        self.stack.setCurrentWidget(self.radio_select_page)

    def goto_audio_recovery(self):
        self._discard_page(self.audio_recovery_page)
        self.audio_recovery_page = AudioRecoveryPage(
            gtaiv_path=self.gtaiv_path,
            use_direct=self.use_direct,
            on_back=self.goto_audio_recovery_back,
        )
        self.stack.addWidget(self.audio_recovery_page)
        self.stack.setCurrentWidget(self.audio_recovery_page)

    def goto_audio_recovery_back(self):
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

    def on_replace_finished(self, _result):
        self.progress_page.update_progress(100)
        QMessageBox.information(
            self,
            "Success",
            "The song was transactionally replaced and verified.",
        )
        self.goto_song_select(self.selected_radio)

    def on_replace_cancelled(self):
        QMessageBox.information(
            self,
            "Cancelled",
            "Single-track replacement was cancelled before commit.",
        )
        self.stack.setCurrentWidget(self.replace_page)

    def on_replace_error(self, message):
        QMessageBox.critical(
            self,
            "Single Replacement Error",
            f"No staged single-track changes were committed.\n\n{message}",
            QMessageBox.StandardButton.Ok,
        )
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

    def _on_single_worker_thread_finished(self, worker):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()

    def _on_batch_worker_thread_finished(self, worker):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()

    def cancel_replace(self):
        if isinstance(self.worker, SingleTrackReplacementWorker):
            self.worker.request_cancel()
            QMessageBox.information(
                self,
                "Cancellation Requested",
                "The replacement will stop before committing staged files.",
            )
            return

        if isinstance(self.worker, BatchReplaceWorker):
            self.worker.request_cancel()
            QMessageBox.information(
                self,
                "Cancellation Requested",
                "The batch will stop after the current conversion and before commit.",
            )
            return

        QMessageBox.information(
            self,
            "Nothing to Cancel",
            "No replacement operation is currently running.",
        )
