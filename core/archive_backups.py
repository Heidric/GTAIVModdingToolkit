"""Retention policy for timestamped GTA IV archive backups."""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_ROLLING_BACKUP_LIMIT = 3
MIN_ROLLING_BACKUP_LIMIT = 1
MAX_ROLLING_BACKUP_LIMIT = 20


def validate_rolling_backup_limit(value: object) -> int:
    """Return a bounded integer rolling-backup limit."""
    if isinstance(value, bool):
        raise TypeError("rolling backup limit must be an integer")
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError("rolling backup limit must be an integer") from exc
    if not MIN_ROLLING_BACKUP_LIMIT <= limit <= MAX_ROLLING_BACKUP_LIMIT:
        raise ValueError(
            "rolling backup limit must be between "
            f"{MIN_ROLLING_BACKUP_LIMIT} and {MAX_ROLLING_BACKUP_LIMIT}"
        )
    return limit


def configured_rolling_backup_limit() -> int:
    """Load the configured limit without importing Qt at module import time."""
    from core.app_preferences import load_preferences

    return validate_rolling_backup_limit(load_preferences().rolling_backup_limit)


def _backup_pattern(archive_path: Path) -> re.Pattern[str]:
    return re.compile(
        rf"^{re.escape(archive_path.stem)}\.backup-"
        rf"(?P<timestamp>\d{{8}}-\d{{6}})(?:-(?P<counter>\d+))?{re.escape(archive_path.suffix)}$",
        re.IGNORECASE,
    )


def list_archive_backups(archive_path: str | Path) -> tuple[Path, ...]:
    """Return timestamped backups from oldest to newest."""
    archive = Path(archive_path).expanduser().resolve()
    pattern = _backup_pattern(archive)
    candidates: list[tuple[tuple[str, int], Path]] = []
    for candidate in archive.parent.iterdir():
        if not candidate.is_file():
            continue
        match = pattern.fullmatch(candidate.name)
        if match is None:
            continue
        counter = int(match.group("counter") or 0)
        candidates.append(((match.group("timestamp"), counter), candidate))
    return tuple(path for _key, path in sorted(candidates, key=lambda item: item[0]))


def prune_archive_backups(
    archive_path: str | Path,
    rolling_limit: int | None = None,
) -> tuple[Path, ...]:
    """Keep the oldest backup forever and only the newest N later backups.

    Deletion failures are deliberately non-fatal: an archive transaction must not be
    reported as failed after its commit merely because an old backup could not be
    removed.
    """
    limit = (
        configured_rolling_backup_limit()
        if rolling_limit is None
        else validate_rolling_backup_limit(rolling_limit)
    )
    backups = list_archive_backups(archive_path)
    if len(backups) <= limit + 1:
        return ()

    permanent = backups[0]
    recent = set(backups[-limit:])
    deleted: list[Path] = []
    for candidate in backups[1:]:
        if candidate == permanent or candidate in recent:
            continue
        try:
            candidate.unlink()
        except OSError:
            continue
        deleted.append(candidate)
    return tuple(deleted)
