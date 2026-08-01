from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_browser_exposes_queue_review_and_batch_worker():
    page = (ROOT / "ui/pages/rpf_browser.py").read_text(encoding="utf-8")
    workers = (ROOT / "ui/workers/rpf_browser.py").read_text(encoding="utf-8")

    assert "Add selected replacement to queue" in page
    assert "Review/apply queue" in page
    assert "TextureReplacementQueueDialog" in page
    assert "RPFTextureBatchReplaceWorker" in page
    assert "class RPFTextureBatchReplaceWorker" in workers
    assert "replace_archive_textures_transactional" in workers


def test_settings_exposes_backup_retention_control():
    source = (ROOT / "ui/pages/settings.py").read_text(encoding="utf-8")
    assert "rolling_backup_spin" in source
    assert "oldest backup is always kept" in source
