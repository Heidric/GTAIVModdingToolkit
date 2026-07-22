"""Modal system-check report shown from the settings page."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.system_check import CheckStatus, run_system_check
from ui.styles import BUTTON_STYLE, SCROLL_AREA_STYLE


_STATUS_COLORS = {
    CheckStatus.PASS: "#7CB342",
    CheckStatus.WARNING: "#FFC107",
    CheckStatus.FAIL: "#EF5350",
}


class SystemCheckDialog(QDialog):
    def __init__(self, gtaiv_path: str | None, use_direct: bool, parent=None):
        super().__init__(parent)
        self.gtaiv_path = gtaiv_path
        self.use_direct = use_direct

        self.setWindowTitle("System Check")
        self.setMinimumSize(760, 520)

        layout = QVBoxLayout(self)
        title = QLabel("System Check", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: white;")
        layout.addWidget(title)

        self.summary_label = QLabel(self)
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.results_widget = QWidget(self)
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.results_widget)
        scroll_area.setStyleSheet(SCROLL_AREA_STYLE)
        layout.addWidget(scroll_area)

        buttons = QHBoxLayout()
        refresh_button = QPushButton("Refresh", self)
        refresh_button.setStyleSheet(BUTTON_STYLE)
        refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(refresh_button)

        close_button = QPushButton("Close", self)
        close_button.setStyleSheet(BUTTON_STYLE)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.refresh()

    def _clear_results(self) -> None:
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def refresh(self) -> None:
        report = run_system_check(
            self.gtaiv_path,
            use_direct=self.use_direct,
        )
        self._clear_results()

        for item in report.items:
            label = QLabel(
                f"<b>{escape(item.label)}</b><br>{escape(item.detail)}",
                self.results_widget,
            )
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setStyleSheet(
                "padding: 8px; border-bottom: 1px solid #424242; "
                f"color: {_STATUS_COLORS[item.status]};"
            )
            self.results_layout.addWidget(label)

        summary_color = "#EF5350" if report.has_failures else "#7CB342"
        self.summary_label.setText(report.summary)
        self.summary_label.setStyleSheet(
            f"font-weight: bold; color: {summary_color}; padding: 6px;"
        )
