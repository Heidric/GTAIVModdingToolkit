import os
import shutil
from pathlib import Path

import pytest

from single_replacement import (
    SingleReplacementCancelled,
    replace_single_track_transactional,
)


RADIO_FILE = "radio_vladivostok.rpf"
SONG_FILE = "track_01.oaf"


class FakeRPFParser:
    fail_add = False

    def __init__(self, rpf_path, _exe_path):
        self.rpf_path = Path(rpf_path)

    def extract_file(self, full_song_path, output_directory):
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        song_name = full_song_path.rsplit("/", 1)[-1]
        (output / song_name).write_bytes(self.rpf_path.read_bytes())

    def add_file(self, source_path, _full_song_path):
        if self.fail_add:
            raise RuntimeError("synthetic add failure")
        self.rpf_path.write_bytes(Path(source_path).read_bytes())


def make_game(tmp_path: Path):
    root = tmp_path / "GTAIV"
    rpf = root / "pc" / "audio" / "sfx" / RADIO_FILE
    dat15 = root / "pc" / "audio" / "config" / "sounds.dat15"
    rpf.parent.mkdir(parents=True)
    dat15.parent.mkdir(parents=True)
    (root / "GTAIV.exe").write_bytes(b"exe")
    rpf.write_bytes(b"original-track")
    dat15.write_bytes(b"original-dat15")
    replacement = tmp_path / "replacement.mp3"
    replacement.write_bytes(b"replacement-track")
    return root, rpf, dat15, replacement


def fake_audio_replacer(extracted_path, replacement_path):
    shutil.copy2(replacement_path, extracted_path)
    Path(f"{extracted_path}.wav").write_bytes(b"processed-wav")


def fake_duration_updater(
    _gtaiv_path,
    radio_name,
    song_name,
    duration_ms,
    *,
    dat15_path,
):
    path = Path(dat15_path)
    path.write_bytes(
        path.read_bytes()
        + f"|{radio_name}|{song_name}|{duration_ms}".encode("ascii")
    )


def transaction_kwargs():
    return {
        "parser_factory": FakeRPFParser,
        "audio_replacer": fake_audio_replacer,
        "duration_updater": fake_duration_updater,
        "duration_reader": lambda _path: 4321,
    }


def assert_no_transaction_files(root: Path):
    leftovers = [
        path
        for path in root.rglob("*")
        if path.name.startswith(".gtaiv_toolkit_single_")
    ]
    assert leftovers == []


def test_direct_single_replacement_stages_verifies_and_creates_backups(tmp_path):
    root, rpf, dat15, replacement = make_game(tmp_path)
    progress = []

    result = replace_single_track_transactional(
        root,
        RADIO_FILE,
        SONG_FILE,
        replacement,
        True,
        progress_callback=progress.append,
        **transaction_kwargs(),
    )

    assert rpf.read_bytes() == b"replacement-track"
    assert b"|RADIO_VLADIVOSTOK|track_01.oaf|4321" in dat15.read_bytes()
    assert result.rpf_backup_path.read_bytes() == b"original-track"
    assert result.dat15_backup_path.read_bytes() == b"original-dat15"
    assert progress == [5, 20, 45, 70, 85, 100]
    assert_no_transaction_files(root)


def test_fusionfix_first_install_keeps_originals_unchanged(tmp_path):
    root, original_rpf, original_dat15, replacement = make_game(tmp_path)

    result = replace_single_track_transactional(
        root,
        RADIO_FILE,
        SONG_FILE,
        replacement,
        False,
        **transaction_kwargs(),
    )

    assert original_rpf.read_bytes() == b"original-track"
    assert original_dat15.read_bytes() == b"original-dat15"
    assert result.rpf_path.read_bytes() == b"replacement-track"
    assert b"|RADIO_VLADIVOSTOK|track_01.oaf|4321" in result.dat15_path.read_bytes()
    assert result.rpf_backup_path is None
    assert result.dat15_backup_path is None
    assert_no_transaction_files(root)


def test_failure_before_commit_leaves_active_files_unchanged(tmp_path):
    root, rpf, dat15, replacement = make_game(tmp_path)

    class FailingParser(FakeRPFParser):
        fail_add = True

    kwargs = transaction_kwargs()
    kwargs["parser_factory"] = FailingParser

    with pytest.raises(RuntimeError, match="synthetic add failure"):
        replace_single_track_transactional(
            root,
            RADIO_FILE,
            SONG_FILE,
            replacement,
            True,
            **kwargs,
        )

    assert rpf.read_bytes() == b"original-track"
    assert dat15.read_bytes() == b"original-dat15"
    assert list(root.rglob("*.backup-*")) == []
    assert_no_transaction_files(root)


def test_commit_failure_restores_both_direct_targets(tmp_path):
    root, rpf, dat15, replacement = make_game(tmp_path)

    def fail_dat15_commit(source_path, target_path):
        if Path(target_path) == dat15:
            raise OSError("synthetic commit failure")
        os.replace(source_path, target_path)

    with pytest.raises(OSError, match="synthetic commit failure"):
        replace_single_track_transactional(
            root,
            RADIO_FILE,
            SONG_FILE,
            replacement,
            True,
            replace_file=fail_dat15_commit,
            **transaction_kwargs(),
        )

    assert rpf.read_bytes() == b"original-track"
    assert dat15.read_bytes() == b"original-dat15"
    assert_no_transaction_files(root)


def test_cancellation_before_staging_does_not_change_files(tmp_path):
    root, rpf, dat15, replacement = make_game(tmp_path)

    with pytest.raises(SingleReplacementCancelled):
        replace_single_track_transactional(
            root,
            RADIO_FILE,
            SONG_FILE,
            replacement,
            True,
            cancellation_callback=lambda: True,
            **transaction_kwargs(),
        )

    assert rpf.read_bytes() == b"original-track"
    assert dat15.read_bytes() == b"original-dat15"
    assert_no_transaction_files(root)
