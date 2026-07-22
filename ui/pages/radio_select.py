import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.radio_logo.ui_icons import (
    build_active_station_icon_cache,
    resolve_station_icon_path,
)
from ui.styles import BUTTON_STYLE, SCROLL_AREA_STYLE, TOOL_BUTTON_STYLE
from utils import resource_path


class RadioSelectPage(QWidget):
    def __init__(self, gtaiv_path, use_direct, on_next, on_back, on_install_logos):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.use_direct = use_direct
        self.on_next = on_next
        self.on_back = on_back
        self.on_install_logos = on_install_logos

        self.selected_radio = ""
        self.dynamic_icons = {}

        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Select a Radio", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FFC107;")
        self.layout.addWidget(title)

        self.radio_grid_widget = QWidget(self)
        self.radio_grid_layout = QGridLayout(self.radio_grid_widget)
        self.radio_grid_layout.setSpacing(20)
        self.radio_grid_layout.setContentsMargins(20, 20, 20, 20)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.radio_grid_widget)
        self.scroll_area.setStyleSheet(SCROLL_AREA_STYLE)
        self.layout.addWidget(self.scroll_area)

        buttons_layout = QHBoxLayout()

        self.back_button = QPushButton("Back", self)
        self.back_button.clicked.connect(self.on_back)
        self.back_button.setStyleSheet(BUTTON_STYLE)
        buttons_layout.addWidget(self.back_button)

        self.logo_pack_button = QPushButton("Radio Logo Tools", self)
        self.logo_pack_button.clicked.connect(self.on_install_logos)
        self.logo_pack_button.setStyleSheet(BUTTON_STYLE)
        buttons_layout.addWidget(self.logo_pack_button)

        self.next_button = QPushButton("Next", self)
        self.next_button.clicked.connect(self.proceed)
        self.next_button.setEnabled(False)
        self.next_button.setStyleSheet(BUTTON_STYLE)
        buttons_layout.addWidget(self.next_button)

        self.layout.addLayout(buttons_layout)

        self.radio_files = []
        self.radio_buttons = {}
        self.radio_button_group = QButtonGroup(self)
        self.radio_button_group.setExclusive(True)
        self.radio_button_group.buttonClicked.connect(self.radio_selected)

        self.load_radio_files()

    def load_radio_files(self):
        sfx_path = os.path.abspath(os.path.join(self.gtaiv_path, "pc/audio/sfx"))
        self.radio_files = sorted(
            (
                filename
                for filename in os.listdir(sfx_path)
                if filename.startswith("radio_") and filename.endswith(".rpf")
            ),
            key=str.casefold,
        )

        if not self.radio_files:
            QMessageBox.warning(
                self,
                "No Radios Found",
                "No radio files found in the specified directory.",
                QMessageBox.StandardButton.Ok,
                QMessageBox.StandardButton.NoButton,
            )
            return

        self._rebuild_dynamic_icons()
        button_size = QSize(180, 180)

        for index, radio_file in enumerate(self.radio_files):
            radio_name = radio_file[:-4]
            button = QToolButton(self)
            button.setText(radio_name.replace("radio_", "").upper())
            self._set_radio_icon(radio_name, button)

            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setFixedSize(button_size)
            button.setCheckable(True)
            button.setStyleSheet(TOOL_BUTTON_STYLE)

            self.radio_button_group.addButton(button)
            self.radio_buttons[radio_name] = button

            row, column = divmod(index, 3)
            self.radio_grid_layout.addWidget(button, row, column)

    def _rebuild_dynamic_icons(self):
        try:
            self.dynamic_icons = build_active_station_icon_cache(
                self.gtaiv_path,
                use_direct=self.use_direct,
            )
        except Exception as exc:
            print(f"Unable to refresh active radio-logo icons: {exc}")
            self.dynamic_icons = {}

    def _set_radio_icon(self, radio_name, button):
        icon_size = QSize(120, 120)
        dynamic_path = resolve_station_icon_path(radio_name, self.dynamic_icons)
        bundled_path = resource_path(
            os.path.join("assets", "radio", f"{radio_name}.png")
        )
        icon_path = str(dynamic_path) if dynamic_path is not None else bundled_path

        pixmap = QPixmap(icon_path)
        if pixmap.isNull():
            button.setIcon(QIcon(":/icons/radio_default.png"))
            return

        button.setIcon(
            QIcon(
                pixmap.scaled(
                    icon_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        )
        button.setIconSize(icon_size)

    def refresh_icons(self):
        """Reload active WTD textures after logo installation or recovery."""

        self._rebuild_dynamic_icons()
        for radio_name, button in self.radio_buttons.items():
            self._set_radio_icon(radio_name, button)

    def radio_selected(self, button):
        for radio_name, btn in self.radio_buttons.items():
            if btn == button:
                self.selected_radio = radio_name + ".rpf"
                break

        self.next_button.setEnabled(True)

    def proceed(self):
        if not self.selected_radio:
            QMessageBox.warning(
                self,
                "No Radio Selected",
                "Please select a radio to proceed.",
                QMessageBox.StandardButton.Ok,
                QMessageBox.StandardButton.NoButton,
            )
            return

        self.on_next(self.selected_radio)
