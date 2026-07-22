"""Qt worker for transactional batch track replacement."""

from PySide6.QtCore import QThread, Signal

from batch_replacement import (
    BatchReplacementCancelled,
    replace_batch_transactional,
)


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

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            result = replace_batch_transactional(
                self.gtaiv_path,
                self.selected_radio,
                self.mappings,
                self.use_direct,
                progress_callback=self.progress.emit,
                cancellation_callback=lambda: self._cancel_requested,
            )
        except BatchReplacementCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))
        else:
            self.completed.emit(result.replaced_count)
