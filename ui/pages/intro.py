from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
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

from core.app_preferences import (
    AppPreferences,
    ReplacementMode,
    load_preferences,
    save_preferences,
)
from core.game_installations import discover_gtaiv_installations, is_gtaiv_installation
from core.support_bundle import create_support_bundle as build_support_bundle
from replacement_strategy import check_fusionfix_installed
from ui.path_dialogs import (
    PathHistoryKey,
    get_remembered_directory,
    remember_directory,
    select_existing_directory,
    select_save_file,
)
from ui.styles import BUTTON_STYLE, GROUP_BOX_STYLE, LINE_EDIT_STYLE, RADIO_BUTTON_STYLE


class IntroPage(QWidget):
    def __init__(self, on_next, on_settings):
        super().__init__()
        self.on_next = on_next
        self.on_settings = on_settings

        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.path_input = QLineEdit(self)
        self.path_input.setPlaceholderText("Enter the GTA IV installation directory...")
        self.path_input.setFixedWidth(500)
        self.path_input.setStyleSheet(LINE_EDIT_STYLE)
        self.layout.addWidget(self.path_input, alignment=Qt.AlignmentFlag.AlignCenter)

        path_buttons = QHBoxLayout()
        self.browse_button = QPushButton("Browse", self)
        self.browse_button.clicked.connect(self.browse_directory)
        self.browse_button.setStyleSheet(BUTTON_STYLE)
        path_buttons.addWidget(self.browse_button)

        self.detect_button = QPushButton("Detect", self)
        self.detect_button.clicked.connect(self.detect_installations)
        self.detect_button.setStyleSheet(BUTTON_STYLE)
        path_buttons.addWidget(self.detect_button)
        self.layout.addLayout(path_buttons)

        self.path_status = QLabel(self)
        self.path_status.setFixedWidth(500)
        self.path_status.setWordWrap(True)
        self.path_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_status.setStyleSheet("color: #B0BEC5;")
        self.layout.addWidget(self.path_status, alignment=Qt.AlignmentFlag.AlignCenter)

        self.method_group = QGroupBox("Replacement Method", self)
        self.method_group.setStyleSheet(GROUP_BOX_STYLE)
        self.method_group.setFixedWidth(500)

        self.method_layout = QVBoxLayout(self.method_group)

        self.fusion_radio = QRadioButton("FusionFix (Recommended - Safe)", self.method_group)
        self.fusion_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        self.method_layout.addWidget(self.fusion_radio)

        self.direct_radio = QRadioButton("Direct Replacement (Not Recommended - Risky)", self.method_group)
        self.direct_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        self.method_layout.addWidget(self.direct_radio)

        self.layout.addWidget(self.method_group, alignment=Qt.AlignmentFlag.AlignCenter)

        utility_buttons = QHBoxLayout()
        self.settings_button = QPushButton("Settings & About", self)
        self.settings_button.clicked.connect(self.on_settings)
        self.settings_button.setStyleSheet(BUTTON_STYLE)
        utility_buttons.addWidget(self.settings_button)

        self.support_bundle_button = QPushButton("Create Support Bundle", self)
        self.support_bundle_button.clicked.connect(self.create_support_bundle)
        self.support_bundle_button.setStyleSheet(BUTTON_STYLE)
        utility_buttons.addWidget(self.support_bundle_button)
        self.layout.addLayout(utility_buttons)

        self.next_button = QPushButton("Next", self)
        self.next_button.clicked.connect(self.validate_and_proceed)
        self.next_button.setFixedWidth(100)
        self.next_button.setStyleSheet(BUTTON_STYLE)
        self.layout.addWidget(self.next_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self.reload_preferences(auto_detect=True)

    def reload_preferences(self, *, auto_detect: bool = False):
        preferences = load_preferences()
        self.direct_radio.setChecked(preferences.use_direct)
        self.fusion_radio.setChecked(not preferences.use_direct)

        remembered = get_remembered_directory(PathHistoryKey.GTA_IV_INSTALLATION)
        if remembered and is_gtaiv_installation(remembered):
            self.path_input.setText(remembered)
            self.path_status.setText("Using the saved GTA IV installation.")
            return

        self.path_input.clear()
        self.path_status.setText("No valid GTA IV installation is selected.")
        if auto_detect and preferences.auto_detect_installation:
            installations = discover_gtaiv_installations()
            if installations:
                self._apply_detected_installation(installations[0], remember=True)

    def _apply_detected_installation(self, installation, *, remember: bool):
        path = str(installation.path)
        self.path_input.setText(path)
        self.path_status.setText(f"Detected via {installation.source}.")
        if remember:
            remember_directory(PathHistoryKey.GTA_IV_INSTALLATION, path)

    def browse_directory(self):
        path = select_existing_directory(
            self,
            "Select GTA IV Directory",
            PathHistoryKey.GTA_IV_INSTALLATION,
            fallback=self.path_input.text().strip(),
        )
        if path:
            self.path_input.setText(path)
            if is_gtaiv_installation(path):
                self.path_status.setText("Selected GTA IV installation.")
            else:
                self.path_status.setText(
                    "The selected directory does not contain GTAIV.exe and pc/audio/sfx."
                )

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

        self._apply_detected_installation(selected, remember=True)

    def create_support_bundle(self):
        suggested_name = (
            "GTAIVModdingToolkit-support-"
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        )
        output_path = select_save_file(
            self,
            "Create Support Bundle",
            PathHistoryKey.SUPPORT_BUNDLE,
            file_filter="ZIP Archive (*.zip)",
            suggested_name=suggested_name,
        )
        if not output_path:
            return
        if not output_path.casefold().endswith(".zip"):
            output_path += ".zip"

        gtaiv_path = self.path_input.text().strip()
        if not is_gtaiv_installation(gtaiv_path):
            gtaiv_path = None

        try:
            result = build_support_bundle(
                output_path,
                gtaiv_path=gtaiv_path,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Support Bundle Error",
                f"Could not create the support bundle:\n\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            return

        QMessageBox.information(
            self,
            "Support Bundle Created",
            (
                f"Support bundle created at:\n{result.output_path}\n\n"
                "It contains redacted diagnostics and recent text logs, but no "
                "game archives, executables, audio, or replacement images. "
                "Review the ZIP before sharing it."
            ),
            QMessageBox.StandardButton.Ok,
        )

    def validate_and_proceed(self):
        gtaiv_path = self.path_input.text().strip()
        if not is_gtaiv_installation(gtaiv_path):
            QMessageBox.warning(
                self,
                "Invalid Path",
                "The selected directory must contain GTAIV.exe and pc/audio/sfx.",
                QMessageBox.StandardButton.Ok,
                QMessageBox.StandardButton.NoButton,
            )
            return

        remember_directory(PathHistoryKey.GTA_IV_INSTALLATION, gtaiv_path)
        previous_preferences = load_preferences()
        mode = (
            ReplacementMode.DIRECT
            if self.direct_radio.isChecked()
            else ReplacementMode.FUSIONFIX
        )
        save_preferences(
            AppPreferences(
                replacement_mode=mode,
                auto_detect_installation=previous_preferences.auto_detect_installation,
            )
        )
        use_direct = mode is ReplacementMode.DIRECT

        if not use_direct and not check_fusionfix_installed(gtaiv_path):
            msg = QMessageBox(self)
            msg.setWindowTitle("FusionFix Required")
            msg.setText(
                "FusionFix is required for the recommended safe replacement method.\n\n"
                "It was not found in your game directory.\n"
                "Would you like to visit the download page?"
            )
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            ret = msg.exec()

            if ret == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl("https://fusionfix.io/iv"))
            return

        self.on_next(gtaiv_path, use_direct)
