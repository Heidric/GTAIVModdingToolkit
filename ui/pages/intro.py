import os
from datetime import datetime

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QPushButton, QMessageBox, QGroupBox, QRadioButton
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

from core.support_bundle import create_support_bundle as build_support_bundle
from ui.styles import BUTTON_STYLE, LINE_EDIT_STYLE, RADIO_BUTTON_STYLE, GROUP_BOX_STYLE
from replacement_strategy import check_fusionfix_installed
from ui.path_dialogs import (
    PathHistoryKey,
    get_remembered_directory,
    remember_directory,
    select_existing_directory,
    select_save_file,
)


class IntroPage(QWidget):
    def __init__(self, on_next):
        super().__init__()
        self.on_next = on_next

        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.path_input = QLineEdit(self)
        self.path_input.setPlaceholderText("Enter the GTA IV installation directory...")
        self.path_input.setFixedWidth(400)
        self.path_input.setStyleSheet(LINE_EDIT_STYLE)
        self.path_input.setText(get_remembered_directory(PathHistoryKey.GTA_IV_INSTALLATION))
        self.layout.addWidget(self.path_input, alignment=Qt.AlignmentFlag.AlignCenter)

        self.browse_button = QPushButton("Browse", self)
        self.browse_button.clicked.connect(self.browse_directory)
        self.browse_button.setFixedWidth(100)
        self.browse_button.setStyleSheet(BUTTON_STYLE)
        self.layout.addWidget(self.browse_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self.method_group = QGroupBox("Replacement Method", self)
        self.method_group.setStyleSheet(GROUP_BOX_STYLE)
        self.method_group.setFixedWidth(400)

        self.method_layout = QVBoxLayout(self.method_group)

        self.fusion_radio = QRadioButton("FusionFix (Recommended - Safe)", self.method_group)
        self.fusion_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        self.fusion_radio.setChecked(True)
        self.method_layout.addWidget(self.fusion_radio)

        self.direct_radio = QRadioButton("Direct Replacement (Not Recommended - Risky)", self.method_group)
        self.direct_radio.setStyleSheet(RADIO_BUTTON_STYLE)
        self.method_layout.addWidget(self.direct_radio)

        self.layout.addWidget(self.method_group, alignment=Qt.AlignmentFlag.AlignCenter)

        self.support_bundle_button = QPushButton("Create Support Bundle", self)
        self.support_bundle_button.clicked.connect(self.create_support_bundle)
        self.support_bundle_button.setFixedWidth(180)
        self.support_bundle_button.setStyleSheet(BUTTON_STYLE)
        self.layout.addWidget(self.support_bundle_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self.next_button = QPushButton("Next", self)
        self.next_button.clicked.connect(self.validate_and_proceed)
        self.next_button.setFixedWidth(100)
        self.next_button.setStyleSheet(BUTTON_STYLE)
        self.layout.addWidget(self.next_button, alignment=Qt.AlignmentFlag.AlignCenter)

    def browse_directory(self):
        path = select_existing_directory(
            self,
            "Select GTA IV Directory",
            PathHistoryKey.GTA_IV_INSTALLATION,
            fallback=self.path_input.text().strip(),
        )
        if path:
            self.path_input.setText(path)

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
        if not os.path.isdir(gtaiv_path):
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
        if not os.path.isdir(gtaiv_path) or not os.path.exists(os.path.join(gtaiv_path, "pc/audio/sfx")):
            QMessageBox.warning(self, "Invalid Path", "Invalid GTA IV directory. Please try again.",
                                QMessageBox.StandardButton.Ok,
                                QMessageBox.StandardButton.NoButton)
            return

        remember_directory(PathHistoryKey.GTA_IV_INSTALLATION, gtaiv_path)
        use_direct = self.direct_radio.isChecked()

        if not use_direct:
            if not check_fusionfix_installed(gtaiv_path):
                msg = QMessageBox(self)
                msg.setWindowTitle("FusionFix Required")
                msg.setText("FusionFix is required for the recommended safe replacement method.\n\n"
                            "It was not found in your game directory.\n"
                            "Would you like to visit the download page?")
                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                ret = msg.exec()

                if ret == QMessageBox.Yes:
                    QDesktopServices.openUrl(QUrl("https://fusionfix.io/iv"))

                return

        self.on_next(gtaiv_path, use_direct)
