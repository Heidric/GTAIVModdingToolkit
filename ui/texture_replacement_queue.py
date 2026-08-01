"""Queue model and review dialog for batched texture replacements."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.archive_texture_batch import TextureReplacementRequest
from ui.styles import BUTTON_STYLE


@dataclass(frozen=True)
class QueuedTextureReplacement:
    request: TextureReplacementRequest
    resource_kind: str
    dimensions: str
    format_name: str
    target_png_data: bytes

    @property
    def key(self) -> tuple[str, int]:
        return self.request.key


class TextureReplacementQueue:
    """Ordered, duplicate-free replacement queue scoped to one archive."""

    def __init__(self):
        self._archive_path: Path | None = None
        self._items: dict[tuple[str, int], QueuedTextureReplacement] = {}

    @property
    def archive_path(self) -> Path | None:
        return self._archive_path

    @property
    def items(self) -> tuple[QueuedTextureReplacement, ...]:
        return tuple(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def contains(self, key: tuple[str, int]) -> bool:
        return key in self._items

    def add(
        self,
        archive_path: str | Path,
        item: QueuedTextureReplacement,
    ) -> bool:
        archive = Path(archive_path).expanduser().resolve()
        if self._archive_path is not None and archive != self._archive_path:
            raise ValueError("replacement queue belongs to a different archive")
        self._archive_path = archive
        replaced = item.key in self._items
        self._items[item.key] = item
        return replaced

    def remove(self, key: tuple[str, int]) -> None:
        self._items.pop(key, None)
        if not self._items:
            self._archive_path = None

    def clear(self) -> None:
        self._items.clear()
        self._archive_path = None

    def requests(self) -> tuple[TextureReplacementRequest, ...]:
        return tuple(item.request for item in self._items.values())

    def entry_count(self) -> int:
        return len({item.request.entry_path.casefold() for item in self._items.values()})

    def summary(self) -> str:
        if not self._items or self._archive_path is None:
            return "No queued replacements."
        lines = [
            f"Archive: {self._archive_path}",
            f"Entries to modify: {self.entry_count()}",
            f"Texture replacements: {len(self)}",
            "",
        ]
        current_entry = None
        for item in self._items.values():
            if item.request.entry_path != current_entry:
                current_entry = item.request.entry_path
                lines.append(current_entry)
            lines.append(
                f"  - #{item.request.texture_index} {item.request.texture_name}"
                f" -> {item.request.image_path.name}"
            )
        return "\n".join(lines)


class TextureReplacementQueueDialog(QDialog):
    apply_requested = Signal()

    def __init__(self, queue: TextureReplacementQueue, parent=None):
        super().__init__(parent)
        self.queue = queue
        self.setWindowTitle("Pending Texture Replacements")
        self.resize(980, 620)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("color: #B0BEC5;")
        layout.addWidget(self.summary_label)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.table = QTableWidget(0, 4, splitter)
        self.table.setHorizontalHeaderLabels(("Entry", "Texture", "Type", "Image"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.currentCellChanged.connect(self._selection_changed)

        preview_panel = QWidget(splitter)
        preview_layout = QVBoxLayout(preview_panel)
        target_title = QLabel("Current texture", preview_panel)
        target_title.setStyleSheet("font-weight: bold; color: #FFC107;")
        preview_layout.addWidget(target_title)
        self.target_preview = self._preview_label(preview_panel)
        preview_layout.addWidget(self.target_preview, stretch=1)
        replacement_title = QLabel("Replacement image", preview_panel)
        replacement_title.setStyleSheet("font-weight: bold; color: #FFC107;")
        preview_layout.addWidget(replacement_title)
        self.replacement_preview = self._preview_label(preview_panel)
        preview_layout.addWidget(self.replacement_preview, stretch=1)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        buttons = QHBoxLayout()
        self.remove_button = QPushButton("Remove selected", self)
        self.remove_button.setStyleSheet(BUTTON_STYLE)
        self.remove_button.clicked.connect(self._remove_selected)
        buttons.addWidget(self.remove_button)

        self.clear_button = QPushButton("Clear queue", self)
        self.clear_button.setStyleSheet(BUTTON_STYLE)
        self.clear_button.clicked.connect(self._clear_queue)
        buttons.addWidget(self.clear_button)

        buttons.addStretch(1)
        self.apply_button = QPushButton("Apply queued replacements", self)
        self.apply_button.setStyleSheet(BUTTON_STYLE)
        self.apply_button.clicked.connect(self._confirm_apply)
        buttons.addWidget(self.apply_button)

        close_button = QPushButton("Close", self)
        close_button.setStyleSheet(BUTTON_STYLE)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.refresh()

    @staticmethod
    def _preview_label(parent) -> QLabel:
        label = QLabel("No selection.", parent)
        label.setMinimumSize(260, 180)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "background-color: #161616; border: 1px solid #424242; color: #B0BEC5;"
        )
        return label

    def refresh(self) -> None:
        items = self.queue.items
        self.summary_label.setText(
            f"{len(items)} replacement(s) across {self.queue.entry_count()} entry/entries. "
            "All changes will use one archive backup and one commit."
        )

        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(items))
            for row, item in enumerate(items):
                values = (
                    item.request.entry_path,
                    f"#{item.request.texture_index} {item.request.texture_name}",
                    item.resource_kind,
                    str(item.request.image_path),
                )
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            if items:
                self.table.setCurrentCell(0, 0)
        finally:
            self.table.blockSignals(False)

        enabled = bool(items)
        self.remove_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.apply_button.setEnabled(enabled)
        if enabled and self.isVisible():
            self._selection_changed()
        elif not enabled:
            self._reset_previews()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._selection_changed()

    def _selected_item(self) -> QueuedTextureReplacement | None:
        row = self.table.currentRow()
        items = self.queue.items
        if row < 0 or row >= len(items):
            return None
        return items[row]

    def _selection_changed(self, *_args) -> None:
        item = self._selected_item()
        if item is None:
            self._reset_previews()
            return

        try:
            self._set_preview_bytes(self.target_preview, item.target_png_data)
            self._set_preview_path(self.replacement_preview, item.request.image_path)
        except Exception as exc:
            self._set_preview_error(self.target_preview, exc)
            self._set_preview_error(self.replacement_preview, exc)

    def _reset_previews(self) -> None:
        for label in (self.target_preview, self.replacement_preview):
            label.clear()
            label.setText("No selection.")

    def _set_preview_bytes(self, label: QLabel, payload: bytes) -> None:
        try:
            image = QImage.fromData(payload) if payload else QImage()
        except Exception as exc:
            self._set_preview_error(label, exc)
            return
        if image.isNull():
            label.clear()
            label.setText("Preview unavailable.")
            return
        self._set_preview_pixmap(label, QPixmap.fromImage(image))

    def _set_preview_path(self, label: QLabel, path: Path) -> None:
        try:
            pixmap = QPixmap(str(path))
        except Exception as exc:
            self._set_preview_error(label, exc)
            return
        self._set_preview_pixmap(label, pixmap)

    @staticmethod
    def _set_preview_error(label: QLabel, exc: Exception) -> None:
        label.clear()
        label.setText(f"Preview error:\n{exc}")

    @staticmethod
    def _set_preview_pixmap(label: QLabel, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            label.clear()
            label.setText("Preview unavailable.")
            return
        label.setPixmap(
            pixmap.scaled(
                label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _remove_selected(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        self.queue.remove(item.key)
        self.refresh()

    def _clear_queue(self) -> None:
        if not self.queue.items:
            return
        response = QMessageBox.question(
            self,
            "Clear Replacement Queue",
            "Remove all queued texture replacements?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response == QMessageBox.StandardButton.Yes:
            self.queue.clear()
            self.refresh()

    def _confirm_apply(self) -> None:
        response = QMessageBox.question(
            self,
            "Apply Queued Texture Replacements",
            self.queue.summary() + "\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response == QMessageBox.StandardButton.Yes:
            self.apply_requested.emit()
