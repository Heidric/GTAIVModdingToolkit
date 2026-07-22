"""Audio-history recovery page."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from core.audio_history import latest_audio_snapshot, restore_latest_audio_snapshot
from ui.styles import BUTTON_STYLE


class AudioRecoveryWorker(QThread):
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, gtaiv_path: str, use_direct: bool):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.use_direct = use_direct

    def run(self):
        try:
            self.completed.emit(
                restore_latest_audio_snapshot(self.gtaiv_path, self.use_direct)
            )
        except Exception as exc:
            self.error.emit(str(exc))


class AudioRecoveryPage(QWidget):
    def __init__(self, gtaiv_path: str, use_direct: bool, on_back):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.use_direct = use_direct
        self.on_back = on_back
        self.worker = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Audio Recovery", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FFC107;")
        layout.addWidget(title)

        self.mode_label = QLabel(
            f"Mode: {'Direct' if use_direct else 'FusionFix'}",
            self,
        )
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.mode_label)

        self.status_label = QLabel(self)
        self.status_label.setFixedWidth(560)
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #B0BEC5;")
        layout.addWidget(self.status_label)

        self.restore_button = QPushButton("Restore Previous Audio State", self)
        self.restore_button.clicked.connect(self.restore_latest)
        self.restore_button.setStyleSheet(BUTTON_STYLE)
        layout.addWidget(
            self.restore_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh_state)
        self.refresh_button.setStyleSheet(BUTTON_STYLE)
        layout.addWidget(
            self.refresh_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        self.back_button = QPushButton("Back", self)
        self.back_button.clicked.connect(self.on_back)
        self.back_button.setStyleSheet(BUTTON_STYLE)
        layout.addWidget(self.back_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self.refresh_state()

    @staticmethod
    def _format_created_at(value: str) -> str:
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value

    def refresh_state(self):
        snapshot = latest_audio_snapshot(self.gtaiv_path, self.use_direct)
        if snapshot is None:
            self.status_label.setText(
                "No audio recovery state is available yet. New successful single or "
                "batch replacements will create one automatically."
            )
            self.restore_button.setEnabled(False)
            return

        rpf_state = "present" if snapshot.rpf_was_present else "absent"
        dat15_state = "present" if snapshot.dat15_was_present else "absent"
        self.status_label.setText(
            "Latest recoverable operation:\n"
            f"Station: {snapshot.station_file}\n"
            f"Captured: {self._format_created_at(snapshot.created_at_utc)}\n"
            f"Previous RPF: {rpf_state}; previous sounds.dat15: {dat15_state}\n"
            f"Source: {snapshot.reason}"
        )
        self.restore_button.setEnabled(True)

    def restore_latest(self):
        snapshot = latest_audio_snapshot(self.gtaiv_path, self.use_direct)
        if snapshot is None:
            self.refresh_state()
            return

        answer = QMessageBox.question(
            self,
            "Restore Previous Audio State",
            (
                f"Restore the previous paired audio state for {snapshot.station_file}?\n\n"
                "This restores both the station RPF and the matching global "
                "sounds.dat15 state. The currently active state will be retained, "
                "so the recovery can be reversed."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.restore_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.back_button.setEnabled(False)
        self.status_label.setText("Restoring and transactionally swapping audio files...")

        worker = AudioRecoveryWorker(self.gtaiv_path, self.use_direct)
        self.worker = worker
        worker.completed.connect(self._on_completed)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    def _on_completed(self, result):
        QMessageBox.information(
            self,
            "Audio State Restored",
            (
                f"Restored the previous audio state for "
                f"{result.restored_snapshot.station_file}.\n\n"
                "The displaced state is now the next recoverable state."
            ),
            QMessageBox.StandardButton.Ok,
        )
        self.refresh_state()

    def _on_error(self, message: str):
        QMessageBox.critical(
            self,
            "Audio Recovery Error",
            f"The previous active state was preserved.\n\n{message}",
            QMessageBox.StandardButton.Ok,
        )
        self.refresh_state()

    def _on_worker_finished(self):
        worker = self.worker
        self.worker = None
        self.refresh_button.setEnabled(True)
        self.back_button.setEnabled(True)
        if worker is not None:
            worker.deleteLater()
