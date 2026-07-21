from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTemporaryDir, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.radio_logo.images import (
    LogoFitMode,
    format_logo_requirements,
    inspect_logo_image,
)
from core.radio_logo.installer import (
    KNOWN_RADIO_LOGO_WTD_NAMES,
    RadioLogoTarget,
    get_radio_logo_destination_dir,
    install_radio_logo_pack,
)
from core.radio_logo.station_pack import (
    create_station_logo_plan,
    list_station_logo_bases,
    prepare_station_logo_previews,
)
from core.radio_logo.workflow import install_station_logo_from_image
from ui.path_dialogs import (
    PathHistoryKey,
    select_open_file,
    select_open_files,
)
from ui.styles import BUTTON_STYLE


_TARGET_LABELS = (
    (RadioLogoTarget.GTA_IV, "Grand Theft Auto IV"),
    (RadioLogoTarget.TLAD, "The Lost and Damned"),
    (RadioLogoTarget.TBOGT, "The Ballad of Gay Tony"),
)

_STATION_LABELS = {
    "beat": "The Beat 102.7",
    "electrochoc": "Electro-Choc",
    "fusion": "Fusion FM",
    "if99": "IF99",
    "independence": "Independence FM",
    "integrity": "Integrity 2.0",
    "jnr": "JNR",
    "k109": "K109 The Studio",
    "lchc": "Liberty City Hardcore",
    "lrr": "Liberty Rock Radio",
    "massiveb": "Massive B Soundsystem 96.9",
    "plr": "Public Liberty Radio",
    "radiobroker": "Radio Broker",
    "ramjamfm": "RamJam FM",
    "sanjuan": "San Juan Sounds",
    "selfactualizationfm": "Self-Actualization FM",
    "theclassics": "The Classics 104.1",
    "thejourney": "The Journey",
    "thevibe": "The Vibe 98.8",
    "tuffgong": "Tuff Gong Radio",
    "vicecityfm": "Vice City FM",
    "vladivostok": "Vladivostok FM",
    "wktt": "WKTT",
}

_PREVIEW_SIZE = QSize(300, 150)


