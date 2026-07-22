import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.installation_lock import (
    installation_lock,
    installation_lock_path,
    locked_installation_operation,
)


def make_game(tmp_path):
    root = tmp_path / "GTAIV"
    root.mkdir()
    return root


def test_second_process_is_rejected_with_owner_details(tmp_path):
    root = make_game(tmp_path)
    script = """
import sys
from pathlib import Path
from core.installation_lock import InstallationBusyError, installation_lock

try:
    with installation_lock(sys.argv[1], operation="child audio recovery"):
        pass
except InstallationBusyError as exc:
    print(exc)
    raise SystemExit(17)
raise SystemExit(0)
"""

    with installation_lock(root, operation="single-track replacement"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 17
    assert "single-track replacement" in result.stdout


def test_lock_is_reusable_after_release(tmp_path):
    root = make_game(tmp_path)

    with installation_lock(root, operation="first"):
        pass
    with installation_lock(root, operation="second") as owner:
        assert owner.operation == "second"


def test_lock_file_contains_owner_metadata(tmp_path):
    root = make_game(tmp_path)

    with installation_lock(root, operation="batch replacement") as owner:
        lock_path = installation_lock_path(root)
        with lock_path.open("rb") as stream:
            stream.seek(1)
            metadata = json.loads(stream.read().decode("utf-8"))
        assert metadata["pid"] == owner.pid
        assert metadata["operation"] == "batch replacement"

    with lock_path.open("rb") as stream:
        assert stream.read(1) == b"\0"


def test_decorator_serializes_existing_root_and_preserves_result(tmp_path):
    root = make_game(tmp_path)

    @locked_installation_operation("decorated operation")
    def operation(gtaiv_path, value):
        return gtaiv_path, value

    returned_path, returned_value = operation(str(root), 42)

    assert returned_path == str(root)
    assert returned_value == 42


def test_decorator_preserves_original_missing_path_validation(tmp_path):
    missing_root = tmp_path / "missing"

    @locked_installation_operation("decorated operation")
    def operation(gtaiv_path):
        raise RuntimeError(f"original validation: {gtaiv_path}")

    with pytest.raises(RuntimeError, match="original validation"):
        operation(missing_root)


def test_invalid_scope_is_rejected(tmp_path):
    root = make_game(tmp_path)

    with pytest.raises(ValueError, match="lock scope"):
        installation_lock_path(root, scope="../audio")
