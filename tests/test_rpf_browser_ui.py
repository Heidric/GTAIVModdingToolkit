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
    assert "core.rpf_wtd" not in page_modules
    assert "core.rpf_archive" in worker_modules
    assert "core.wtd_archive" in worker_modules
    assert "core.rpf_wtd" in worker_modules


def test_browser_page_does_not_define_qthreads():
    assert "QThread" not in _class_names("ui/pages/rpf_browser.py")
    worker_classes = _class_names("ui/workers/rpf_browser.py")
    assert {
        "RPFInspectWorker",
        "RPFWTDInspectWorker",
        "RPFEntryExportWorker",
        "WTDTexturePreviewWorker",
        "WTDTextureExportWorker",
        "RPFWTDTextureReplaceWorker",
    } <= worker_classes


def test_main_window_routes_to_rpf_browser_page():
    source = (ROOT / "ui/main_window.py").read_text(encoding="utf-8")
    assert "RPFBrowserPage" in source
    assert "goto_rpf_browser" in source
    assert "on_rpf_browser=self.goto_rpf_browser" in source


def test_browser_exposes_transactional_texture_replacement_controls():
    page_source = (ROOT / "ui/pages/rpf_browser.py").read_text(encoding="utf-8")
    worker_source = (ROOT / "ui/workers/rpf_browser.py").read_text(encoding="utf-8")
    dialogs_source = (ROOT / "ui/path_dialogs.py").read_text(encoding="utf-8")

    assert "replace_selected_texture" in page_source
    assert "Confirm Texture Replacement" in page_source
    assert "backup_path" in page_source
    assert "replace_rpf_wtd_texture_from_image_transactional" not in page_source
    assert "replace_rpf_wtd_texture_from_image_transactional" in worker_source
    assert "WTD_TEXTURE_REPLACEMENT" in dialogs_source


def _load_rpf_browser_workers(monkeypatch, replacement_function):
    import importlib.util
    import sys
    import types

    class BoundSignal:
        def __init__(self):
            self.emitted = []

        def emit(self, *args):
            self.emitted.append(args)

        def connect(self, _callback):
            pass

    class SignalDescriptor:
        def __init__(self, *_types):
            self.name = None

        def __set_name__(self, _owner, name):
            self.name = name

        def __get__(self, instance, _owner):
            if instance is None:
                return self
            return instance.__dict__.setdefault(self.name, BoundSignal())

    class QThread:
        def __init__(self):
            pass

    qt_core = types.ModuleType("PySide6.QtCore")
    qt_core.QThread = QThread
    qt_core.Signal = SignalDescriptor
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qt_core

    rpf_archive = types.ModuleType("core.rpf_archive")
    rpf_archive.export_rpf_entry = lambda *args, **kwargs: None
    rpf_archive.inspect_rpf_archive = lambda *args, **kwargs: None

    rpf_wtd = types.ModuleType("core.rpf_wtd")
    rpf_wtd.replace_rpf_wtd_texture_from_image_transactional = replacement_function

    wtd_archive = types.ModuleType("core.wtd_archive")
    wtd_archive.export_wtd_texture = lambda *args, **kwargs: None
    wtd_archive.inspect_wtd_archive = lambda *args, **kwargs: None
    wtd_archive.render_wtd_texture_preview = lambda *args, **kwargs: None

    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qt_core)
    monkeypatch.setitem(sys.modules, "core.rpf_archive", rpf_archive)
    monkeypatch.setitem(sys.modules, "core.rpf_wtd", rpf_wtd)
    monkeypatch.setitem(sys.modules, "core.wtd_archive", wtd_archive)

    module_path = ROOT / "ui/workers/rpf_browser.py"
    spec = importlib.util.spec_from_file_location(
        "rpf_browser_workers_test_target",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_texture_replacement_worker_forwards_transaction_arguments(monkeypatch):
    calls = []
    expected_result = object()

    def replace(*args, **kwargs):
        calls.append((args, kwargs))
        return expected_result

    workers = _load_rpf_browser_workers(monkeypatch, replace)
    worker = workers.RPFWTDTextureReplaceWorker(
        "archive.rpf",
        "GTAIV.exe",
        "textures/hud.wtd",
        7,
        "replacement.png",
        quality=0.75,
    )

    worker.run()

    assert calls == [
        (
            (
                "archive.rpf",
                "GTAIV.exe",
                "textures/hud.wtd",
                7,
                "replacement.png",
            ),
            {"quality": 0.75},
        )
    ]
    assert worker.completed.emitted == [(expected_result,)]
    assert worker.error.emitted == []


def test_texture_replacement_worker_reports_backend_error(monkeypatch):
    def replace(*_args, **_kwargs):
        raise RuntimeError("replacement failed")

    workers = _load_rpf_browser_workers(monkeypatch, replace)
    worker = workers.RPFWTDTextureReplaceWorker(
        "archive.rpf",
        "GTAIV.exe",
        "textures/hud.wtd",
        7,
        "replacement.png",
    )

    worker.run()

    assert worker.completed.emitted == []
    assert worker.error.emitted == [("replacement failed",)]
