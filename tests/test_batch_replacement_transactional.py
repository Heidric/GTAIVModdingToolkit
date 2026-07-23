import base64
import json
import os
from pathlib import Path

import pytest

import batch_replacement
from batch_replacement import (
    BatchReplacementCancelled,
    replace_batch_transactional,
)


def _encode_archive(entries: dict[str, bytes]) -> bytes:
    payload = {
        name: base64.b64encode(value).decode("ascii")
        for name, value in entries.items()
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _read_archive(path: Path) -> dict[str, bytes]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: base64.b64decode(value.encode("ascii"))
        for name, value in payload.items()
    }


class FakeParser:
    def __init__(self, rpf_path: str, _exe_path: str):
        self.rpf_path = Path(rpf_path)

    def _entries(self) -> dict[str, bytes]:
        return _read_archive(self.rpf_path)

    def extract_file(self, full_path: str, output_directory: str) -> None:
        song_name = full_path.rsplit("/", 1)[-1]
        output = Path(output_directory) / song_name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(self._entries()[song_name])

    def add_file(self, source_path: str, full_path: str) -> None:
        song_name = full_path.rsplit("/", 1)[-1]
        entries = self._entries()
        entries[song_name] = Path(source_path).read_bytes()
        self.rpf_path.write_bytes(_encode_archive(entries))

    def get_file_capacity(self, _full_path: str) -> int:
        return 1024 * 1024


def _make_game(tmp_path: Path) -> tuple[Path, str, Path, Path]:
    root = tmp_path / "GTAIV"
    radio_file = "radio_test.rpf"
    original_rpf = root / "pc" / "audio" / "sfx" / radio_file
    original_dat15 = root / "pc" / "audio" / "config" / "sounds.dat15"
    original_rpf.parent.mkdir(parents=True)
    original_dat15.parent.mkdir(parents=True)
    (root / "GTAIV.exe").write_bytes(b"exe")
    original_rpf.write_bytes(
        _encode_archive(
            {
                "track_1.wav": b"old-one",
                "track_2.wav": b"old-two",
            }
        )
    )
    original_dat15.write_text("original durations\n", encoding="utf-8")
    return root, radio_file, original_rpf, original_dat15


def _make_audio_replacer():
    def replace(extracted_path: str, replacement_path: str) -> None:
        extracted = Path(extracted_path)
        replacement = Path(replacement_path)
        extracted.write_bytes(b"converted:" + replacement.read_bytes())
        Path(f"{extracted}.wav").write_bytes(b"processed wav")

    return replace


def _duration_updater(
    _root: str,
    _radio_name: str,
    song_name: str,
    duration_ms: int,
    *,
    dat15_path: str,
) -> None:
    path = Path(dat15_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"{song_name}={duration_ms}\n",
        encoding="utf-8",
    )


def _dependencies():
    return {
        "parser_factory": FakeParser,
        "audio_replacer": _make_audio_replacer(),
        "duration_updater": _duration_updater,
        "duration_reader": lambda _path: 4321,
    }


def test_fusionfix_batch_stages_verifies_and_commits_without_touching_originals(
    tmp_path,
    monkeypatch,
):
    root, radio_file, original_rpf, original_dat15 = _make_game(tmp_path)
    input_one = tmp_path / "one.mp3"
    input_two = tmp_path / "two.mp3"
    input_one.write_bytes(b"new-one")
    input_two.write_bytes(b"new-two")
    captured = []

    monkeypatch.setattr(
        batch_replacement,
        "capture_audio_state",
        lambda *args, **kwargs: captured.append((args, kwargs)) or object(),
    )
    monkeypatch.setattr(batch_replacement, "discard_audio_snapshot", lambda _snapshot: None)

    result = replace_batch_transactional(
        root,
        radio_file,
        (("track_1.wav", input_one), ("track_2.wav", input_two)),
        False,
        **_dependencies(),
    )

    target_rpf = root / "update" / "pc" / "audio" / "sfx" / radio_file
    target_dat15 = root / "update" / "pc" / "audio" / "config" / "sounds.dat15"
    assert result.replaced_count == 2
    assert result.rpf_path == target_rpf.resolve()
    assert result.dat15_path == target_dat15.resolve()
    assert _read_archive(target_rpf) == {
        "track_1.wav": b"converted:new-one",
        "track_2.wav": b"converted:new-two",
    }
    assert "track_1.wav=4321" in target_dat15.read_text(encoding="utf-8")
    assert "track_2.wav=4321" in target_dat15.read_text(encoding="utf-8")
    assert _read_archive(original_rpf)["track_1.wav"] == b"old-one"
    assert original_dat15.read_text(encoding="utf-8") == "original durations\n"
    assert len(captured) == 1
    assert captured[0][1]["reason"] == "batch replacement"
    assert not list(root.rglob(".gtaiv_toolkit_batch_*"))


