import os
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.path_dialogs import PathHistoryKey, select_open_files
from ui.styles import BUTTON_STYLE


_AUDIO_FILTER = "Audio Files (*.mp3 *.wav *.ogg *.flac *.aac *.m4a)"


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


class BatchReplacePage(QWidget):
    def __init__(self, selected_radio, songs, on_replace, on_back):
        super().__init__()
        self.selected_radio = selected_radio
        self.songs = list(songs)
        self.on_replace = on_replace
        self.on_back = on_back

        layout = QVBoxLayout(self)

        title = QLabel(f"Batch Replace — {selected_radio[:-4].upper()}", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FFC107;")
        layout.addWidget(title)

        description = QLabel(
            "Select audio files and map each file to an existing radio track slot. "
            "No new slots are created.",
            self,
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        self.capacity_label = QLabel(self)
        self.capacity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.capacity_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #B0BEC5;")
        layout.addWidget(self.capacity_label)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Target track slot", "Replacement audio"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        tools = QHBoxLayout()
        self.add_button = QPushButton("Select Audio Files", self)
        self.add_button.setStyleSheet(BUTTON_STYLE)
        self.add_button.clicked.connect(self.select_files)
        tools.addWidget(self.add_button)

        remove_button = QPushButton("Remove Selected", self)
        remove_button.setStyleSheet(BUTTON_STYLE)
        remove_button.clicked.connect(self.remove_selected)
        tools.addWidget(remove_button)

        clear_button = QPushButton("Clear", self)
        clear_button.setStyleSheet(BUTTON_STYLE)
        clear_button.clicked.connect(lambda: self.table.setRowCount(0))
        tools.addWidget(clear_button)
        layout.addLayout(tools)

        navigation = QHBoxLayout()
        back_button = QPushButton("Back", self)
        back_button.setStyleSheet(BUTTON_STYLE)
        back_button.clicked.connect(self.on_back)
        navigation.addWidget(back_button)

        replace_button = QPushButton("Replace All", self)
        replace_button.setStyleSheet(BUTTON_STYLE)
        replace_button.clicked.connect(self.replace_all)
        navigation.addWidget(replace_button)
        layout.addLayout(navigation)

        self.table.model().rowsInserted.connect(self._update_capacity_label)
        self.table.model().rowsRemoved.connect(self._update_capacity_label)
        self._update_capacity_label()

    def _update_capacity_label(self, *_):
        selected = self.table.rowCount()
        total = len(self.songs)
        remaining = max(0, total - selected)
        self.capacity_label.setText(
            f"Replaceable slots: {total} · Selected files: {selected} · Remaining: {remaining}"
        )
        self.add_button.setEnabled(remaining > 0)

    def select_files(self):
        files = select_open_files(
            self,
            "Select Replacement Audio Files",
            PathHistoryKey.BATCH_REPLACEMENT_AUDIO,
            file_filter=_AUDIO_FILTER,
        )
        if not files:
            return

        remaining = len(self.songs) - self.table.rowCount()
        if len(files) > remaining:
            QMessageBox.warning(
                self,
                "Too Many Files",
                f"You selected {len(files)} file(s), but only {remaining} replaceable slot(s) remain.",
            )
            return

        used_targets = self._selected_targets()
        for audio_path in files:
            self._append_mapping(audio_path, used_targets)

    def _append_mapping(self, audio_path, used_targets):
        row = self.table.rowCount()
        self.table.insertRow(row)

        combo = QComboBox(self.table)
        combo.addItems(self.songs)

        stem = os.path.splitext(os.path.basename(audio_path))[0]
        normalized_stem = _normalized_name(stem)
        preferred = next(
            (
                song
                for song in self.songs
                if song not in used_targets and _normalized_name(song) == normalized_stem
            ),
            None,
        )
        if preferred is None:
            preferred = next((song for song in self.songs if song not in used_targets), self.songs[0])

        combo.setCurrentText(preferred)
        used_targets.add(preferred)
        self.table.setCellWidget(row, 0, combo)

        path_item = QTableWidgetItem(audio_path)
        path_item.setToolTip(audio_path)
        path_item.setData(Qt.ItemDataRole.UserRole, audio_path)
        self.table.setItem(row, 1, path_item)

    def _selected_targets(self):
        targets = set()
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 0)
            if combo is not None:
                targets.add(combo.currentText())
        return targets

    def remove_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def replace_all(self):
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "No Files Selected", "Select at least one replacement audio file.")
            return

        mappings = []
        targets = []
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 0)
            path_item = self.table.item(row, 1)
            target = combo.currentText() if combo is not None else ""
            audio_path = path_item.data(Qt.ItemDataRole.UserRole) if path_item is not None else ""

            if not target or not audio_path or not os.path.isfile(audio_path):
                QMessageBox.warning(self, "Invalid Mapping", f"Row {row + 1} is incomplete or invalid.")
                return

            targets.append(target)
            mappings.append((target, audio_path))

        duplicates = sorted({target for target in targets if targets.count(target) > 1})
        if duplicates:
            QMessageBox.warning(
                self,
                "Duplicate Track Slots",
                "Each target slot may be used only once:\n" + "\n".join(duplicates),
            )
            return

        answer = QMessageBox.question(
            self,
            "Confirm Batch Replacement",
            f"Replace {len(mappings)} track(s) in {self.selected_radio[:-4].upper()}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.on_replace(mappings)
