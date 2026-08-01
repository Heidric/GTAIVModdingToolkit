"""Architecture checks for embedded WDR texture browser support."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_browser_accepts_wtd_and_wdr_texture_resources():
    source = (ROOT / "ui/pages/rpf_browser.py").read_text(encoding="utf-8")

    assert 'resource_suffix not in {".wtd", ".wdr"}' in source
    assert 'f"{request_id}-{digest}{resource_suffix}"' in source
    assert '"Select a .wtd or .wdr entry to inspect its textures."' in source
    assert '"PNG Image (*.png);;DDS Texture (*.dds);;All Files (*)"' in source
    assert 'def _safe_texture_filename(name: str, suffix: str = ".png")' in source


def test_workers_route_wdr_operations_and_png_exports():
    source = (ROOT / "ui/workers/rpf_browser.py").read_text(encoding="utf-8")

    assert 'Path(self.entry_path).suffix.casefold() == ".wdr"' in source
    assert 'Path(self.wtd_path).suffix.casefold() == ".wdr"' in source
    assert (
        'destination_suffix = Path(self.destination_path).suffix.casefold()'
        in source
    )
    assert 'elif destination_suffix == ".png":' in source
    assert "inspect_wdr_archive" in source
    assert "render_wdr_texture_preview" in source
    assert "export_wdr_texture" in source
    assert "export_wtd_texture_png" in source
    assert "replace_rpf_wdr_texture_from_image_transactional" in source
    assert "replace_rpf_wtd_texture_from_image_transactional" in source


def test_wtd_backend_exposes_full_resolution_png_export():
    source = (ROOT / "core/wtd_archive.py").read_text(encoding="utf-8")

    assert "def export_wtd_texture_png" in source
    assert 'image.save(output, format="PNG")' in source
