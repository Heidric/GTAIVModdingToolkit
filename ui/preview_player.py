import os
import shutil
import subprocess
import tempfile
import atexit
from PySide6.QtCore import QObject, QTimer, QUrl, Signal, QThread
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from core.rpf import RPFParser
from utils import resource_path
from audio_utils import get_ivaudioconv_path

class AudioExtractor(QThread):
    finished = Signal(str) 
    error = Signal(str)

    def __init__(self, gtaiv_path, selected_radio, selected_song, output_dir, parser=None):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.selected_radio = selected_radio
        self.selected_song = selected_song
        self.output_dir = output_dir
        self.parser = parser

    def run(self):
        try:
            cache_filename = f"{self.selected_radio}_{self.selected_song}.wav"
            final_wav = os.path.join(self.output_dir, cache_filename)
            
            if os.path.exists(final_wav):
                self.finished.emit(final_wav)
                return

            if self.parser:
                parser = self.parser
            else:
                rpf_rel_path = f"pc/audio/sfx/{self.selected_radio}"
                update_rpf_path = os.path.join(self.gtaiv_path, "update", rpf_rel_path)
                orig_rpf_path = os.path.join(self.gtaiv_path, rpf_rel_path)
                
                if os.path.exists(update_rpf_path):
                    rpf_path = update_rpf_path
                elif os.path.exists(orig_rpf_path):
                    rpf_path = orig_rpf_path
                else:
                    raise FileNotFoundError(f"RPF not found: {rpf_rel_path}")
                    
                parser = RPFParser(rpf_path, os.path.join(self.gtaiv_path, "GTAIV.exe"))
            
            radio_name = self.selected_radio[:-4].upper()
            full_song_path = f"{radio_name}/{self.selected_song}"
            
            parser.extract_file(full_song_path, self.output_dir)
            
            extracted_ivaud = os.path.join(self.output_dir, self.selected_song)
            
            iv_audio_conv_path = get_ivaudioconv_path()
            
            subprocess.run([iv_audio_conv_path, extracted_ivaud], check=True, capture_output=True)
            wav_file = f"{extracted_ivaud}.wav"
            
            if not os.path.exists(wav_file):
                raise FileNotFoundError("Failed to convert audio.")
                
            shutil.move(wav_file, final_wav)
            if os.path.exists(extracted_ivaud):
                os.remove(extracted_ivaud)
                
            self.finished.emit(final_wav)
            
        except Exception as e:
            self.error.emit(str(e))

class PreviewPlayer(QObject):
    playback_started = Signal(str) # song_key
    playback_paused = Signal(str) # song_key
    playback_stopped = Signal(str) # song_key
    extraction_started = Signal(str) # song_key
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.stop_playback)
        
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        self.current_extractor = None
        
        self.temp_dir = tempfile.mkdtemp()
        atexit.register(self.cleanup)
        
        self.current_song_key = None
        self.remaining_ms = 0

    def preview_song(self, gtaiv_path, selected_radio, selected_song, duration_ms, parser=None):
        key = selected_song 
        
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and self.current_song_key == key:
            self.pause_playback()
            return
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState and self.current_song_key == key:
            self.resume_playback()
            return
            
        self.stop_playback()
        self.current_song_key = key
        self.remaining_ms = duration_ms
        
        self.extraction_started.emit(key)
        
        self.current_extractor = AudioExtractor(gtaiv_path, selected_radio, selected_song, self.temp_dir, parser)
        self.current_extractor.finished.connect(lambda wav: self.start_playback(wav, duration_ms))
        self.current_extractor.error.connect(self.on_error)
        self.current_extractor.start()

    def start_playback(self, wav_path, duration_ms):
        self.player.setSource(QUrl.fromLocalFile(wav_path))
        self.audio_output.setVolume(1.0)
        self.player.play()
        if self.current_song_key:
            self.playback_started.emit(self.current_song_key)
        
        if duration_ms > 0:
            self.timer.start(duration_ms)

    def pause_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.remaining_ms = self.timer.remainingTime()
            self.timer.stop()
            self.player.pause()
            if self.current_song_key:
                self.playback_paused.emit(self.current_song_key)

    def resume_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
            if self.remaining_ms > 0:
                self.timer.start(self.remaining_ms)
            if self.current_song_key:
                self.playback_started.emit(self.current_song_key)

    def stop_playback(self):
        old_key = self.current_song_key
        self.player.stop()
        self.timer.stop()
        self.current_song_key = None
        self.current_extractor = None
        if old_key:
            self.playback_stopped.emit(old_key)

    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop_playback()

    def on_error(self, msg):
        self.error_occurred.emit(msg)
        self.stop_playback()

    def cleanup(self):
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except:
                pass
