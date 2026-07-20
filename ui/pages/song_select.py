import os
import json
import tempfile

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QHBoxLayout, \
    QMessageBox
from PySide6.QtCore import Qt, Signal, QSize
from ui.styles import BUTTON_STYLE, SONG_LIST_STYLE
from core.rpf import RPFParser
import qtawesome as qta
from ui.preview_player import PreviewPlayer
from audio_utils import get_sounds_dat15_data, get_song_duration

class SongItemWidget(QWidget):
    preview_clicked = Signal(str)

    def __init__(self, song_name, duration_text, item, list_widget, parent=None):
        super().__init__(parent)
        self.song_name = song_name
        self.item = item
        self.list_widget = list_widget

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)

        self.label = QLabel(song_name)
        self.label.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")

        self.dur_label = QLabel(duration_text)
        self.dur_label.setStyleSheet("color: #B0BEC5; font-size: 12px;")

        self.play_btn = QPushButton()
        self.play_btn.setIcon(qta.icon('mdi.play', color='white'))
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setStyleSheet("background-color: transparent; border: none;")
        self.play_btn.clicked.connect(self.on_click)

        self.loading_label = QLabel("Loading...")
        self.loading_label.setStyleSheet("color: #FFC107; font-size: 10px;")
        self.loading_label.hide()

        layout.addWidget(self.label)
        layout.addStretch()
        layout.addWidget(self.dur_label)
        layout.addSpacing(15)
        layout.addWidget(self.loading_label)
        layout.addWidget(self.play_btn)

    def on_click(self):
        self.preview_clicked.emit(self.song_name)

    def mousePressEvent(self, event):
        self.list_widget.setCurrentItem(self.item)
        super().mousePressEvent(event)

    def set_playing(self, playing):
        self.loading_label.hide()
        self.play_btn.show()
        if playing:
            self.play_btn.setIcon(qta.icon('mdi.pause', color='#FFC107'))
        else:
            self.play_btn.setIcon(qta.icon('mdi.play', color='white'))

    def set_paused(self, paused):
        if paused:
            self.play_btn.setIcon(qta.icon('mdi.play', color='#FFC107'))

    def set_loading(self, loading):
        if loading:
            self.play_btn.hide()
            self.loading_label.show()
        else:
            self.loading_label.hide()
            self.play_btn.show()


