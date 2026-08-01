"""Tests for archive-entry name filtering and browser wiring."""

from dataclasses import dataclass
from pathlib import Path

from ui.archive_entry_filter import (
    filter_archive_entries_by_name,
    normalize_archive_name_filter,
)


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Entry:
    path: str


def test_normalize_archive_name_filter_trims_and_casefolds():
    assert normalize_archive_name_filter(None) is None
    assert normalize_archive_name_filter("") is None
    assert normalize_archive_name_filter("  UnderPass  ") == "underpass"
    assert normalize_archive_name_filter("*.WdR") == "*.wdr"


def test_filter_archive_entries_matches_filename_substrings_case_insensitively():
    entries = (
        Entry("maps/east/og_underpass247.wdr"),
        Entry("textures/bs3lod03.wtd"),
        Entry("textures/OTHER.WTD"),
    )

    assert filter_archive_entries_by_name(entries, None) == entries
    assert filter_archive_entries_by_name(entries, "underPASS") == (entries[0],)
    assert filter_archive_entries_by_name(entries, ".wtd") == (
        entries[1],
        entries[2],
    )
    assert filter_archive_entries_by_name(entries, "lod03") == (entries[1],)


def test_name_filter_does_not_match_parent_directory_only():
    entries = (
        Entry("underpass/first.wtd"),
        Entry("maps/underpass_shop.wdr"),
    )

    assert filter_archive_entries_by_name(entries, "underpass") == (entries[1],)


def test_browser_filters_loaded_snapshot_without_starting_an_inspection_worker():
    source = (ROOT / "ui/pages/rpf_browser.py").read_text(encoding="utf-8")

    assert "entry_name_filter" in source
    assert "_on_entry_name_filter_changed" in source
    assert "filter_archive_entries_by_name" in source
    handler = source.split("def _on_entry_name_filter_changed", 1)[1].split(
        "\n    def ", 1
    )[0]
    assert "RPFInspectWorker" not in handler
