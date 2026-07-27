"""Architecture and pure-model tests for the generic RPF browser UI."""

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from ui.rpf_browser_model import build_rpf_browser_tree


ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    offset: int



def _tree(relative_path: str) -> ast.AST:
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


def _imported_modules(relative_path: str) -> set[str]:
    modules = set()
    for node in ast.walk(_tree(relative_path)):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _class_names(relative_path: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree(relative_path))
        if isinstance(node, ast.ClassDef)
    }


def test_browser_tree_builds_nested_directories_and_sorts_directories_first():
    entries = (
        Entry("textures/zeta.wtd", 30, 300),
        Entry("audio/radio.rpf", 20, 200),
        Entry("textures/maps/city.wtd", 10, 100),
        Entry("root.bin", 5, 50),
    )

    tree = build_rpf_browser_tree(entries)

    assert [node.name for node in tree] == ["audio", "textures", "root.bin"]
    textures = tree[1]
    assert textures.is_directory
    assert [node.name for node in textures.children] == ["maps", "zeta.wtd"]
    assert textures.children[0].children[0].entry.path == "textures/maps/city.wtd"


def test_browser_tree_rejects_file_directory_collisions():
    entries = (
        Entry("textures", 1, 0),
        Entry("textures/hud.wtd", 2, 1),
    )

    with pytest.raises(ValueError, match="both a file and directory"):
        build_rpf_browser_tree(entries)


def test_browser_page_keeps_backend_operations_in_worker_module():
    page_modules = _imported_modules("ui/pages/rpf_browser.py")
    worker_modules = _imported_modules("ui/workers/rpf_browser.py")

    assert "core.rpf_archive" not in page_modules
    assert "core.wtd_archive" not in page_modules
    assert "core.rpf_archive" in worker_modules
    assert "core.wtd_archive" in worker_modules


def test_browser_page_does_not_define_qthreads():
    assert "QThread" not in _class_names("ui/pages/rpf_browser.py")
    worker_classes = _class_names("ui/workers/rpf_browser.py")
    assert {
        "RPFInspectWorker",
        "RPFWTDInspectWorker",
        "RPFEntryExportWorker",
        "WTDTexturePreviewWorker",
        "WTDTextureExportWorker",
    } <= worker_classes


def test_main_window_routes_to_rpf_browser_page():
    source = (ROOT / "ui/main_window.py").read_text(encoding="utf-8")
    assert "RPFBrowserPage" in source
    assert "goto_rpf_browser" in source
    assert "on_rpf_browser=self.goto_rpf_browser" in source
