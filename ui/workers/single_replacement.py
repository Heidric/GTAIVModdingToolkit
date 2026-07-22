"""Qt worker for transactional single-track replacement."""

from PySide6.QtCore import QThread, Signal

from single_replacement import (
    SingleReplacementCancelled,
    replace_single_track_transactional,
)


class SingleTrackReplacementWorker(QThread):
    progress = Signal(int)
    completed = Signal(object)
    cancelled = Signal()
    error = Signal(str)

    def __init__(
        self,
        gtaiv_path,
        selected_radio,
        selected_song,
        new_song_path,
        use_direct,
    ):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.selected_radio = selected_radio
        self.selected_song = selected_song
        self.new_song_path = new_song_path
        self.use_direct = use_direct
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            result = replace_single_track_transactional(
                self.gtaiv_path,
                self.selected_radio,
                self.selected_song,
                self.new_song_path,
                self.use_direct,
                progress_callback=self.progress.emit,
                cancellation_callback=lambda: self._cancel_requested,
            )
            self.completed.emit(result)
        except SingleReplacementCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))
