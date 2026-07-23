import importlib.util
import json
import types
from pathlib import Path

import pytest


def _load_audio_utils():
    module_path = Path(__file__).resolve().parents[1] / "audio_utils.py"
    spec = importlib.util.spec_from_file_location(
        "audio_utils_hardening_target",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audio_utils = _load_audio_utils()


def test_audio_utils_uses_qt_independent_runtime_helpers():
    module_path = Path(__file__).resolve().parents[1] / "audio_utils.py"
    source = module_path.read_text(encoding="utf-8")

    assert "from core.runtime_tools import" in source
    assert "from utils import" not in source


def _write_dat15_json(output_dir, payload):
    json_path = Path(output_dir) / "sounds.dat15.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    return str(json_path)


def test_update_song_duration_rejects_missing_metadata_entry(tmp_path, monkeypatch):
    dat15_path = tmp_path / "sounds.dat15"
    dat15_path.write_bytes(b"original")

    monkeypatch.setattr(
        audio_utils,
        "convert_dat15_to_json",
        lambda _source, output_dir: _write_dat15_json(output_dir, {}),
    )
    converted = []
    monkeypatch.setattr(
        audio_utils,
        "convert_json_to_dat15",
        lambda *args: converted.append(args),
    )

    with pytest.raises(KeyError, match="RADIO_TEST_TRACK_01"):
        audio_utils.update_song_duration(
            None,
            "radio_test",
            "track_01",
            1234,
            dat15_path=str(dat15_path),
        )

    assert converted == []
    assert not Path(f"{dat15_path}_backup").exists()


def test_update_song_duration_fails_closed_on_verification_mismatch(
    tmp_path,
    monkeypatch,
):
    dat15_path = tmp_path / "sounds.dat15"
    dat15_path.write_bytes(b"original")
    payload = {
        "RADIO_TEST_TRACK_01": {
            "Metadata": {"__field00": 1000},
        }
    }

    monkeypatch.setattr(
        audio_utils,
        "convert_dat15_to_json",
        lambda _source, output_dir: _write_dat15_json(output_dir, payload),
    )
    monkeypatch.setattr(audio_utils, "convert_json_to_dat15", lambda *_args: None)
    monkeypatch.setattr(
        audio_utils,
        "get_sounds_dat15_data",
        lambda *_args: {
            "RADIO_TEST_TRACK_01": {
                "Metadata": {"__field00": 999},
            }
        },
    )

    with pytest.raises(RuntimeError, match="expected 1234, got 999"):
        audio_utils.update_song_duration(
            None,
            "radio_test",
            "track_01",
            1234,
            dat15_path=str(dat15_path),
        )

    assert Path(f"{dat15_path}_backup").read_bytes() == b"original"


def test_process_audio_rejects_silent_input_before_ffmpeg(monkeypatch):
    silent_audio = types.SimpleNamespace(dBFS=float("-inf"))
    monkeypatch.setattr(audio_utils, "check_ffmpeg", lambda: True)
    monkeypatch.setattr(
        audio_utils.AudioSegment,
        "from_file",
        lambda _path: silent_audio,
    )
    monkeypatch.setattr(
        audio_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("FFmpeg must not run for silence"),
    )

    with pytest.raises(ValueError, match="silent"):
        audio_utils.process_audio("track", "silent.ogg")


def test_replace_special_audio_surfaces_diagnostic_directory(tmp_path, monkeypatch):
    original_audio = tmp_path / "track.oaf"
    original_audio.write_bytes(b"original")
    diagnostic_dir = tmp_path / "diagnostics"
    diagnostic_dir.mkdir()

    monkeypatch.setattr(
        audio_utils,
        "get_ivaudioconv_path",
        lambda: (_ for _ in ()).throw(ValueError("converter failed")),
    )
    monkeypatch.setattr(
        audio_utils.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(diagnostic_dir),
    )

    with pytest.raises(RuntimeError) as error:
        audio_utils.replace_special_audio(str(original_audio), "replacement.ogg")

    message = str(error.value)
    assert "converter failed" in message
    assert str(diagnostic_dir) in message
    assert (diagnostic_dir / original_audio.name).read_bytes() == b"original"
