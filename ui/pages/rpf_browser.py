"""Generic RPF and WTD browser page."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PySide6.QtCore import QByteArray, QTemporaryDir, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
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

from core.archive_texture_batch import TextureReplacementRequest
from ui.archive_entry_filter import (
    filter_archive_entries_by_name,
    normalize_archive_name_filter,
)
from ui.texture_replacement_queue import (
    QueuedTextureReplacement,
    TextureReplacementQueue,
    TextureReplacementQueueDialog,
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
    RPFEntryReplaceWorker,
    RPFInspectWorker,
    RPFTextureBatchReplaceWorker,
    RPFWTDInspectWorker,
    RPFWTDTextureReplaceWorker,
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
        self._current_preview_png_data = b""
        self._workers = set()
        self._archive_request_id = 0
        self._wtd_request_id = 0
        self._preview_request_id = 0
        self._replacement_worker = None
        self._replacement_result = None
        self._replacement_error = None
        self._replacement_queue = TextureReplacementQueue()
        self._current_preview_png_data = b""
        self._archive_open_in_progress = False
        self._entry_export_in_progress = False
        self._texture_export_in_progress = False
        self._entry_name_filter = None
        self._entry_items = {}
        self._temporary_wtd_paths = set()
        self._last_backup_path = None
        self._restore_entry_path = None
        self._restore_texture_index = None
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
        self._last_backup_path = None
        self._archive_request_id += 1
        self._clear_archive()
        self.archive_input.clear()
        self.status_label.setText("Select an RPF archive.")
        self._refresh_controls()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("RPF Browser", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #FFC107;")
        layout.addWidget(title)

        safety_note = QLabel(
            "Inspect, export, and transactionally replace existing RPF entries. "
            "Supported WTD textures can also be replaced individually. The toolkit "
            "verifies the staged archive and creates a backup before commit.",
            self,
        )
        safety_note.setWordWrap(True)
        safety_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        safety_note.setStyleSheet("color: #B0BEC5;")
        layout.addWidget(safety_note)

        archive_row = QHBoxLayout()
        self.archive_input = QLineEdit(self)
        self.archive_input.setPlaceholderText("Select an RPF archive...")
        self.archive_input.setStyleSheet(LINE_EDIT_STYLE)
        archive_row.addWidget(self.archive_input, stretch=1)

        self.browse_button = QPushButton("Browse", self)
        self.browse_button.setStyleSheet(BUTTON_STYLE)
        self.browse_button.clicked.connect(self.browse_archive)
        archive_row.addWidget(self.browse_button)

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
        self.back_button = QPushButton("Back", self)
        self.back_button.setStyleSheet(BUTTON_STYLE)
        self.back_button.clicked.connect(self.on_back)
        footer.addWidget(self.back_button)

        self.open_backup_button = QPushButton("Open latest backup folder", self)
        self.open_backup_button.setStyleSheet(BUTTON_STYLE)
        self.open_backup_button.setEnabled(False)
        self.open_backup_button.clicked.connect(self._open_latest_backup_folder)
        footer.addWidget(self.open_backup_button)

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

        filter_row = QHBoxLayout()
        filter_label = QLabel("Name filter:", panel)
        filter_label.setStyleSheet("color: #B0BEC5;")
        filter_row.addWidget(filter_label)

        self.entry_name_filter = QLineEdit(panel)
        self.entry_name_filter.setPlaceholderText("e.g. underpass, .wtd, lod03; empty shows all files")
        self.entry_name_filter.setClearButtonEnabled(True)
        self.entry_name_filter.setStyleSheet(LINE_EDIT_STYLE)
        self.entry_name_filter.setEnabled(False)
        self.entry_name_filter.textChanged.connect(
            self._on_entry_name_filter_changed
        )
        filter_row.addWidget(self.entry_name_filter, stretch=1)
        layout.addLayout(filter_row)

        self.entry_tree = QTreeWidget(panel)
        self.entry_tree.setHeaderLabels(("Name", "Size", "Offset"))
        self.entry_tree.setAlternatingRowColors(True)
        self.entry_tree.setStyleSheet(
            """
            QTreeWidget::item:selected:active {
                background-color: #FFC107;
                color: #000000;
            }
            QTreeWidget::item:selected:!active {
                background-color: #FFC107;
                color: #000000;
            }
            """
        )
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

        self.replace_entry_button = QPushButton(
            "Replace selected entry from file", panel
        )
        self.replace_entry_button.setStyleSheet(BUTTON_STYLE)
        self.replace_entry_button.setEnabled(False)
        self.replace_entry_button.clicked.connect(self.replace_selected_entry)
        layout.addWidget(self.replace_entry_button)
        return panel

    def _build_texture_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        label = QLabel("Resource textures", panel)
        label.setStyleSheet("font-weight: bold; color: #FFC107;")
        layout.addWidget(label)

        self.wtd_details = QLabel(
            "Select a .wtd or .wdr entry to inspect its textures.",
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

        self.export_texture_button = QPushButton("Export selected texture...", panel)
        self.export_texture_button.setStyleSheet(BUTTON_STYLE)
        self.export_texture_button.setEnabled(False)
        self.export_texture_button.clicked.connect(self.export_selected_texture)
        layout.addWidget(self.export_texture_button)

        self.replace_texture_button = QPushButton(
            "Replace selected texture from image", panel
        )
        self.replace_texture_button.setStyleSheet(BUTTON_STYLE)
        self.replace_texture_button.setEnabled(False)
        self.replace_texture_button.clicked.connect(self.replace_selected_texture)
        layout.addWidget(self.replace_texture_button)

        queue_row = QHBoxLayout()
        self.queue_texture_button = QPushButton(
            "Add selected replacement to queue", panel
        )
        self.queue_texture_button.setStyleSheet(BUTTON_STYLE)
        self.queue_texture_button.setEnabled(False)
        self.queue_texture_button.clicked.connect(
            self.queue_selected_texture_replacement
        )
        queue_row.addWidget(self.queue_texture_button)

        self.review_queue_button = QPushButton(
            "Review/apply queue (0)", panel
        )
        self.review_queue_button.setStyleSheet(BUTTON_STYLE)
        self.review_queue_button.setEnabled(False)
        self.review_queue_button.clicked.connect(self.show_replacement_queue)
        queue_row.addWidget(self.review_queue_button)
        layout.addLayout(queue_row)
        return panel

    def browse_archive(self):
        if self._file_operation_in_progress() or self._archive_open_in_progress:
            return
        selected = select_open_file(
            self,
            "Open RPF Archive",
            PathHistoryKey.RPF_ARCHIVE,
            file_filter="GTA IV Archives (*.rpf *.img);;All Files (*)",
            fallback=self.archive_input.text().strip(),
        )
        if selected:
            self.archive_input.setText(selected)
            self.open_archive()

    def open_archive(self):
        if self._file_operation_in_progress() or self._archive_open_in_progress:
            return
        archive_path = self.archive_input.text().strip()
        if not archive_path:
            QMessageBox.warning(self, "RPF Archive", "Select an RPF archive first.")
            return

        self._clear_archive()
        self._archive_request_id += 1
        request_id = self._archive_request_id
        self._archive_open_in_progress = True
        self._refresh_controls()
        self.status_label.setText("Reading RPF archive...")
        worker = RPFInspectWorker(request_id, archive_path, self.gtaiv_exe_path)
        worker.completed.connect(self._on_archive_loaded)
        worker.error.connect(self._on_archive_error)
        worker.finished.connect(self._on_archive_open_finished)
        self._start_worker(worker)

    def _on_archive_open_finished(self):
        self._archive_open_in_progress = False
        self._refresh_controls()

    def _on_archive_loaded(self, request_id, snapshot):
        if request_id != self._archive_request_id:
            return
        self.archive_snapshot = snapshot
        self.archive_input.setText(str(snapshot.archive_path))
        visible_count = self._rebuild_entry_tree()
        self.status_label.setText(
            self._entry_filter_status(visible_count, len(snapshot.entries))
        )
        if self._restore_entry_path is not None:
            item = self._entry_items.get(self._restore_entry_path)
            if item is None:
                self._restore_entry_path = None
                self._restore_texture_index = None
                self.status_label.setText(
                    "Archive reloaded, but the previously selected WTD entry is missing."
                )
            else:
                self.entry_tree.setCurrentItem(item)
                self.entry_tree.scrollToItem(item)

    def _on_entry_name_filter_changed(self, value: str):
        self._entry_name_filter = normalize_archive_name_filter(value)
        if self.archive_snapshot is None:
            return
        visible_count = self._rebuild_entry_tree()
        self.status_label.setText(
            self._entry_filter_status(
                visible_count, len(self.archive_snapshot.entries)
            )
        )

    def _rebuild_entry_tree(self) -> int:
        if self.archive_snapshot is None:
            return 0

        selected_path = (
            self.selected_entry.path if self.selected_entry is not None else None
        )
        visible_entries = filter_archive_entries_by_name(
            self.archive_snapshot.entries,
            self._entry_name_filter,
        )

        self.entry_tree.blockSignals(True)
        try:
            self.entry_tree.clear()
            self._entry_items.clear()
            for node in build_rpf_browser_tree(visible_entries):
                self.entry_tree.addTopLevelItem(self._tree_item(node))
            self.entry_tree.expandToDepth(0)

            selected_item = (
                self._entry_items.get(selected_path)
                if selected_path is not None
                else None
            )
            if selected_item is not None:
                self.entry_tree.setCurrentItem(selected_item)
                self.entry_tree.scrollToItem(selected_item)
        finally:
            self.entry_tree.blockSignals(False)

        if selected_path is not None and selected_path not in self._entry_items:
            self.selected_entry = None
            self.entry_details.setText("No entry selected.")
            self._restore_entry_actions()
            self._clear_wtd()

        return len(visible_entries)

    def _entry_filter_status(self, visible_count: int, total_count: int) -> str:
        archive_name = self.archive_snapshot.archive_path.name
        if self._entry_name_filter is None:
            return f"Loaded {total_count} file entries from {archive_name}."
        return (
            f"Showing {visible_count} of {total_count} entries whose name contains "
            f"{self._entry_name_filter!r} in {archive_name}."
        )

    def _on_archive_error(self, request_id: int, message: str):
        if request_id != self._archive_request_id:
            return
        self._restore_entry_path = None
        self._restore_texture_index = None
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
            self._entry_items[node.entry.path] = item
        for child in node.children:
            item.addChild(self._tree_item(child))
        return item

    def on_entry_selected(self, current, _previous):
        entry = current.data(0, _ENTRY_ROLE) if current is not None else None
        self.selected_entry = entry
        self._restore_entry_actions()
        self._clear_wtd()
        if entry is None:
            self.entry_details.setText("Directory selected.")
            return

        self.entry_details.setText(
            f"Path: {entry.path}\nSize: {self._format_bytes(entry.size)} "
            f"({entry.size} bytes)\nOffset: 0x{entry.offset:X}"
        )
        resource_suffix = Path(entry.path).suffix.casefold()
        if resource_suffix not in {".wtd", ".wdr"}:
            self.wtd_details.setText(
                "The selected entry does not contain inspectable textures."
            )
            return
        self._inspect_selected_wtd(entry.path)

    def _inspect_selected_wtd(self, entry_path: str):
        if self.archive_snapshot is None:
            return
        self._wtd_request_id += 1
        request_id = self._wtd_request_id
        digest = hashlib.sha256(entry_path.encode("utf-8")).hexdigest()[:20]
        resource_suffix = Path(entry_path).suffix.casefold()
        extracted_path = str(
            Path(self._temporary_directory.path())
            / f"{request_id}-{digest}{resource_suffix}"
        )
        self._temporary_wtd_paths.add(Path(extracted_path))
        self.wtd_details.setText(
            "Extracting and reading texture resource metadata..."
        )
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
            self._discard_temporary_wtd(local_path)
            return
        if self.selected_entry is None or self.selected_entry.path != entry_path:
            self._discard_temporary_wtd(local_path)
            return
        previous_path = self.extracted_wtd_path
        self.wtd_snapshot = snapshot
        self.extracted_wtd_path = local_path
        self._temporary_wtd_paths.add(Path(local_path))
        if previous_path and previous_path != local_path:
            self._discard_temporary_wtd(previous_path)
        resource_kind = Path(entry_path).suffix.lstrip(".").upper()
        self.wtd_details.setText(
            f"{resource_kind} textures: {len(snapshot.textures)} | "
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
        self.status_label.setText(f"Loaded texture resource {entry_path}.")
        if self._restore_entry_path == entry_path:
            restore_index = self._restore_texture_index
            self._restore_entry_path = None
            self._restore_texture_index = None
            if restore_index is not None:
                for row in range(self.texture_table.rowCount()):
                    item = self.texture_table.item(row, 0)
                    if item is not None and item.data(_TEXTURE_INDEX_ROLE) == restore_index:
                        self.texture_table.setCurrentCell(row, 0)
                        self.texture_table.scrollToItem(item)
                        break

    def _on_wtd_error(self, request_id, entry_path, message):
        if request_id != self._wtd_request_id:
            return
        if self.selected_entry is None or self.selected_entry.path != entry_path:
            return
        if self._restore_entry_path == entry_path:
            self._restore_entry_path = None
            self._restore_texture_index = None
        self.wtd_details.setText("The selected texture resource could not be read.")
        self.status_label.setText(f"Could not inspect {entry_path}.")
        QMessageBox.critical(self, "Texture Inspection Error", message)

    def on_texture_selected(self, current_row, _current_column, _previous_row, _previous_column):
        self.export_texture_button.setEnabled(False)
        self.replace_texture_button.setEnabled(False)
        self.queue_texture_button.setEnabled(False)
        self._current_preview_png_data = b""
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
        self._restore_texture_actions()
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
        self._current_preview_png_data = bytes(preview.png_data)
        image = QImage.fromData(QByteArray(preview.png_data), "PNG")
        if image.isNull():
            self.preview_label.setText("The generated preview image is invalid.")
            return
        pixmap = QPixmap.fromImage(image)
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
        self._update_queue_controls()

    def _on_preview_error(self, request_id, texture_index, message):
        if request_id != self._preview_request_id:
            return
        if self._selected_texture_index() != texture_index:
            return
        self.preview_label.setText("Preview unavailable.")
        self.status_label.setText(message)

    def export_selected_entry(self):
        if self._file_operation_in_progress():
            return
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
        self._entry_export_in_progress = True
        self._refresh_controls()
        worker = RPFEntryExportWorker(
            str(self.archive_snapshot.archive_path),
            self.gtaiv_exe_path,
            self.selected_entry.path,
            destination,
            overwrite=overwrite,
        )
        worker.completed.connect(self._on_entry_exported)
        worker.error.connect(self._on_export_error)
        worker.finished.connect(self._on_entry_export_finished)
        self._start_worker(worker)

    def _on_entry_export_finished(self):
        self._entry_export_in_progress = False
        self._refresh_controls()

    def _on_entry_exported(self, path: str):
        self.status_label.setText(f"Exported RPF entry to {path}.")
        QMessageBox.information(self, "Export Complete", f"Exported to:\n{path}")

    def replace_selected_entry(self):
        if (
            self.selected_entry is None
            or self.archive_snapshot is None
            or self._replacement_worker is not None
        ):
            return
        if self._export_in_progress():
            QMessageBox.warning(
                self,
                "RPF Entry Replacement",
                "Wait for the current export to finish before modifying the archive.",
            )
            return

        replacement_path = select_open_file(
            self,
            "Select Replacement File",
            PathHistoryKey.RPF_ENTRY_REPLACEMENT,
            file_filter="All Files (*)",
            fallback=str(self.archive_snapshot.archive_path),
        )
        if not replacement_path:
            return

        archive_path = str(self.archive_snapshot.archive_path)
        replacement = Path(replacement_path).expanduser().resolve()
        archive = Path(archive_path).expanduser().resolve()
        if replacement == archive:
            QMessageBox.warning(
                self,
                "RPF Entry Replacement",
                "The replacement file must not be the RPF archive itself.",
            )
            return
        if not replacement.is_file():
            QMessageBox.warning(
                self,
                "RPF Entry Replacement",
                f"The replacement file does not exist:\n{replacement}",
            )
            return
        try:
            replacement_size = replacement.stat().st_size
        except OSError as exc:
            QMessageBox.critical(
                self,
                "RPF Entry Replacement",
                f"Could not read the replacement file:\n{exc}",
            )
            return

        response = QMessageBox.question(
            self,
            "Confirm RPF Entry Replacement",
            "This operation replaces one existing entry inside the selected RPF "
            "archive. It does not add, remove, or rename entries. The toolkit will "
            "stage and verify the modified archive, retain a timestamped backup, and "
            "only then commit the change.\n\n"
            f"Archive: {archive_path}\n"
            f"RPF entry: {self.selected_entry.path}\n"
            f"Current size: {self._format_bytes(self.selected_entry.size)} "
            f"({self.selected_entry.size} bytes)\n"
            f"Replacement file: {replacement}\n"
            f"Replacement size: {self._format_bytes(replacement_size)} "
            f"({replacement_size} bytes)\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        self._replacement_result = None
        self._replacement_error = None
        self._restore_entry_path = self.selected_entry.path
        self._restore_texture_index = None
        worker = RPFEntryReplaceWorker(
            archive_path,
            self.gtaiv_exe_path,
            self.selected_entry.path,
            str(replacement),
        )
        self._replacement_worker = worker
        self._invalidate_browser_reads()
        self._refresh_controls()
        self.status_label.setText(
            f"Replacing RPF entry {self.selected_entry.path} and verifying the staged "
            "archive..."
        )
        worker.completed.connect(self._on_entry_replaced)
        worker.error.connect(self._on_entry_replacement_error)
        worker.finished.connect(
            lambda current=worker: self._on_entry_replacement_finished(current)
        )
        self._start_worker(worker)

    def _on_entry_replaced(self, result):
        self._replacement_result = result
        self.status_label.setText(
            f"Replaced RPF entry {result.entry_path}; finalizing the UI state..."
        )

    def _on_entry_replacement_error(self, message: str):
        self._replacement_result = None
        self._replacement_error = message
        self._restore_entry_path = None
        self._restore_texture_index = None
        self.status_label.setText("RPF entry replacement failed.")

    def _on_entry_replacement_finished(self, worker):
        if self._replacement_worker is not worker:
            return
        self._replacement_worker = None
        result = self._replacement_result
        error_message = self._replacement_error
        self._replacement_result = None
        self._replacement_error = None
        self._refresh_controls()

        if result is not None:
            self._record_backup(result.backup_path)
            relocation = "yes" if result.relocated else "no"
            QMessageBox.information(
                self,
                "RPF Entry Replaced",
                f"Entry {result.entry_path} was replaced and the committed archive "
                "was verified.\n\n"
                f"Previous size: {self._format_bytes(result.previous_size)} "
                f"({result.previous_size} bytes)\n"
                f"Replacement size: {self._format_bytes(result.replacement_size)} "
                f"({result.replacement_size} bytes)\n"
                f"Backup: {result.backup_path}\n"
                f"Entry relocated: {relocation}",
            )
            self.open_archive()
            return

        QMessageBox.critical(
            self,
            "RPF Entry Replacement Error",
            "The transactional replacement failed. The original RPF archive was "
            f"preserved or restored.\n\n{error_message or 'Unknown error'}",
        )
        self._restore_entry_actions()

    def export_selected_texture(self):
        if self._file_operation_in_progress():
            return
        texture_index = self._selected_texture_index()
        if texture_index is None or self.wtd_snapshot is None or not self.extracted_wtd_path:
            return
        texture = self.wtd_snapshot.texture(texture_index)
        suggested = self._safe_texture_filename(texture.name)
        destination = select_save_file(
            self,
            "Export Texture",
            PathHistoryKey.WTD_TEXTURE_EXPORT,
            file_filter=(
                "PNG Image (*.png);;DDS Texture (*.dds);;All Files (*)"
            ),
            suggested_name=suggested,
            fallback=self.extracted_wtd_path,
        )
        if not destination:
            return
        destination_suffix = Path(destination).suffix.casefold()
        if not destination_suffix:
            destination += ".png"
        elif destination_suffix not in {".png", ".dds"}:
            QMessageBox.warning(
                self,
                "Texture Export",
                "Texture export supports only PNG and DDS destinations.",
            )
            return
        overwrite = self._confirm_overwrite(destination)
        if overwrite is None:
            return
        self._texture_export_in_progress = True
        self._refresh_controls()
        worker = WTDTextureExportWorker(
            self.extracted_wtd_path,
            texture_index,
            destination,
            overwrite=overwrite,
        )
        worker.completed.connect(self._on_texture_exported)
        worker.error.connect(self._on_export_error)
        worker.finished.connect(self._on_texture_export_finished)
        self._start_worker(worker)

    def queue_selected_texture_replacement(self):
        texture_index = self._selected_texture_index()
        if (
            texture_index is None
            or self.wtd_snapshot is None
            or self.archive_snapshot is None
            or self.selected_entry is None
            or self._replacement_worker is not None
        ):
            return

        texture = self.wtd_snapshot.texture(texture_index)
        if not texture.replaceable:
            QMessageBox.warning(
                self,
                "Queue Texture Replacement",
                f"The {texture.format_name} texture format is not replaceable.",
            )
            return
        if not self._current_preview_png_data:
            QMessageBox.warning(
                self,
                "Queue Texture Replacement",
                "Wait for the current texture preview to finish rendering.",
            )
            return

        image_path = select_open_file(
            self,
            "Select Queued Replacement Image",
            PathHistoryKey.WTD_TEXTURE_REPLACEMENT,
            file_filter=(
                "Supported Images (*.png *.webp *.jpg *.jpeg *.bmp *.tga);;"
                "PNG Images (*.png);;WebP Images (*.webp);;All Files (*)"
            ),
        )
        if not image_path:
            return

        archive_path = self.archive_snapshot.archive_path.resolve()
        queued_archive = self._replacement_queue.archive_path
        if queued_archive is not None and queued_archive != archive_path:
            response = QMessageBox.question(
                self,
                "Replacement Queue Uses Another Archive",
                "The current queue belongs to a different archive. Clear it and "
                "start a queue for the open archive?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
            self._replacement_queue.clear()

        request = TextureReplacementRequest(
            entry_path=self.selected_entry.path,
            texture_index=texture.index,
            texture_name=texture.name,
            image_path=Path(image_path),
        )
        item = QueuedTextureReplacement(
            request=request,
            resource_kind=Path(self.selected_entry.path).suffix.lstrip(".").upper(),
            dimensions=f"{texture.width} × {texture.height}",
            format_name=texture.format_name,
            target_png_data=bytes(self._current_preview_png_data),
        )
        if self._replacement_queue.contains(item.key):
            response = QMessageBox.question(
                self,
                "Replace Queued Image",
                f"A replacement is already queued for {texture.name}. "
                "Use the newly selected image instead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return

        self._replacement_queue.add(archive_path, item)
        self.status_label.setText(
            f"Queued replacement for {self.selected_entry.path} / {texture.name}."
        )
        self._update_queue_controls()

    def show_replacement_queue(self):
        if not self._replacement_queue.items:
            return
        try:
            dialog = TextureReplacementQueueDialog(self._replacement_queue, self)
            dialog.apply_requested.connect(
                lambda current=dialog: self._apply_queued_replacements(current)
            )
            dialog.exec()
        except Exception as exc:
            self.status_label.setText("Could not open the replacement queue.")
            QMessageBox.critical(
                self,
                "Replacement Queue Error",
                f"Could not open the replacement queue.\n\n{exc}",
            )
        finally:
            self._update_queue_controls()

    def _apply_queued_replacements(self, dialog):
        if self._replacement_worker is not None or self.archive_snapshot is None:
            return
        queue_archive = self._replacement_queue.archive_path
        if queue_archive is None or queue_archive != self.archive_snapshot.archive_path.resolve():
            QMessageBox.warning(
                self,
                "Apply Replacement Queue",
                "Open the archive that owns this replacement queue before applying it.",
            )
            return

        requests = self._replacement_queue.requests()
        if not requests:
            return
        first = requests[0]
        self._replacement_result = None
        self._replacement_error = None
        self._restore_entry_path = first.entry_path
        self._restore_texture_index = first.texture_index
        worker = RPFTextureBatchReplaceWorker(
            str(queue_archive),
            self.gtaiv_exe_path,
            requests,
        )
        self._replacement_worker = worker
        self._invalidate_browser_reads()
        self._refresh_controls()
        self.status_label.setText(
            f"Applying {len(requests)} queued texture replacements in one transaction..."
        )
        worker.completed.connect(self._on_texture_batch_replaced)
        worker.error.connect(self._on_texture_batch_replacement_error)
        worker.finished.connect(
            lambda current=worker: self._on_texture_batch_replacement_finished(current)
        )
        self._start_worker(worker)
        dialog.accept()

    def _on_texture_batch_replaced(self, result):
        self._replacement_result = result
        self.status_label.setText(
            f"Applied {len(result.replacements)} replacements; finalizing the UI state..."
        )

    def _on_texture_batch_replacement_error(self, message: str):
        self._replacement_result = None
        self._replacement_error = message
        self._restore_entry_path = None
        self._restore_texture_index = None
        self.status_label.setText("Batch texture replacement failed.")

    def _on_texture_batch_replacement_finished(self, worker):
        if self._replacement_worker is not worker:
            return
        self._replacement_worker = None
        result = self._replacement_result
        error_message = self._replacement_error
        self._replacement_result = None
        self._replacement_error = None
        self._refresh_controls()

        if result is not None:
            self._record_backup(result.backup_path)
            relocated = ", ".join(result.relocated_entries) or "none"
            replacement_count = len(result.replacements)
            entry_count = len(result.entries)
            self._replacement_queue.clear()
            self._update_queue_controls()
            QMessageBox.information(
                self,
                "Queued Replacements Applied",
                f"Applied {replacement_count} texture replacements across "
                f"{entry_count} resource entries. The committed archive was verified.\n\n"
                f"Backup: {result.backup_path}\n"
                f"Relocated entries: {relocated}",
            )
            self.open_archive()
            return

        QMessageBox.critical(
            self,
            "Batch Texture Replacement Error",
            "The batch transaction failed. The original archive was preserved or "
            f"restored. The queue was kept for another attempt.\n\n"
            f"{error_message or 'Unknown error'}",
        )
        self._restore_texture_actions()
        self._update_queue_controls()

    def _update_queue_controls(self):
        busy = self._file_operation_in_progress()
        texture_index = self._selected_texture_index()
        can_queue = False
        if (
            not busy
            and texture_index is not None
            and self.wtd_snapshot is not None
            and self.selected_entry is not None
            and self._current_preview_png_data
        ):
            can_queue = self.wtd_snapshot.texture(texture_index).replaceable
        self.queue_texture_button.setEnabled(can_queue)
        count = len(self._replacement_queue)
        self.review_queue_button.setText(f"Review/apply queue ({count})")
        self.review_queue_button.setEnabled(not busy and count > 0)

    def replace_selected_texture(self):
        texture_index = self._selected_texture_index()
        if (
            texture_index is None
            or self.wtd_snapshot is None
            or self.archive_snapshot is None
            or self.selected_entry is None
            or self._replacement_worker is not None
        ):
            return

        if self._export_in_progress():
            QMessageBox.warning(
                self,
                "Texture Replacement",
                "Wait for the current export to finish before modifying the archive.",
            )
            return

        texture = self.wtd_snapshot.texture(texture_index)
        if not texture.replaceable:
            QMessageBox.warning(
                self,
                "Texture Replacement",
                f"The {texture.format_name} texture format is not replaceable.",
            )
            return

        image_path = select_open_file(
            self,
            "Select Replacement Image",
            PathHistoryKey.WTD_TEXTURE_REPLACEMENT,
            file_filter=(
                "Supported Images (*.png *.webp *.jpg *.jpeg *.bmp *.tga);;"
                "PNG Images (*.png);;WebP Images (*.webp);;All Files (*)"
            ),
        )
        if not image_path:
            return

        archive_path = str(self.archive_snapshot.archive_path)
        response = QMessageBox.question(
            self,
            "Confirm Texture Replacement",
            "This operation modifies the selected GTA IV archive in place after "
            "staging and verification. A timestamped backup of the original "
            "archive will be retained.\n\n"
            f"Archive: {archive_path}\n"
            f"Texture resource: {self.selected_entry.path}\n"
            f"Texture: #{texture.index} {texture.name} "
            f"({texture.width} × {texture.height}, {texture.format_name})\n"
            f"Replacement image: {image_path}\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        self._replacement_result = None
        self._replacement_error = None
        self._restore_entry_path = self.selected_entry.path
        self._restore_texture_index = texture.index
        worker = RPFWTDTextureReplaceWorker(
            archive_path,
            self.gtaiv_exe_path,
            self.selected_entry.path,
            texture.index,
            image_path,
        )
        self._replacement_worker = worker
        self._invalidate_browser_reads()
        self._refresh_controls()
        self.status_label.setText(
            f"Replacing texture {texture.name} and verifying the staged archive..."
        )
        worker.completed.connect(self._on_texture_replaced)
        worker.error.connect(self._on_texture_replacement_error)
        worker.finished.connect(
            lambda current=worker: self._on_texture_replacement_finished(current)
        )
        self._start_worker(worker)

    def _on_texture_replaced(self, result):
        self._replacement_result = result
        self.status_label.setText(
            f"Replaced texture {result.texture.name}; finalizing the UI state..."
        )

    def _on_texture_replacement_error(self, message: str):
        self._replacement_result = None
        self._replacement_error = message
        self._restore_entry_path = None
        self._restore_texture_index = None
        self.status_label.setText("Texture replacement failed.")

    def _on_texture_replacement_finished(self, worker):
        if self._replacement_worker is not worker:
            return
        self._replacement_worker = None
        result = self._replacement_result
        error_message = self._replacement_error
        self._replacement_result = None
        self._replacement_error = None
        self._refresh_controls()

        if result is not None:
            self._record_backup(result.backup_path)
            relocation = "yes" if result.relocated else "no"
            QMessageBox.information(
                self,
                "Texture Replaced",
                f"Texture #{result.texture.index} {result.texture.name} was replaced "
                "and the committed archive was verified.\n\n"
                f"Backup: {result.backup_path}\n"
                f"Archive entry relocated: {relocation}",
            )
            self.open_archive()
            return

        QMessageBox.critical(
            self,
            "Texture Replacement Error",
            "The transactional replacement failed. The original archive "
            f"was preserved or restored.\n\n{error_message or 'Unknown error'}",
        )
        self._restore_texture_actions()

    def _file_operation_in_progress(self) -> bool:
        return self._replacement_worker is not None or self._export_in_progress()

    def _export_in_progress(self) -> bool:
        return self._entry_export_in_progress or self._texture_export_in_progress

    def _refresh_controls(self):
        busy = self._file_operation_in_progress()
        self.archive_input.setEnabled(not busy and not self._archive_open_in_progress)
        self.browse_button.setEnabled(not busy and not self._archive_open_in_progress)
        self.open_button.setEnabled(not busy and not self._archive_open_in_progress)
        self.back_button.setEnabled(not busy)
        self.entry_tree.setEnabled(not busy)
        self.entry_name_filter.setEnabled(
            self.archive_snapshot is not None
            and not busy
            and not self._archive_open_in_progress
        )
        self.texture_table.setEnabled(not busy)
        self.open_backup_button.setEnabled(
            not busy
            and self._last_backup_path is not None
            and self._last_backup_path.parent.is_dir()
        )
        self._restore_entry_actions()
        self._restore_texture_actions()
        self._update_queue_controls()

    def _restore_entry_actions(self):
        enabled = self.selected_entry is not None and not self._file_operation_in_progress()
        self.export_entry_button.setEnabled(enabled)
        self.replace_entry_button.setEnabled(enabled)

    def _restore_texture_actions(self):
        if self._file_operation_in_progress():
            self.export_texture_button.setEnabled(False)
            self.replace_texture_button.setEnabled(False)
            return
        texture_index = self._selected_texture_index()
        if texture_index is None or self.wtd_snapshot is None:
            self.export_texture_button.setEnabled(False)
            self.replace_texture_button.setEnabled(False)
            return
        texture = self.wtd_snapshot.texture(texture_index)
        self.export_texture_button.setEnabled(texture.extractable)
        self.replace_texture_button.setEnabled(texture.replaceable)

    def _on_texture_exported(self, path: str):
        self.status_label.setText(f"Exported texture to {path}.")
        QMessageBox.information(self, "Export Complete", f"Exported to:\n{path}")

    def _on_export_error(self, message: str):
        self.status_label.setText("Export failed.")
        QMessageBox.critical(self, "Export Error", message)

    def _on_texture_export_finished(self):
        self._texture_export_in_progress = False
        self._refresh_controls()

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
        self._entry_items.clear()
        self.entry_details.setText("No entry selected.")
        self.export_entry_button.setEnabled(False)
        self.replace_entry_button.setEnabled(False)
        self._clear_wtd()

    def _clear_wtd(self):
        previous_path = self.extracted_wtd_path
        self._wtd_request_id += 1
        self._preview_request_id += 1
        self.wtd_snapshot = None
        self.extracted_wtd_path = ""
        self._current_preview_png_data = b""
        self.texture_table.setRowCount(0)
        self.wtd_details.setText("Select a .wtd or .wdr entry to inspect its textures.")
        self.preview_label.clear()
        self.preview_label.setText("No texture selected.")
        self.export_texture_button.setEnabled(False)
        self.replace_texture_button.setEnabled(False)
        if previous_path:
            self._discard_temporary_wtd(previous_path)

    def _invalidate_browser_reads(self):
        self._wtd_request_id += 1
        self._preview_request_id += 1

    def _discard_temporary_wtd(self, path: str | Path):
        if not path:
            return
        temporary = Path(path)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            return
        self._temporary_wtd_paths.discard(temporary)

    def _cleanup_temporary_wtd_files(self):
        current = Path(self.extracted_wtd_path) if self.extracted_wtd_path else None
        for temporary in tuple(self._temporary_wtd_paths):
            if current is not None and temporary == current:
                continue
            self._discard_temporary_wtd(temporary)

    def _record_backup(self, backup_path):
        self._last_backup_path = Path(backup_path).expanduser().resolve()
        self._refresh_controls()

    def _open_latest_backup_folder(self):
        if self._last_backup_path is None:
            return
        directory = self._last_backup_path.parent
        if not directory.is_dir():
            QMessageBox.warning(
                self,
                "Backup Folder",
                f"The backup directory no longer exists:\n{directory}",
            )
            self._last_backup_path = None
            self._refresh_controls()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            QMessageBox.warning(
                self,
                "Backup Folder",
                f"Could not open the backup directory:\n{directory}",
            )

    def has_active_workers(self) -> bool:
        return bool(self._workers)

    def active_operation_description(self) -> str:
        if self._replacement_worker is not None:
            return "an RPF replacement transaction"
        if self._entry_export_in_progress:
            return "an RPF entry export"
        if self._texture_export_in_progress:
            return "a WTD texture export"
        if self._archive_open_in_progress:
            return "an RPF archive inspection"
        if self._workers:
            return "an RPF browser background operation"
        return ""

    def _start_worker(self, worker):
        self._workers.add(worker)
        worker.finished.connect(lambda current=worker: self._release_worker(current))
        worker.start()

    def _release_worker(self, worker):
        self._workers.discard(worker)
        worker.deleteLater()
        self._cleanup_temporary_wtd_files()

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
    def _safe_texture_filename(name: str, suffix: str = ".png") -> str:
        stem = _SAFE_FILENAME.sub("_", name).strip("._") or "texture"
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        return f"{stem}{normalized_suffix.casefold()}"

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
