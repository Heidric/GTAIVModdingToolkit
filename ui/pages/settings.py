from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from build_info import build_summary
from core.app_logging import application_log_directory
from core.app_preferences import (
    AppPreferences,
    ReplacementMode,
    load_preferences,
    save_preferences,
)
from core.game_installations import discover_gtaiv_installations, is_gtaiv_installation
from ui.path_dialogs import (
    PathHistoryKey,
    forget_remembered_directory,
    get_remembered_directory,
    remember_directory,
    select_existing_directory,
)
from ui.styles import BUTTON_STYLE, GROUP_BOX_STYLE, LINE_EDIT_STYLE, RADIO_BUTTON_STYLE
from ui.system_check_dialog import SystemCheckDialog


class SettingsPage(QWidget):
    def __init__(self, on_back, on_saved):
        super().__init__()
        self.on_back = on_back
        self.on_saved = on_saved

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Settings & About", self)
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: white;")
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)

        installation_group = QGroupBox("GTA IV Installation", self)
        installation_group.setStyleSheet(GROUP_BOX_STYLE)
        installation_group.setFixedWidth(620)
        installation_layout = QVBoxLayout(installation_group)

        self.path_input = QLineEdit(self)
        self.path_input.setPlaceholderText("GTA IV installation directory")
        self.path_input.setStyleSheet(LINE_EDIT_STYLE)
        installation_layout.addWidget(self.path_input)

        path_buttons = QHBoxLayout()
        self.browse_button = QPushButton("Browse", self)
        self.browse_button.setStyleSheet(BUTTON_STYLE)
        self.browse_button.clicked.connect(self.browse_directory)
        path_buttons.addWidget(self.browse_button)

        self.detect_button = QPushButton("Detect Installations", self)
        self.detect_button.setStyleSheet(BUTTON_STYLE)
        self.detect_button.clicked.connect(self.detect_installations)
        path_buttons.addWidget(self.detect_button)
        installation_layout.addLayout(path_buttons)

        self.auto_detect_checkbox = QCheckBox(
            "Detect GTA IV automatically when no saved installation is available",
            self,
        )
        installation_layout.addWidget(self.auto_detect_checkbox)
        layout.addWidget(installation_group, alignment=Qt.AlignmentFlag.AlignCenter)

        method_group = QGroupBox("Default Replacement Method", self)
        method_group.setStyleSheet(GROUP_BOX_STYLE)
        method_group.setFixedWidth(620)
        method_layout = QVBoxLayout(method_group)

        self.fusion_radio = QRadioButton("FusionFix (Recommended - Safe)", method_group)
        self.fusion_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        method_layout.addWidget(self.fusion_radio)

        self.direct_radio = QRadioButton(
            "Direct Replacement (Not Recommended - Risky)",
            method_group,
        )
        self.direct_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        method_layout.addWidget(self.direct_radio)
        layout.addWidget(method_group, alignment=Qt.AlignmentFlag.AlignCenter)

        about_group = QGroupBox("Build Information", self)
        about_group.setStyleSheet(GROUP_BOX_STYLE)
        about_group.setFixedWidth(620)
        about_layout = QVBoxLayout(about_group)

        build_label = QLabel(build_summary(), self)
        build_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        build_label.setStyleSheet("color: white;")
        about_layout.addWidget(build_label)

        self.log_directory_label = QLabel(str(application_log_directory()), self)
        self.log_directory_label.setWordWrap(True)
        self.log_directory_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.log_directory_label.setStyleSheet("color: #B0BEC5;")
        about_layout.addWidget(self.log_directory_label)

        system_check_button = QPushButton("Run System Check", self)
        system_check_button.setStyleSheet(BUTTON_STYLE)
        system_check_button.clicked.connect(self.open_system_check)
        about_layout.addWidget(system_check_button)

        open_logs_button = QPushButton("Open Logs Folder", self)
        open_logs_button.setStyleSheet(BUTTON_STYLE)
        open_logs_button.clicked.connect(self.open_logs_folder)
        about_layout.addWidget(open_logs_button)
        layout.addWidget(about_group, alignment=Qt.AlignmentFlag.AlignCenter)

        action_buttons = QHBoxLayout()
        self.back_button = QPushButton("Back", self)
        self.back_button.setStyleSheet(BUTTON_STYLE)
        self.back_button.clicked.connect(self.on_back)
        action_buttons.addWidget(self.back_button)

        self.save_button = QPushButton("Save", self)
        self.save_button.setStyleSheet(BUTTON_STYLE)
        self.save_button.clicked.connect(self.save)
        action_buttons.addWidget(self.save_button)
        layout.addLayout(action_buttons)

        self.reload()

    def reload(self):
        preferences = load_preferences()
        self.path_input.setText(
            get_remembered_directory(PathHistoryKey.GTA_IV_INSTALLATION)
        )
        self.auto_detect_checkbox.setChecked(preferences.auto_detect_installation)
        self.direct_radio.setChecked(preferences.use_direct)
        self.fusion_radio.setChecked(not preferences.use_direct)

    def browse_directory(self):
        selected = select_existing_directory(
            self,
            "Select GTA IV Directory",
            PathHistoryKey.GTA_IV_INSTALLATION,
            fallback=self.path_input.text().strip(),
        )
        if selected:
            self.path_input.setText(selected)

    def detect_installations(self):
        current = self.path_input.text().strip()
        additional = (current,) if current else ()
        installations = discover_gtaiv_installations(
            additional_candidates=additional,
        )
        if not installations:
            QMessageBox.information(
                self,
                "GTA IV Not Found",
                "No valid GTA IV installation was detected. Select the directory manually.",
                QMessageBox.StandardButton.Ok,
            )
            return

        selected = installations[0]
        if len(installations) > 1:
            labels = [installation.display_name for installation in installations]
            label, accepted = QInputDialog.getItem(
                self,
                "Select GTA IV Installation",
                "Detected installations:",
                labels,
                0,
                False,
            )
            if not accepted:
                return
            selected = installations[labels.index(label)]

        self.path_input.setText(str(selected.path))

    def open_system_check(self):
        dialog = SystemCheckDialog(
            gtaiv_path=self.path_input.text().strip() or None,
            use_direct=self.direct_radio.isChecked(),
            parent=self,
        )
        dialog.exec()

    def open_logs_folder(self):
        directory = application_log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def save(self):
        path = self.path_input.text().strip()
        if path and not is_gtaiv_installation(path):
            QMessageBox.warning(
                self,
                "Invalid GTA IV Directory",
                "The selected directory must contain GTAIV.exe and pc/audio/sfx.",
                QMessageBox.StandardButton.Ok,
            )
            return

        if path:
            remember_directory(PathHistoryKey.GTA_IV_INSTALLATION, path)
        else:
            forget_remembered_directory(PathHistoryKey.GTA_IV_INSTALLATION)

        mode = (
            ReplacementMode.DIRECT
            if self.direct_radio.isChecked()
            else ReplacementMode.FUSIONFIX
        )
        save_preferences(
            AppPreferences(
                replacement_mode=mode,
                auto_detect_installation=self.auto_detect_checkbox.isChecked(),
            )
        )
        self.on_saved()
