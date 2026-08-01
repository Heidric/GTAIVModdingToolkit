from pathlib import Path

import pytest

from core.archive_backups import (
    DEFAULT_ROLLING_BACKUP_LIMIT,
    list_archive_backups,
    prune_archive_backups,
    validate_rolling_backup_limit,
)


def _backup(archive: Path, stamp: str, payload: bytes) -> Path:
    path = archive.with_name(f"{archive.stem}.backup-{stamp}{archive.suffix}")
    path.write_bytes(payload)
    return path


def test_default_retains_three_rolling_backups_beyond_permanent_original():
    assert DEFAULT_ROLLING_BACKUP_LIMIT == 3


def test_pruning_keeps_oldest_forever_and_newest_rolling_backups(tmp_path):
    archive = tmp_path / "brook_s3.img"
    archive.write_bytes(b"current")
    backups = [
        _backup(archive, f"20260730-01000{index}", bytes([index]))
        for index in range(6)
    ]

    deleted = prune_archive_backups(archive, 2)

    assert set(deleted) == set(backups[1:4])
    assert list_archive_backups(archive) == (backups[0], backups[4], backups[5])


@pytest.mark.parametrize("value", (0, 21, True, "invalid"))
def test_backup_limit_validation_rejects_unsafe_values(value):
    with pytest.raises((TypeError, ValueError)):
        validate_rolling_backup_limit(value)
