from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QMessageBox
from PySide6.QtCore import Qt
from core.audio_input import AUDIO_FILE_FILTER, validate_replacement_audio
from ui.styles import BUTTON_STYLE, LINE_EDIT_STYLE
from ui.path_dialogs import PathHistoryKey, remember_directory, select_open_file


class ReplacePage(QWidget):
    def __init__(self, selected_song, on_replace, on_back):
        super().__init__()
        self.selected_song = selected_song
        self.on_replace = on_replace
        self.on_back = on_back

        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(f"Replace {self.selected_song}", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FFC107;")
        self.layout.addWidget(title)

        self.new_song_input = QLineEdit(self)
        self.new_song_input.setPlaceholderText("Select the new song file...")
        self.new_song_input.setFixedWidth(400)
        self.new_song_input.setStyleSheet(LINE_EDIT_STYLE)
        self.layout.addWidget(self.new_song_input, alignment=Qt.AlignmentFlag.AlignCenter)

        browse_button = QPushButton("Browse", self)
        browse_button.clicked.connect(self.browse_file)
        browse_button.setFixedWidth(100)
        browse_button.setStyleSheet(BUTTON_STYLE)
        self.layout.addWidget(browse_button, alignment=Qt.AlignmentFlag.AlignCenter)

        buttons_layout = QHBoxLayout()

        back_button = QPushButton("Back", self)
        back_button.clicked.connect(self.on_back)
        back_button.setStyleSheet(BUTTON_STYLE)
        buttons_layout.addWidget(back_button)

        replace_button = QPushButton("Replace", self)
        replace_button.clicked.connect(self.replace)
        replace_button.setStyleSheet(BUTTON_STYLE)
        buttons_layout.addWidget(replace_button)

        self.layout.addLayout(buttons_layout)

    def browse_file(self):
        new_song_path = select_open_file(
            self,
            "Select New Song",
            PathHistoryKey.REPLACEMENT_AUDIO,
            file_filter=AUDIO_FILE_FILTER,
            fallback=self.new_song_input.text().strip(),
        )
        if new_song_path:
            self.new_song_input.setText(new_song_path)

    def replace(self):
        new_song_path = self.new_song_input.text().strip()
        try:
            replacement_audio = validate_replacement_audio(new_song_path)
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "Invalid File",
                str(exc),
                QMessageBox.StandardButton.Ok,
                QMessageBox.StandardButton.NoButton,
            )
            return

        replacement_path = str(replacement_audio)
        remember_directory(PathHistoryKey.REPLACEMENT_AUDIO, replacement_path)
        self.on_replace(replacement_path)
