from pathlib import Path

import pytest

from core.archive_texture_batch import TextureReplacementRequest
from ui.texture_replacement_queue import (
    QueuedTextureReplacement,
    TextureReplacementQueue,
    TextureReplacementQueueDialog,
)


def _item(tmp_path: Path, entry: str, index: int, name: str):
    image = tmp_path / f"{name}.png"
    image.write_bytes(b"png")
    return QueuedTextureReplacement(
        request=TextureReplacementRequest(entry, index, name, image),
        resource_kind=Path(entry).suffix.lstrip(".").upper(),
        dimensions="512 × 512",
        format_name="DXT1",
        target_png_data=b"preview",
    )


def test_queue_replaces_duplicate_target_without_reordering(tmp_path):
    archive = tmp_path / "brook_s3.img"
    first = _item(tmp_path, "shop.wdr", 1, "front")
    second = _item(tmp_path, "lod.wtd", 2, "lod")
    replacement = _item(tmp_path, "shop.wdr", 1, "front-new")
    queue = TextureReplacementQueue()

    assert queue.add(archive, first) is False
    assert queue.add(archive, second) is False
    assert queue.add(archive, replacement) is True

    assert [item.request.texture_name for item in queue.items] == ["front-new", "lod"]
    assert queue.entry_count() == 2


def test_queue_is_scoped_to_one_archive(tmp_path):
    queue = TextureReplacementQueue()
    queue.add(tmp_path / "one.img", _item(tmp_path, "shop.wdr", 1, "front"))

    with pytest.raises(ValueError, match="different archive"):
        queue.add(tmp_path / "two.img", _item(tmp_path, "lod.wtd", 2, "lod"))


def test_dialog_selected_item_uses_visible_row_order(tmp_path):
    first = _item(tmp_path, "shop.wdr", 1, "front")
    second = _item(tmp_path, "lod.wtd", 2, "lod")
    queue = TextureReplacementQueue()
    queue.add(tmp_path / "brook_s3.img", first)
    queue.add(tmp_path / "brook_s3.img", second)

    class Table:
        @staticmethod
        def currentRow():
            return 1

    dialog = type("DialogState", (), {"table": Table(), "queue": queue})()

    assert TextureReplacementQueueDialog._selected_item(dialog) is second


def test_dialog_defers_initial_preview_until_after_construction():
    source = (
        Path(__file__).resolve().parents[1] / "ui/texture_replacement_queue.py"
    ).read_text(encoding="utf-8")

    refresh = source.split("    def refresh", 1)[1].split("\n    def ", 1)[0]
    assert "self.table.blockSignals(True)" in refresh
    assert "if enabled and self.isVisible():" in refresh
    assert "def showEvent" in source
    assert "QImage.fromData(payload)" in source


def test_browser_reports_queue_dialog_construction_errors():
    source = (
        Path(__file__).resolve().parents[1] / "ui/pages/rpf_browser.py"
    ).read_text(encoding="utf-8")
    handler = source.split("    def show_replacement_queue", 1)[1].split(
        "\n    def ", 1
    )[0]

    assert "except Exception as exc" in handler
    assert "Replacement Queue Error" in handler
