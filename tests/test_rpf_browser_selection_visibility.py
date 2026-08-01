"""Regression test for focus-independent archive selection visibility."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_archive_selection_has_explicit_active_and_inactive_styles():
    source = (ROOT / "ui/pages/rpf_browser.py").read_text(encoding="utf-8")

    assert "QTreeWidget::item:selected:active" in source
    assert "QTreeWidget::item:selected:!active" in source
    assert source.count("background-color: #FFC107;") >= 2
    assert source.count("color: #000000;") >= 2
