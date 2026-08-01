"""Pure filtering helpers for GTA IV archive entries."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Protocol


class ArchiveEntryLike(Protocol):
    path: str


def normalize_archive_name_filter(value: str | None) -> str | None:
    """Return a lowercase substring query, or ``None`` for no filter."""
    if value is None:
        return None
    normalized = value.strip().casefold()
    return normalized or None


def filter_archive_entries_by_name(
    entries: tuple[ArchiveEntryLike, ...] | list[ArchiveEntryLike],
    query: str | None,
) -> tuple[ArchiveEntryLike, ...]:
    """Filter by case-insensitive filename substring, preserving source order."""
    normalized = normalize_archive_name_filter(query)
    if normalized is None:
        return tuple(entries)
    return tuple(
        entry
        for entry in entries
        if normalized in PurePosixPath(entry.path).name.casefold()
    )


# Compatibility helpers retained for callers that still need suffix-only filtering.
def normalize_archive_extension(extension: str | None) -> str | None:
    """Return a lowercase dotted suffix, or ``None`` for no filter."""
    if extension is None:
        return None

    normalized = extension.strip().casefold()
    if normalized.startswith("*"):
        normalized = normalized[1:].strip()
    if normalized in {"", "."}:
        return None
    if "/" in normalized or "\\" in normalized:
        raise ValueError("extension filter must not contain path separators")
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    return normalized


def filter_archive_entries_by_extension(
    entries: tuple[ArchiveEntryLike, ...] | list[ArchiveEntryLike],
    extension: str | None,
) -> tuple[ArchiveEntryLike, ...]:
    """Filter entries by final suffix while preserving source order."""
    normalized = normalize_archive_extension(extension)
    if normalized is None:
        return tuple(entries)
    return tuple(
        entry
        for entry in entries
        if PurePosixPath(entry.path).suffix.casefold() == normalized
    )
