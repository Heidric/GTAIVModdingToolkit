"""Read-only generic RPF and WTD browser page."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PySide6.QtCore import QTemporaryDir, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.path_dialogs import (
    PathHistoryKey,
    select_open_file,
    select_save_file,
)
from ui.rpf_browser_model import RPFBrowserNode, build_rpf_browser_tree
from ui.styles import BUTTON_STYLE, LINE_EDIT_STYLE
from ui.workers.rpf_browser import (
    RPFEntryExportWorker,
    RPFInspectWorker,
    RPFWTDInspectWorker,
    WTDTextureExportWorker,
    WTDTexturePreviewWorker,
)


_ENTRY_ROLE = Qt.ItemDataRole.UserRole
_TEXTURE_INDEX_ROLE = Qt.ItemDataRole.UserRole
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class RPFBrowserPage(QWidget):
    def __init__(self, gtaiv_path: str, on_back):
        super().__init__()
        self.gtaiv_path = str(Path(gtaiv_path).expanduser().resolve())
        self.gtaiv_exe_path = str(Path(self.gtaiv_path) / "GTAIV.exe")
        self.on_back = on_back

        self.archive_snapshot = None
        self.selected_entry = None
        self.wtd_snapshot = None
        self.extracted_wtd_path = ""
        self._workers = set()
        self._archive_request_id = 0
        self._wtd_request_id = 0
        self._preview_request_id = 0
        self._temporary_directory = QTemporaryDir(
            "gtaiv-toolkit-rpf-browser-XXXXXX"
        )
        self._temporary_directory.setAutoRemove(True)
        if not self._temporary_directory.isValid():
            raise OSError("Could not create the RPF browser temporary directory")

        self._build_ui()

    def set_gtaiv_path(self, gtaiv_path: str):
        resolved = str(Path(gtaiv_path).expanduser().resolve())
        if resolved == self.gtaiv_path:
            return
        self.gtaiv_path = resolved
        self.gtaiv_exe_path = str(Path(resolved) / "GTAIV.exe")
        self._archive_request_id += 1
        self._clear_archive()
        self.archive_input.clear()
        self.status_label.setText("Select an RPF archive.")

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("RPF Browser", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #FFC107;")
        layout.addWidget(title)

        read_only = QLabel(
            "Read-only mode: inspect archives, preview WTD textures, and export files. "
            "This page does not modify the game.",
            self,
        )
        read_only.setWordWrap(True)
        read_only.setAlignment(Qt.AlignmentFlag.AlignCenter)
        read_only.setStyleSheet("color: #B0BEC5;")
        layout.addWidget(read_only)

        archive_row = QHBoxLayout()
        self.archive_input = QLineEdit(self)
        self.archive_input.setPlaceholderText("Select an RPF archive...")
        self.archive_input.setStyleSheet(LINE_EDIT_STYLE)
        archive_row.addWidget(self.archive_input, stretch=1)

        browse_button = QPushButton("Browse", self)
        browse_button.setStyleSheet(BUTTON_STYLE)
        browse_button.clicked.connect(self.browse_archive)
        archive_row.addWidget(browse_button)

        self.open_button = QPushButton("Open", self)
        self.open_button.setStyleSheet(BUTTON_STYLE)
        self.open_button.clicked.connect(self.open_archive)
        archive_row.addWidget(self.open_button)
        layout.addLayout(archive_row)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_entry_panel())
        splitter.addWidget(self._build_texture_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, stretch=1)

        footer = QHBoxLayout()
        back_button = QPushButton("Back", self)
        back_button.setStyleSheet(BUTTON_STYLE)
        back_button.clicked.connect(self.on_back)
        footer.addWidget(back_button)

        self.status_label = QLabel("Select an RPF archive.", self)
        self.status_label.setStyleSheet("color: #B0BEC5;")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, stretch=1)
        layout.addLayout(footer)

    def _build_entry_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        label = QLabel("Archive entries", panel)
        label.setStyleSheet("font-weight: bold; color: #FFC107;")
        layout.addWidget(label)

        self.entry_tree = QTreeWidget(panel)
        self.entry_tree.setHeaderLabels(("Name", "Size", "Offset"))
        self.entry_tree.setAlternatingRowColors(True)
        self.entry_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.entry_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.entry_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.entry_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.entry_tree.currentItemChanged.connect(self.on_entry_selected)
        layout.addWidget(self.entry_tree, stretch=1)

        self.entry_details = QLabel("No entry selected.", panel)
        self.entry_details.setWordWrap(True)
        self.entry_details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.entry_details.setStyleSheet("color: #B0BEC5;")
        layout.addWidget(self.entry_details)

        self.export_entry_button = QPushButton("Export selected entry", panel)
        self.export_entry_button.setStyleSheet(BUTTON_STYLE)
        self.export_entry_button.setEnabled(False)
        self.export_entry_button.clicked.connect(self.export_selected_entry)
        layout.addWidget(self.export_entry_button)
        return panel

    def _build_texture_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        label = QLabel("WTD textures", panel)
        label.setStyleSheet("font-weight: bold; color: #FFC107;")
        layout.addWidget(label)

        self.wtd_details = QLabel(
            "Select a .wtd entry to inspect its textures.",
            panel,
        )
        self.wtd_details.setWordWrap(True)
        self.wtd_details.setStyleSheet("color: #B0BEC5;")
        layout.addWidget(self.wtd_details)

        self.texture_table = QTableWidget(0, 7, panel)
        self.texture_table.setHorizontalHeaderLabels(
            ("#", "Name", "Dimensions", "Format", "Mips", "Bytes", "Replaceable")
        )
        self.texture_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.texture_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.texture_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.texture_table.setAlternatingRowColors(True)
        self.texture_table.verticalHeader().setVisible(False)
        self.texture_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for column in (0, 2, 3, 4, 5, 6):
            self.texture_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.texture_table.currentCellChanged.connect(self.on_texture_selected)
        layout.addWidget(self.texture_table, stretch=1)

        self.preview_label = QLabel("No texture selected.", panel)
        self.preview_label.setMinimumSize(320, 220)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            "background-color: #161616; border: 1px solid #424242; color: #B0BEC5;"
        )
        layout.addWidget(self.preview_label)

        self.export_texture_button = QPushButton("Export selected texture as DDS", panel)
        self.export_texture_button.setStyleSheet(BUTTON_STYLE)
        self.export_texture_button.setEnabled(False)
        self.export_texture_button.clicked.connect(self.export_selected_texture)
        layout.addWidget(self.export_texture_button)
        return panel

    def browse_archive(self):
        selected = select_open_file(
            self,
            "Open RPF Archive",
            PathHistoryKey.RPF_ARCHIVE,
            file_filter="RPF Archives (*.rpf);;All Files (*)",
            fallback=self.archive_input.text().strip(),
        )
        if selected:
            self.archive_input.setText(selected)
            self.open_archive()

    def open_archive(self):
        archive_path = self.archive_input.text().strip()
        if not archive_path:
            QMessageBox.warning(self, "RPF Archive", "Select an RPF archive first.")
            return

        self._clear_archive()
        self._archive_request_id += 1
        request_id = self._archive_request_id
        self.open_button.setEnabled(False)
        self.status_label.setText("Reading RPF archive...")
        worker = RPFInspectWorker(request_id, archive_path, self.gtaiv_exe_path)
        worker.completed.connect(self._on_archive_loaded)
        worker.error.connect(self._on_archive_error)
        worker.finished.connect(lambda: self.open_button.setEnabled(True))
        self._start_worker(worker)

    def _on_archive_loaded(self, request_id, snapshot):
        if request_id != self._archive_request_id:
            return
        self.archive_snapshot = snapshot
        self.archive_input.setText(str(snapshot.archive_path))
        nodes = build_rpf_browser_tree(snapshot.entries)
        for node in nodes:
            self.entry_tree.addTopLevelItem(self._tree_item(node))
        self.entry_tree.expandToDepth(0)
        self.status_label.setText(
            f"Loaded {len(snapshot.entries)} file entries from {snapshot.archive_path.name}."
        )

    def _on_archive_error(self, request_id: int, message: str):
        if request_id != self._archive_request_id:
            return
        self.status_label.setText("Could not read the RPF archive.")
        QMessageBox.critical(self, "RPF Browser Error", message)

    def _tree_item(self, node: RPFBrowserNode) -> QTreeWidgetItem:
        if node.entry is None:
            item = QTreeWidgetItem((node.name, "", ""))
        else:
            item = QTreeWidgetItem(
                (node.name, self._format_bytes(node.entry.size), f"0x{node.entry.offset:X}")
            )
            item.setData(0, _ENTRY_ROLE, node.entry)
        for child in node.children:
            item.addChild(self._tree_item(child))
        return item

    def on_entry_selected(self, current, _previous):
        entry = current.data(0, _ENTRY_ROLE) if current is not None else None
        self.selected_entry = entry
        self.export_entry_button.setEnabled(entry is not None)
        self._clear_wtd()
        if entry is None:
            self.entry_details.setText("Directory selected.")
            return

        self.entry_details.setText(
            f"Path: {entry.path}\nSize: {self._format_bytes(entry.size)} "
            f"({entry.size} bytes)\nOffset: 0x{entry.offset:X}"
        )
        if not entry.path.casefold().endswith(".wtd"):
            self.wtd_details.setText("The selected entry is not a WTD texture dictionary.")
            return
        self._inspect_selected_wtd(entry.path)

    def _inspect_selected_wtd(self, entry_path: str):
        if self.archive_snapshot is None:
            return
        self._wtd_request_id += 1
        request_id = self._wtd_request_id
        digest = hashlib.sha256(entry_path.encode("utf-8")).hexdigest()[:20]
        extracted_path = str(
            Path(self._temporary_directory.path()) / f"{request_id}-{digest}.wtd"
        )
        self.wtd_details.setText("Extracting and reading WTD metadata...")
        worker = RPFWTDInspectWorker(
            request_id,
            str(self.archive_snapshot.archive_path),
            self.gtaiv_exe_path,
            entry_path,
            extracted_path,
        )
        worker.completed.connect(self._on_wtd_loaded)
        worker.error.connect(self._on_wtd_error)
        self._start_worker(worker)

    def _on_wtd_loaded(self, request_id, entry_path, snapshot, local_path):
        if request_id != self._wtd_request_id:
            return
        if self.selected_entry is None or self.selected_entry.path != entry_path:
            return
        self.wtd_snapshot = snapshot
        self.extracted_wtd_path = local_path
        self.wtd_details.setText(
            f"Textures: {len(snapshot.textures)} | "
            f"Virtual: {self._format_bytes(snapshot.virtual_size)} | "
            f"Physical: {self._format_bytes(snapshot.physical_size)}"
        )
        self.texture_table.setRowCount(len(snapshot.textures))
        for row, texture in enumerate(snapshot.textures):
            values = (
                str(texture.index),
                texture.name,
                f"{texture.width} × {texture.height}",
                texture.format_name,
                str(texture.mip_count),
                self._format_bytes(texture.data_size) if texture.data_size is not None else "Unknown",
                "Yes" if texture.replaceable else "No",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(_TEXTURE_INDEX_ROLE, texture.index)
                self.texture_table.setItem(row, column, item)
        self.status_label.setText(f"Loaded WTD entry {entry_path}.")

    def _on_wtd_error(self, request_id, entry_path, message):
        if request_id != self._wtd_request_id:
            return
        if self.selected_entry is None or self.selected_entry.path != entry_path:
            return
        self.wtd_details.setText("The selected WTD could not be read.")
        self.status_label.setText(f"Could not inspect {entry_path}.")
        QMessageBox.critical(self, "WTD Inspection Error", message)

    def on_texture_selected(self, current_row, _current_column, _previous_row, _previous_column):
        self.export_texture_button.setEnabled(False)
        self.preview_label.clear()
        if current_row < 0 or self.wtd_snapshot is None or not self.extracted_wtd_path:
            self.preview_label.setText("No texture selected.")
            return

        index_item = self.texture_table.item(current_row, 0)
        if index_item is None:
            self.preview_label.setText("No texture selected.")
            return
        texture_index = index_item.data(_TEXTURE_INDEX_ROLE)
        texture = self.wtd_snapshot.texture(texture_index)
        self.export_texture_button.setEnabled(texture.extractable)
        if not texture.extractable:
            self.preview_label.setText(
                f"Preview unavailable for {texture.format_name}."
            )
            return

        self._preview_request_id += 1
        request_id = self._preview_request_id
        self.preview_label.setText("Rendering preview...")
        worker = WTDTexturePreviewWorker(
            request_id,
            self.extracted_wtd_path,
            texture_index,
            max_dimension=512,
        )
        worker.completed.connect(self._on_preview_loaded)
        worker.error.connect(self._on_preview_error)
        self._start_worker(worker)

    def _on_preview_loaded(self, request_id, texture_index, preview):
        if request_id != self._preview_request_id:
            return
        if self._selected_texture_index() != texture_index:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(preview.png_data, b"PNG"):
            self.preview_label.setText("The generated preview image is invalid.")
            return
        self.preview_label.setPixmap(
            pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.status_label.setText(
            f"Previewing {preview.texture.name} ({preview.texture.format_name})."
        )

    def _on_preview_error(self, request_id, texture_index, message):
        if request_id != self._preview_request_id:
            return
        if self._selected_texture_index() != texture_index:
            return
        self.preview_label.setText("Preview unavailable.")
        self.status_label.setText(message)

    def export_selected_entry(self):
        if self.selected_entry is None or self.archive_snapshot is None:
            return
        destination = select_save_file(
            self,
            "Export RPF Entry",
            PathHistoryKey.RPF_EXPORT,
            suggested_name=self.selected_entry.name,
            fallback=str(self.archive_snapshot.archive_path),
        )
        if not destination:
            return
        overwrite = self._confirm_overwrite(destination)
        if overwrite is None:
            return
        self.export_entry_button.setEnabled(False)
        worker = RPFEntryExportWorker(
            str(self.archive_snapshot.archive_path),
            self.gtaiv_exe_path,
            self.selected_entry.path,
            destination,
            overwrite=overwrite,
        )
        worker.completed.connect(self._on_entry_exported)
        worker.error.connect(self._on_export_error)
        worker.finished.connect(
            lambda: self.export_entry_button.setEnabled(self.selected_entry is not None)
        )
        self._start_worker(worker)

    def _on_entry_exported(self, path: str):
        self.status_label.setText(f"Exported RPF entry to {path}.")
        QMessageBox.information(self, "Export Complete", f"Exported to:\n{path}")

    def export_selected_texture(self):
        texture_index = self._selected_texture_index()
        if texture_index is None or self.wtd_snapshot is None or not self.extracted_wtd_path:
            return
        texture = self.wtd_snapshot.texture(texture_index)
        suggested = self._safe_texture_filename(texture.name)
        destination = select_save_file(
            self,
            "Export WTD Texture",
            PathHistoryKey.WTD_TEXTURE_EXPORT,
            file_filter="DDS Texture (*.dds);;All Files (*)",
            suggested_name=suggested,
            fallback=self.extracted_wtd_path,
        )
        if not destination:
            return
        if not destination.casefold().endswith(".dds"):
            destination += ".dds"
        overwrite = self._confirm_overwrite(destination)
        if overwrite is None:
            return
        self.export_texture_button.setEnabled(False)
        worker = WTDTextureExportWorker(
            self.extracted_wtd_path,
            texture_index,
            destination,
            overwrite=overwrite,
        )
        worker.completed.connect(self._on_texture_exported)
        worker.error.connect(self._on_export_error)
        worker.finished.connect(self._restore_texture_export_button)
        self._start_worker(worker)

    def _on_texture_exported(self, path: str):
        self.status_label.setText(f"Exported texture to {path}.")
        QMessageBox.information(self, "Export Complete", f"Exported to:\n{path}")

    def _on_export_error(self, message: str):
        self.status_label.setText("Export failed.")
        QMessageBox.critical(self, "Export Error", message)

    def _restore_texture_export_button(self):
        texture_index = self._selected_texture_index()
        if texture_index is None or self.wtd_snapshot is None:
            self.export_texture_button.setEnabled(False)
            return
        self.export_texture_button.setEnabled(
            self.wtd_snapshot.texture(texture_index).extractable
        )

    def _selected_texture_index(self):
        row = self.texture_table.currentRow()
        if row < 0:
            return None
        item = self.texture_table.item(row, 0)
        return item.data(_TEXTURE_INDEX_ROLE) if item is not None else None

    def _clear_archive(self):
        self.archive_snapshot = None
        self.selected_entry = None
        self.entry_tree.clear()
        self.entry_details.setText("No entry selected.")
        self.export_entry_button.setEnabled(False)
        self._clear_wtd()

    def _clear_wtd(self):
        self._wtd_request_id += 1
        self._preview_request_id += 1
        self.wtd_snapshot = None
        self.extracted_wtd_path = ""
        self.texture_table.setRowCount(0)
        self.wtd_details.setText("Select a .wtd entry to inspect its textures.")
        self.preview_label.clear()
        self.preview_label.setText("No texture selected.")
        self.export_texture_button.setEnabled(False)

    def _start_worker(self, worker):
        self._workers.add(worker)
        worker.finished.connect(lambda current=worker: self._release_worker(current))
        worker.start()

    def _release_worker(self, worker):
        self._workers.discard(worker)
        worker.deleteLater()

    def _confirm_overwrite(self, path: str) -> bool | None:
        if not Path(path).exists():
            return False
        response = QMessageBox.question(
            self,
            "Replace Existing File",
            f"The destination already exists:\n{path}\n\nReplace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return True if response == QMessageBox.StandardButton.Yes else None

    @staticmethod
    def _safe_texture_filename(name: str) -> str:
        stem = _SAFE_FILENAME.sub("_", name).strip("._") or "texture"
        return f"{stem}.dds"

    @staticmethod
    def _format_bytes(value: int) -> str:
        amount = float(value)
        for suffix in ("B", "KiB", "MiB", "GiB"):
            if amount < 1024.0 or suffix == "GiB":
                if suffix == "B":
                    return f"{int(amount)} {suffix}"
                return f"{amount:.1f} {suffix}"
            amount /= 1024.0
        return f"{value} B"