class StationLogoInstallWorker(QThread):
    completed = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        gtaiv_path,
        target,
        station_base,
        source_image,
        use_direct,
        fit_mode,
        padding_ratio,
    ):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.target = target
        self.station_base = station_base
        self.source_image = source_image
        self.use_direct = use_direct
        self.fit_mode = fit_mode
        self.padding_ratio = padding_ratio

    def run(self):
        try:
            result = install_station_logo_from_image(
                self.gtaiv_path,
                self.target,
                self.station_base,
                self.source_image,
                use_direct=self.use_direct,
                fit_mode=self.fit_mode,
                padding_ratio=self.padding_ratio,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return

        self.completed.emit(result)


class PreparedPackInstallWorker(QThread):
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, gtaiv_path, source_files, target, use_direct):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.source_files = list(source_files)
        self.target = target
        self.use_direct = use_direct

    def run(self):
        try:
            result = install_radio_logo_pack(
                self.gtaiv_path,
                self.source_files,
                self.target,
                use_direct=self.use_direct,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return

        self.completed.emit(result)


class RadioLogoInstallPage(QWidget):
    def __init__(self, gtaiv_path, use_direct, on_back):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.use_direct = use_direct
        self.on_back = on_back
        self.worker = None
        self.source_image = ""
        self.image_info = None
        self.current_plan = None
        self.preview_ready = False
        self.preview_directory = QTemporaryDir(
            "gtaiv-toolkit-radio-logo-preview-XXXXXX"
        )
        self.preview_directory.setAutoRemove(True)

        layout = QVBoxLayout(self)

        title = QLabel("Radio Logo Tools", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FFC107;")
        layout.addWidget(title)

        description = QLabel(
            "Replace a station logo from one image, or install a prepared "
            "radio_hud*.wtd pack in advanced mode.",
            self,
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        mode_text = (
            "Direct replacement: existing game files will be backed up before replacement."
            if self.use_direct
            else "FusionFix mode: files will be installed under the game's update directory."
        )
        self.mode_label = QLabel(mode_text, self)
        self.mode_label.setWordWrap(True)
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mode_label.setStyleSheet("font-weight: bold; color: #B0BEC5;")
        layout.addWidget(self.mode_label)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Game target:", self))
        self.target_combo = QComboBox(self)
        target_row.addWidget(self.target_combo, 1)
        layout.addLayout(target_row)

        self.destination_label = QLabel(self)
        self.destination_label.setWordWrap(True)
        self.destination_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.destination_label)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_image_tab(), "From Image")
        self.tabs.addTab(self._build_pack_tab(), "Prepared WTD Pack")
        layout.addWidget(self.tabs, 1)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        navigation = QHBoxLayout()
        self.back_button = QPushButton("Back", self)
        self.back_button.setStyleSheet(BUTTON_STYLE)
        self.back_button.clicked.connect(self.on_back)
        navigation.addWidget(self.back_button)
        layout.addLayout(navigation)

        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        self._populate_targets()
        self._update_controls()

    def _build_image_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        station_row = QHBoxLayout()
        station_row.addWidget(QLabel("Station:", tab))
        self.station_combo = QComboBox(tab)
        self.station_combo.currentIndexChanged.connect(self._on_station_changed)
        station_row.addWidget(self.station_combo, 1)
        layout.addLayout(station_row)

        self.requirements_label = QLabel(
            "Select a game target and station to inspect its texture requirements.",
            tab,
        )
        self.requirements_label.setWordWrap(True)
        self.requirements_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.requirements_label)

        image_row = QHBoxLayout()
        self.image_path_label = QLabel("No image selected", tab)
        self.image_path_label.setWordWrap(True)
        self.image_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        image_row.addWidget(self.image_path_label, 1)

        self.select_image_button = QPushButton("Select Image", tab)
        self.select_image_button.setStyleSheet(BUTTON_STYLE)
        self.select_image_button.clicked.connect(self.select_image)
        image_row.addWidget(self.select_image_button)
        layout.addLayout(image_row)

        self.image_info_label = QLabel(
            "Recommended input: transparent PNG or WebP, 2:1 aspect ratio, "
            "at least 256 x 128 px.",
            tab,
        )
        self.image_info_label.setWordWrap(True)
        layout.addWidget(self.image_info_label)

        fit_row = QHBoxLayout()
        fit_row.addWidget(QLabel("Image fit:", tab))
        self.fit_combo = QComboBox(tab)
        self.fit_combo.addItem("Fit — keep the whole image", LogoFitMode.FIT.value)
        self.fit_combo.addItem("Fill — crop to the canvas", LogoFitMode.FILL.value)
        self.fit_combo.addItem("Stretch — ignore aspect ratio", LogoFitMode.STRETCH.value)
        self.fit_combo.currentIndexChanged.connect(self._refresh_preview)
        fit_row.addWidget(self.fit_combo, 1)

        fit_row.addWidget(QLabel("Safe padding:", tab))
        self.padding_spin = QDoubleSpinBox(tab)
        self.padding_spin.setRange(0.0, 30.0)
        self.padding_spin.setDecimals(1)
        self.padding_spin.setSingleStep(1.0)
        self.padding_spin.setSuffix("%")
        self.padding_spin.valueChanged.connect(self._refresh_preview)
        fit_row.addWidget(self.padding_spin)
        layout.addLayout(fit_row)

        previews = QHBoxLayout()
        color_column = QVBoxLayout()
        color_title = QLabel(
            "Selected / color texture\n(black is masked by the game HUD)",
            tab,
        )
        color_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color_column.addWidget(color_title)
        self.color_preview = self._create_preview_label(tab)
        color_column.addWidget(self.color_preview)
        previews.addLayout(color_column)

        noncolored_column = QVBoxLayout()
        noncolored_title = QLabel("Unselected / grayscale texture", tab)
        noncolored_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        noncolored_column.addWidget(noncolored_title)
        self.noncolored_preview = self._create_preview_label(tab)
        noncolored_column.addWidget(self.noncolored_preview)
        previews.addLayout(noncolored_column)
        layout.addLayout(previews)

        self.preview_status_label = QLabel("", tab)
        self.preview_status_label.setWordWrap(True)
        self.preview_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview_status_label)

        self.install_image_button = QPushButton("Install Station Logo", tab)
        self.install_image_button.setStyleSheet(BUTTON_STYLE)
        self.install_image_button.clicked.connect(self.install_image)
        layout.addWidget(self.install_image_button)

        return tab

    def _build_pack_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        description = QLabel(
            "Advanced mode for complete prepared WTD files. Only recognized "
            "radio_hud*.wtd filenames are accepted.",
            tab,
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.files_list = QListWidget(tab)
        self.files_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.files_list.itemSelectionChanged.connect(self._update_controls)
        layout.addWidget(self.files_list)

        file_buttons = QHBoxLayout()
        self.select_pack_button = QPushButton("Select WTD Files", tab)
        self.select_pack_button.setStyleSheet(BUTTON_STYLE)
        self.select_pack_button.clicked.connect(self.select_pack_files)
        file_buttons.addWidget(self.select_pack_button)

        self.remove_pack_button = QPushButton("Remove Selected", tab)
        self.remove_pack_button.setStyleSheet(BUTTON_STYLE)
        self.remove_pack_button.clicked.connect(self.remove_selected_pack_files)
        file_buttons.addWidget(self.remove_pack_button)

        self.clear_pack_button = QPushButton("Clear", tab)
        self.clear_pack_button.setStyleSheet(BUTTON_STYLE)
        self.clear_pack_button.clicked.connect(self.clear_pack_files)
        file_buttons.addWidget(self.clear_pack_button)
        layout.addLayout(file_buttons)

        self.install_pack_button = QPushButton("Install Prepared Pack", tab)
        self.install_pack_button.setStyleSheet(BUTTON_STYLE)
        self.install_pack_button.clicked.connect(self.install_pack)
        layout.addWidget(self.install_pack_button)

        return tab

    def _create_preview_label(self, parent):
        label = QLabel("Preview unavailable", parent)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(_PREVIEW_SIZE)
        label.setStyleSheet(
            "background-color: #263238; border: 1px solid #607D8B; "
            "color: #B0BEC5;"
        )
        return label

    def _populate_targets(self):
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        for target, label in _TARGET_LABELS:
            original_directory = get_radio_logo_destination_dir(
                self.gtaiv_path,
                target,
                use_direct=True,
            )
            if original_directory.is_dir():
                self.target_combo.addItem(label, target.value)

        if self.target_combo.count() == 0:
            self.target_combo.addItem(
                "No supported texture directories found",
                None,
            )
            self.target_combo.setEnabled(False)
        self.target_combo.blockSignals(False)
        self._on_target_changed()

    def _selected_target(self):
        value = self.target_combo.currentData()
        return RadioLogoTarget(value) if value else None

    def _selected_station(self):
        value = self.station_combo.currentData()
        return value if value else None

    def _selected_fit_mode(self):
        return LogoFitMode(self.fit_combo.currentData())

    def _padding_ratio(self):
        return self.padding_spin.value() / 100.0

    def _on_target_changed(self, *_):
        target = self._selected_target()
        if target is None:
            self.destination_label.setText("Destination: unavailable")
            self.station_combo.clear()
            self.current_plan = None
            self._clear_previews("No supported target is available.")
            self._update_controls()
            return

        destination = get_radio_logo_destination_dir(
            self.gtaiv_path,
            target,
            use_direct=self.use_direct,
        )
        self.destination_label.setText(f"Destination: {destination}")
        self._populate_stations(target)
        self._update_controls()

    def _populate_stations(self, target):
        self.station_combo.blockSignals(True)
        self.station_combo.clear()
        try:
            stations = list_station_logo_bases(
                self.gtaiv_path,
                target,
                direct_source=self.use_direct,
            )
        except Exception as exc:
            self.station_combo.addItem(f"Unable to read station textures: {exc}", None)
            stations = ()

        for station in stations:
            label = _STATION_LABELS.get(
                station,
                station.replace("_", " ").title(),
            )
            self.station_combo.addItem(f"{label}  [{station}]", station)

        if not stations and self.station_combo.count() == 0:
            self.station_combo.addItem("No replaceable station logos found", None)

        self.station_combo.blockSignals(False)
        self._on_station_changed()

    def _on_station_changed(self, *_):
        target = self._selected_target()
        station = self._selected_station()
        self.current_plan = None

        if target is None or station is None:
            self.requirements_label.setText(
                "Select a game target and station to inspect its texture requirements."
            )
            self._clear_previews("Select a station.")
            self._update_controls()
            return

        try:
            plan = create_station_logo_plan(
                self.gtaiv_path,
                target,
                station,
                direct_source=self.use_direct,
            )
        except Exception as exc:
            self.requirements_label.setText(f"Unable to inspect station textures: {exc}")
            self._clear_previews("Preview unavailable.")
            self._update_controls()
            return

        self.current_plan = plan
        color_guidance = format_logo_requirements(
            plan.color_canvas.width,
            plan.color_canvas.height,
        )
        self.requirements_label.setText(
            f"{color_guidance}\n"
            f"Unselected texture: {plan.noncolored_canvas.width} x "
            f"{plan.noncolored_canvas.height} px, "
            f"{plan.noncolored_canvas.format_name}."
        )
        self._refresh_preview()
        self._update_controls()

    def select_image(self):
        selected = select_open_file(
            self,
            "Select Radio Logo Image",
            PathHistoryKey.RADIO_LOGO_IMAGE,
            file_filter=(
                "Supported Images (*.png *.webp *.jpg *.jpeg *.bmp *.tga);;"
                "PNG Images (*.png);;WebP Images (*.webp);;All Files (*)"
            ),
        )
        if not selected:
            return

        try:
            info = inspect_logo_image(selected)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Invalid Radio Logo Image",
                str(exc),
                QMessageBox.StandardButton.Ok,
            )
            return

        self.source_image = selected
        self.image_info = info
        self.image_path_label.setText(str(Path(selected)))
        alpha_text = (
            "Transparency detected."
            if info.has_transparency
            else "No transparency detected. The inactive logo can appear as a solid rectangle."
        )
        self.image_info_label.setText(
            f"Source: {info.width} x {info.height} px, mode {info.mode}. "
            f"{alpha_text}"
        )
        self.image_info_label.setStyleSheet(
            "color: #FFB74D;" if not info.has_transparency else ""
        )
        self._refresh_preview()
        self._update_controls()

    def _refresh_preview(self, *_):
        if (
            self.current_plan is None
            or not self.source_image
            or not self.preview_directory.isValid()
        ):
            self._clear_previews("Select a station and source image.")
            self._update_controls()
            return

        try:
            result = prepare_station_logo_previews(
                self.current_plan,
                self.source_image,
                self.preview_directory.path(),
                fit_mode=self._selected_fit_mode(),
                padding_ratio=self._padding_ratio(),
                overwrite=True,
            )
            self._set_preview_pixmap(
                self.color_preview,
                result.color_preview_path,
            )
            self._set_preview_pixmap(
                self.noncolored_preview,
                result.noncolored_preview_path,
            )
        except Exception as exc:
            self._clear_previews(f"Preview error: {exc}")
            self._update_controls()
            return

        self.preview_ready = True
        self.preview_status_label.setStyleSheet("")
        self.preview_status_label.setText(
            "Preview matches the images that will be encoded into the WTD files."
        )
        self._update_controls()

    def _set_preview_pixmap(self, label, path):
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise ValueError(f"unable to load generated preview: {path}")
        label.setText("")
        label.setPixmap(
            pixmap.scaled(
                _PREVIEW_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _clear_previews(self, message):
        self.preview_ready = False
        for label in (self.color_preview, self.noncolored_preview):
            label.clear()
            label.setText("Preview unavailable")
        self.preview_status_label.setStyleSheet("color: #FFB74D;")
        self.preview_status_label.setText(message)

    def install_image(self):
        target = self._selected_target()
        station = self._selected_station()
        if (
            target is None
            or station is None
            or not self.source_image
            or self.current_plan is None
        ):
            return

        if self.image_info is not None and not self.image_info.has_transparency:
            answer = QMessageBox.question(
                self,
                "Image Has No Transparency",
                "The selected image has no transparent pixels. Its background "
                "will remain visible in the inactive radio logo.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        destination = get_radio_logo_destination_dir(
            self.gtaiv_path,
            target,
            use_direct=self.use_direct,
        )
        answer = QMessageBox.question(
            self,
            "Confirm Radio Logo Replacement",
            f"Station: {self.station_combo.currentText()}\n"
            f"Target: {self.target_combo.currentText()}\n"
            f"Destination: {destination}\n"
            f"Image: {self.source_image}\n"
            f"Fit: {self._selected_fit_mode().value}\n"
            f"Padding: {self.padding_spin.value():.1f}%\n\n"
            "Build and install this logo?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        worker = StationLogoInstallWorker(
            self.gtaiv_path,
            target,
            station,
            self.source_image,
            self.use_direct,
            self._selected_fit_mode(),
            self._padding_ratio(),
        )
        self._start_worker(
            worker,
            self._on_station_install_completed,
        )

    def _on_station_install_completed(self, result):
        lines = [
            f"Station: {result.station_base}",
            f"Destination: {result.destination_directory}",
        ]
        for item in result.installed_files:
            lines.append(f"Installed: {item.destination_path}")
            if item.backup_path:
                lines.append(f"Backup: {item.backup_path}")

        QMessageBox.information(
            self,
            "Radio Logo Installed",
            "\n\n".join(lines),
        )

    def _selected_pack_paths(self):
        return [
            self.files_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.files_list.count())
        ]

    def select_pack_files(self):
        selected = select_open_files(
            self,
            "Select Radio Logo WTD Files",
            PathHistoryKey.RADIO_LOGO_PACK,
            file_filter="GTA IV Texture Dictionaries (*.wtd)",
        )
        if not selected:
            return

        current_names = {
            os.path.basename(path).casefold()
            for path in self._selected_pack_paths()
        }
        unsupported = []
        duplicates = []
        accepted = []

        for path in selected:
            normalized_name = os.path.basename(path).casefold()
            if normalized_name not in KNOWN_RADIO_LOGO_WTD_NAMES:
                unsupported.append(os.path.basename(path))
                continue
            if normalized_name in current_names:
                duplicates.append(os.path.basename(path))
                continue

            current_names.add(normalized_name)
            accepted.append(path)

        if unsupported:
            QMessageBox.warning(
                self,
                "Unsupported WTD Files",
                "These files are not recognized radio-logo containers:\n"
                + "\n".join(unsupported)
                + "\n\nAccepted names:\n"
                + "\n".join(sorted(KNOWN_RADIO_LOGO_WTD_NAMES)),
            )
        if duplicates:
            QMessageBox.information(
                self,
                "Duplicate WTD Files",
                "These filenames are already selected:\n" + "\n".join(duplicates),
            )

        for path in accepted:
            item = QListWidgetItem(os.path.basename(path), self.files_list)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)

        self._update_controls()

    def remove_selected_pack_files(self):
        for item in self.files_list.selectedItems():
            self.files_list.takeItem(self.files_list.row(item))
        self._update_controls()

    def clear_pack_files(self):
        self.files_list.clear()
        self._update_controls()

    def install_pack(self):
        target = self._selected_target()
        source_files = self._selected_pack_paths()
        if target is None or not source_files:
            return

        destination = get_radio_logo_destination_dir(
            self.gtaiv_path,
            target,
            use_direct=self.use_direct,
        )
        filenames = "\n".join(f"- {os.path.basename(path)}" for path in source_files)
        answer = QMessageBox.question(
            self,
            "Confirm Prepared Pack Installation",
            f"Target: {self.target_combo.currentText()}\n"
            f"Destination: {destination}\n\n"
            f"Files:\n{filenames}\n\n"
            "Install this prepared pack?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        worker = PreparedPackInstallWorker(
            self.gtaiv_path,
            source_files,
            target,
            self.use_direct,
        )
        self._start_worker(worker, self._on_pack_install_completed)

    def _on_pack_install_completed(self, installed):
        lines = []
        for item in installed:
            lines.append(f"Installed: {item.destination_path}")
            if item.backup_path:
                lines.append(f"Backup: {item.backup_path}")

        QMessageBox.information(
            self,
            "Prepared Radio Logo Pack Installed",
            "\n\n".join(lines),
        )
        self.files_list.clear()

    def _start_worker(self, worker, completed_slot):
        if self.worker is not None:
            return

        self.worker = worker
        worker.completed.connect(completed_slot)
        worker.error.connect(self._on_install_error)
        worker.finished.connect(
            lambda current_worker=worker: self._on_worker_finished(current_worker)
        )
        self._set_busy(True)
        worker.start()

    def _on_install_error(self, message):
        QMessageBox.critical(
            self,
            "Radio Logo Installation Error",
            message,
            QMessageBox.StandardButton.Ok,
        )

    def _on_worker_finished(self, worker):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()
        self._set_busy(False)

    def _set_busy(self, busy):
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(0)

        self.tabs.setEnabled(not busy)
        self.target_combo.setEnabled(
            not busy and self.target_combo.currentData() is not None
        )
        self.back_button.setEnabled(not busy)
        self._update_controls()

    def _update_controls(self, *_):
        busy = self.worker is not None
        target_available = self._selected_target() is not None
        station_available = self._selected_station() is not None

        self.station_combo.setEnabled(not busy and target_available)
        self.select_image_button.setEnabled(not busy and station_available)
        self.fit_combo.setEnabled(not busy and station_available)
        self.padding_spin.setEnabled(not busy and station_available)
        self.install_image_button.setEnabled(
            not busy
            and target_available
            and station_available
            and bool(self.source_image)
            and self.current_plan is not None
            and self.preview_ready
        )

        self.files_list.setEnabled(not busy)
        self.select_pack_button.setEnabled(not busy and target_available)
        self.remove_pack_button.setEnabled(
            not busy and bool(self.files_list.selectedItems())
        )
        self.clear_pack_button.setEnabled(
            not busy and self.files_list.count() > 0
        )
        self.install_pack_button.setEnabled(
            not busy
            and target_available
            and self.files_list.count() > 0
        )
