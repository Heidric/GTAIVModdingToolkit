"""Architecture checks for the batch replacement worker boundary."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _import_roots(relative_path: str) -> set[str]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_batch_backend_does_not_import_qt():
    assert "PySide6" not in _import_roots("batch_replacement.py")


def test_batch_worker_lives_in_ui_package():
    backend_source = (ROOT / "batch_replacement.py").read_text(encoding="utf-8")
    worker_source = (
        ROOT / "ui/workers/batch_replacement.py"
    ).read_text(encoding="utf-8")

    assert "class BatchReplaceWorker" not in backend_source
    assert "class BatchReplaceWorker" in worker_source
