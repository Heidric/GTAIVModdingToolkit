import os
from pathlib import Path

import pytest

from core.audio_history import (
    capture_audio_state,
    discard_audio_snapshot,
    latest_audio_snapshot,
    list_audio_snapshots,
    resolve_audio_target_paths,
    restore_latest_audio_snapshot,
)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_state(root: Path, station: str, use_direct: bool, rpf: bytes, dat15: bytes):
    rpf_path, dat15_path = resolve_audio_target_paths(root, station, use_direct)
    atomic_write(rpf_path, rpf)
    atomic_write(dat15_path, dat15)
    return rpf_path, dat15_path


def test_direct_recovery_is_reversible(tmp_path):
    station = "radio_vladivostok.rpf"
    rpf_path, dat15_path = write_state(tmp_path, station, True, b"rpf-a", b"dat-a")
    capture_audio_state(tmp_path, station, True, reason="single-track replacement")
    atomic_write(rpf_path, b"rpf-b")
    atomic_write(dat15_path, b"dat-b")

    first = restore_latest_audio_snapshot(tmp_path, True)

    assert rpf_path.read_bytes() == b"rpf-a"
    assert dat15_path.read_bytes() == b"dat-a"
    assert first.restored_snapshot.station_file == station
    assert latest_audio_snapshot(tmp_path, True).reason == "recovery-displaced-state"

    restore_latest_audio_snapshot(tmp_path, True)

    assert rpf_path.read_bytes() == b"rpf-b"
    assert dat15_path.read_bytes() == b"dat-b"


def test_fusionfix_first_override_recovery_removes_both_files(tmp_path):
    station = "radio_broker.rpf"
    snapshot = capture_audio_state(tmp_path, station, False)
    assert snapshot.rpf_was_present is False
    assert snapshot.dat15_was_present is False

    rpf_path, dat15_path = write_state(tmp_path, station, False, b"override", b"duration")
    restore_latest_audio_snapshot(tmp_path, False)

    assert not rpf_path.exists()
    assert not dat15_path.exists()


def test_latest_snapshot_is_global_within_replacement_mode(tmp_path):
    write_state(tmp_path, "radio_a.rpf", True, b"a", b"dat-a")
    capture_audio_state(tmp_path, "radio_a.rpf", True)
    write_state(tmp_path, "radio_b.rpf", True, b"b", b"dat-b")
    capture_audio_state(tmp_path, "radio_b.rpf", True)

    latest = latest_audio_snapshot(tmp_path, True)

    assert latest is not None
    assert latest.station_file == "radio_b.rpf"
    assert len(list_audio_snapshots(tmp_path, True)) == 2
    assert list_audio_snapshots(tmp_path, False) == ()


def test_recovery_rolls_back_when_second_swap_fails(tmp_path):
    station = "radio_liberty.rpf"
    rpf_path, dat15_path = write_state(tmp_path, station, True, b"old-rpf", b"old-dat")
    capture_audio_state(tmp_path, station, True)
    atomic_write(rpf_path, b"new-rpf")
    atomic_write(dat15_path, b"new-dat")
    calls = 0

    def fail_second_swap(source: str, destination: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second swap failure")
        os.replace(source, destination)

    with pytest.raises(OSError, match="simulated second swap failure"):
        restore_latest_audio_snapshot(
            tmp_path,
            True,
            replace_file=fail_second_swap,
        )

    assert rpf_path.read_bytes() == b"new-rpf"
    assert dat15_path.read_bytes() == b"new-dat"
    assert len(list_audio_snapshots(tmp_path, True)) == 1


def test_discard_audio_snapshot_removes_history_entry(tmp_path):
    station = "radio_jazz.rpf"
    write_state(tmp_path, station, True, b"rpf", b"dat")
    snapshot = capture_audio_state(tmp_path, station, True)

    discard_audio_snapshot(snapshot)

    assert not snapshot.directory.exists()
    assert latest_audio_snapshot(tmp_path, True) is None
