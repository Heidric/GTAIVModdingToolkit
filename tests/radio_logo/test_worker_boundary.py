"""Architecture checks for the radio-logo worker boundary."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKER_CLASSES = {
    "StationLogoInstallWorker",
    "PreparedPackInstallWorker",
    "RadioLogoRecoveryWorker",
}


def _tree(relative_path: str) -> ast.AST:
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


def _class_names(relative_path: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree(relative_path))
        if isinstance(node, ast.ClassDef)
    }


def _imported_names(relative_path: str, module_name: str) -> set[str]:
    names = set()
    for node in ast.walk(_tree(relative_path)):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            names.update(alias.name for alias in node.names)
    return names


def test_radio_logo_workers_live_in_ui_worker_module():
    page_classes = _class_names("ui/pages/radio_logo_install.py")
    worker_classes = _class_names("ui/workers/radio_logo.py")

    assert WORKER_CLASSES.isdisjoint(page_classes)
    assert WORKER_CLASSES <= worker_classes


def test_radio_logo_page_imports_workers_without_threading_primitives():
    page_workers = _imported_names(
        "ui/pages/radio_logo_install.py",
        "ui.workers.radio_logo",
    )
    qt_core_names = _imported_names(
        "ui/pages/radio_logo_install.py",
        "PySide6.QtCore",
    )

    assert WORKER_CLASSES <= page_workers
    assert {"QThread", "Signal"}.isdisjoint(qt_core_names)


def test_radio_logo_backend_operations_are_owned_by_worker_module():
    page_installer_names = _imported_names(
        "ui/pages/radio_logo_install.py",
        "core.radio_logo.installer",
    )
    page_workflow_names = _imported_names(
        "ui/pages/radio_logo_install.py",
        "core.radio_logo.workflow",
    )
    worker_installer_names = _imported_names(
        "ui/workers/radio_logo.py",
        "core.radio_logo.installer",
    )
    worker_workflow_names = _imported_names(
        "ui/workers/radio_logo.py",
        "core.radio_logo.workflow",
    )

    assert "install_radio_logo_pack" not in page_installer_names
    assert "restore_previous_radio_logo_pack" not in page_installer_names
    assert "install_station_logo_from_image" not in page_workflow_names
    assert {
        "install_radio_logo_pack",
        "restore_previous_radio_logo_pack",
    } <= worker_installer_names
    assert "install_station_logo_from_image" in worker_workflow_names