class SongSelectPage(QWidget):
    def __init__(self, gtaiv_path, selected_radio, on_next, on_back, on_batch=None):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.selected_radio = selected_radio
        self.on_next = on_next
        self.on_back = on_back
        self.on_batch = on_batch

        self.player = PreviewPlayer()
        self.player.playback_started.connect(self.on_playback_started)
        self.player.playback_paused.connect(self.on_playback_paused)
        self.player.playback_stopped.connect(self.on_playback_stopped)
        self.player.extraction_started.connect(self.on_extraction_started)
        self.player.error_occurred.connect(self.on_preview_error)

        self.dat15_data = {}
        self.song_widgets = {}
        self.current_preview_song = None
        self.parser = None
        self.songs = []

        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Select a Song to Replace", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FFC107;")
        self.layout.addWidget(title)

        self.song_list = QListWidget(self)
        self.song_list.setStyleSheet(SONG_LIST_STYLE)
        self.layout.addWidget(self.song_list)

        buttons_layout = QHBoxLayout()

        self.back_button = QPushButton("Back", self)
        self.back_button.clicked.connect(self.on_back)
        self.back_button.setStyleSheet(BUTTON_STYLE)
        buttons_layout.addWidget(self.back_button)

        self.batch_button = QPushButton("Batch Replace", self)
        self.batch_button.clicked.connect(self.proceed_batch)
        self.batch_button.setStyleSheet(BUTTON_STYLE)
        self.batch_button.setEnabled(self.on_batch is not None)
        buttons_layout.addWidget(self.batch_button)

        self.next_button = QPushButton("Next", self)
        self.next_button.clicked.connect(self.proceed)
        self.next_button.setStyleSheet(BUTTON_STYLE)
        buttons_layout.addWidget(self.next_button)

        self.layout.addLayout(buttons_layout)

        self.load_songs()

    def load_songs(self):
        # Load sounds.dat15 data
        # Check update folder first
        dat15_update = os.path.join(self.gtaiv_path, "update", "pc", "audio", "config", "sounds.dat15")
        if os.path.exists(dat15_update):
            self.dat15_data = get_sounds_dat15_data(self.gtaiv_path, dat15_update)
        else:
            self.dat15_data = get_sounds_dat15_data(self.gtaiv_path)

        # Check update folder for RPF
        rpf_rel_path = f"pc/audio/sfx/{self.selected_radio}"
        update_rpf_path = os.path.join(self.gtaiv_path, "update", rpf_rel_path)
        orig_rpf_path = os.path.join(self.gtaiv_path, rpf_rel_path)

        if os.path.exists(update_rpf_path):
            rpf_path = os.path.abspath(update_rpf_path)
        else:
            rpf_path = os.path.abspath(orig_rpf_path)

        self.parser = RPFParser(rpf_path, os.path.abspath(os.path.join(self.gtaiv_path, "GTAIV.exe")))
        temp_json_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="gtaiv_radio_", suffix=".json", delete=False) as temp_json:
                temp_json_path = temp_json.name

            self.parser.save_json(temp_json_path)

            with open(temp_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        finally:
            if temp_json_path and os.path.exists(temp_json_path):
                os.remove(temp_json_path)

        songs = []
        for directory in data.get("directories", []):
            if directory["name"].upper() == self.selected_radio[:-4].upper():
                for file in directory["files"]:
                    if not file["name"].startswith("ID_") and not file["name"].startswith("SOLO_"):
                        songs.append(file["name"])

        if not songs:
            QMessageBox.warning(self, "No Songs Found", "No songs found in the selected radio.",
                                QMessageBox.StandardButton.Ok,
                                QMessageBox.StandardButton.NoButton)
            return

        self.songs = list(songs)

        radio_name = self.selected_radio[:-4].upper()
        for song in songs:
            item = QListWidgetItem(self.song_list)
            item.setSizeHint(QSize(0, 50))

            duration_ms = get_song_duration(self.dat15_data, radio_name, song)
            duration_text = self.format_duration(duration_ms)

            widget = SongItemWidget(song, duration_text, item, self.song_list)
            widget.preview_clicked.connect(self.play_preview)

            self.song_list.setItemWidget(item, widget)
            self.song_widgets[song] = widget

            item.setData(Qt.UserRole, song)

    def format_duration(self, ms):
        if ms <= 0:
            return "--:--"
        seconds = (ms // 1000) % 60
        minutes = (ms // 60000)
        return f"{minutes}:{seconds:02}"

    def play_preview(self, song_name):
        radio_name = self.selected_radio[:-4].upper()
        duration = get_song_duration(self.dat15_data, radio_name, song_name)

        self.current_preview_song = song_name
        self.player.preview_song(self.gtaiv_path, self.selected_radio, song_name, duration, self.parser)

    def on_playback_started(self, song_name):
        if song_name in self.song_widgets:
            self.song_widgets[song_name].set_loading(False)
            self.song_widgets[song_name].set_playing(True)

    def on_playback_paused(self, song_name):
        if song_name in self.song_widgets:
            self.song_widgets[song_name].set_paused(True)

    def on_playback_stopped(self, song_name):
        if song_name in self.song_widgets:
            self.song_widgets[song_name].set_loading(False)
            self.song_widgets[song_name].set_playing(False)

        if self.current_preview_song == song_name:
            self.current_preview_song = None

    def on_extraction_started(self, song_name):
        if song_name in self.song_widgets:
            self.song_widgets[song_name].set_loading(True)

    def on_preview_error(self, msg):
        QMessageBox.warning(self, "Preview Error", f"Failed to play preview: {msg}")

    def proceed_batch(self):
        self.player.stop_playback()
        if self.on_batch is not None:
            self.on_batch(list(self.songs))

    def proceed(self):
        self.player.stop_playback()

        selected_item = self.song_list.currentItem()
        if not selected_item:
            QMessageBox.warning(self, "No Song Selected", "Please select a song to replace.",
                                QMessageBox.StandardButton.Ok,
                                QMessageBox.StandardButton.NoButton)
            return

        self.on_next(selected_item.data(Qt.UserRole))