def test_cancellation_after_verification_leaves_no_override_or_history(
    tmp_path,
    monkeypatch,
):
    root, radio_file, _, _ = _make_game(tmp_path)
    replacement = tmp_path / "replacement.mp3"
    replacement.write_bytes(b"new")
    progress = []
    captures = []

    monkeypatch.setattr(
        batch_replacement,
        "capture_audio_state",
        lambda *args, **kwargs: captures.append((args, kwargs)) or object(),
    )

    with pytest.raises(BatchReplacementCancelled):
        replace_batch_transactional(
            root,
            radio_file,
            (("track_1.wav", replacement),),
            False,
            progress_callback=progress.append,
            cancellation_callback=lambda: bool(progress) and progress[-1] >= 90,
            **_dependencies(),
        )

    assert captures == []
    assert not (root / "update" / "pc" / "audio" / "sfx" / radio_file).exists()
    assert not (root / "update" / "pc" / "audio" / "config" / "sounds.dat15").exists()
    assert not list(root.rglob(".gtaiv_toolkit_batch_*"))


def test_unsupported_audio_is_rejected_before_staging(tmp_path, monkeypatch):
    root, radio_file, _, _ = _make_game(tmp_path)
    replacement = tmp_path / "replacement.txt"
    replacement.write_bytes(b"not audio")
    captures = []
    monkeypatch.setattr(
        batch_replacement,
        "capture_audio_state",
        lambda *args, **kwargs: captures.append((args, kwargs)) or object(),
    )

    with pytest.raises(ValueError, match="Unsupported replacement audio format: TXT"):
        replace_batch_transactional(
            root,
            radio_file,
            (("track_1.wav", replacement),),
            False,
            **_dependencies(),
        )

    assert captures == []
    assert not list(root.rglob(".gtaiv_toolkit_batch_*"))


def test_duplicate_target_is_rejected_before_staging(tmp_path, monkeypatch):
    root, radio_file, _, _ = _make_game(tmp_path)
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    captures = []
    monkeypatch.setattr(
        batch_replacement,
        "capture_audio_state",
        lambda *args, **kwargs: captures.append((args, kwargs)) or object(),
    )

    with pytest.raises(ValueError, match="Duplicate target track"):
        replace_batch_transactional(
            root,
            radio_file,
            (("track_1.wav", first), ("TRACK_1.WAV", second)),
            False,
            **_dependencies(),
        )

    assert captures == []
    assert not list(root.rglob(".gtaiv_toolkit_batch_*"))


def test_failed_second_swap_restores_existing_fusionfix_pair_and_discards_history(
    tmp_path,
    monkeypatch,
):
    root, radio_file, original_rpf, original_dat15 = _make_game(tmp_path)
    target_rpf = root / "update" / "pc" / "audio" / "sfx" / radio_file
    target_dat15 = root / "update" / "pc" / "audio" / "config" / "sounds.dat15"
    target_rpf.parent.mkdir(parents=True)
    target_dat15.parent.mkdir(parents=True)
    target_rpf.write_bytes(original_rpf.read_bytes())
    target_dat15.write_bytes(original_dat15.read_bytes())
    old_rpf = target_rpf.read_bytes()
    old_dat15 = target_dat15.read_bytes()
    replacement = tmp_path / "replacement.mp3"
    replacement.write_bytes(b"new")
    snapshot = object()
    discarded = []

    monkeypatch.setattr(batch_replacement, "capture_audio_state", lambda *a, **k: snapshot)
    monkeypatch.setattr(
        batch_replacement,
        "discard_audio_snapshot",
        discarded.append,
    )
    calls = 0

    def fail_second_swap(source: str, destination: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second swap failure")
        os.replace(source, destination)

    with pytest.raises(OSError, match="second swap"):
        replace_batch_transactional(
            root,
            radio_file,
            (("track_1.wav", replacement),),
            False,
            replace_file=fail_second_swap,
            **_dependencies(),
        )

    assert target_rpf.read_bytes() == old_rpf
    assert target_dat15.read_bytes() == old_dat15
    assert discarded == [snapshot]
    assert not list(root.rglob(".gtaiv_toolkit_batch_*"))


def test_direct_backups_are_created_only_after_successful_preparation(
    tmp_path,
    monkeypatch,
):
    root, radio_file, original_rpf, original_dat15 = _make_game(tmp_path)
    replacement = tmp_path / "replacement.mp3"
    replacement.write_bytes(b"new")
    monkeypatch.setattr(batch_replacement, "capture_audio_state", lambda *a, **k: object())
    monkeypatch.setattr(batch_replacement, "discard_audio_snapshot", lambda _snapshot: None)

    def fail_conversion(_extracted: str, _replacement: str) -> None:
        raise RuntimeError("conversion failed")

    with pytest.raises(RuntimeError, match="conversion failed"):
        replace_batch_transactional(
            root,
            radio_file,
            (("track_1.wav", replacement),),
            True,
            parser_factory=FakeParser,
            audio_replacer=fail_conversion,
            duration_updater=_duration_updater,
            duration_reader=lambda _path: 4321,
        )

    assert list(original_rpf.parent.glob(f"{radio_file}.backup-*")) == []
    assert list(original_dat15.parent.glob("sounds.dat15.backup-*")) == []

    result = replace_batch_transactional(
        root,
        radio_file,
        (("track_1.wav", replacement),),
        True,
        **_dependencies(),
    )

    assert result.rpf_backup_path is not None
    assert result.dat15_backup_path is not None
    assert result.rpf_backup_path.is_file()
    assert result.dat15_backup_path.is_file()
    assert _read_archive(result.rpf_backup_path)["track_1.wav"] == b"old-one"
    assert result.dat15_backup_path.read_text(encoding="utf-8") == "original durations\n"
