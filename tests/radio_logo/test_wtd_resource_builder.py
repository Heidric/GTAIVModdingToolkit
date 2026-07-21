from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from core.radio_logo.resource_builder import (
    FFTDC_ENVIRONMENT_VARIABLE,
    FftdcNotFoundError,
    WtdBuildError,
    build_wtd,
    make_fftdc_build_command,
    resolve_fftdc_command,
    validate_wtd_source_directory,
)


def _texture_dir(tmp_path: Path) -> Path:
    source = tmp_path / "radio_hud.wtd"
    source.mkdir()
    (source / "radio_vladivostok.png").write_bytes(b"png-data")
    return source


def _fake_builder(tmp_path: Path, body: str) -> tuple[str, ...]:
    script = tmp_path / "fake_fftdc.py"
    script.write_text(body, encoding="utf-8")
    return sys.executable, str(script)


def test_resolve_explicit_executable(tmp_path):
    executable = tmp_path / "fftdc.exe"
    executable.write_bytes(b"stub")

    assert resolve_fftdc_command(executable) == (str(executable.resolve()),)


def test_resolve_explicit_command_sequence_preserves_arguments(tmp_path):
    script = tmp_path / "wrapper.py"
    script.write_text("", encoding="utf-8")

    resolved = resolve_fftdc_command([sys.executable, str(script), "--wrapper"])

    assert resolved[0] == str(Path(sys.executable).resolve())
    assert resolved[1:] == (str(script), "--wrapper")


def test_resolve_from_environment(tmp_path):
    executable = tmp_path / "fftdc.exe"
    executable.write_bytes(b"stub")

    resolved = resolve_fftdc_command(
        environment={FFTDC_ENVIRONMENT_VARIABLE: str(executable)},
        project_root=tmp_path / "missing-project",
    )

    assert resolved == (str(executable.resolve()),)


def test_resolve_from_project_tools_directory(tmp_path):
    executable = tmp_path / "tools" / "ResourceBuilder" / "fftdc.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"stub")

    assert resolve_fftdc_command(environment={}, project_root=tmp_path) == (str(executable),)


def test_resolve_missing_tool_has_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr("core.radio_logo.resource_builder.shutil.which", lambda _: None)

    with pytest.raises(FftdcNotFoundError, match=FFTDC_ENVIRONMENT_VARIABLE):
        resolve_fftdc_command(environment={}, project_root=tmp_path)


def test_validate_source_directory_lists_supported_textures(tmp_path):
    source = _texture_dir(tmp_path)
    (source / "notes.txt").write_text("ignored", encoding="utf-8")

    resolved, files = validate_wtd_source_directory(source)

    assert resolved == source.resolve()
    assert [path.name for path in files] == ["radio_vladivostok.png"]


def test_validate_source_directory_rejects_no_supported_textures(tmp_path):
    source = tmp_path / "empty.wtd"
    source.mkdir()
    (source / "notes.txt").write_text("not a texture", encoding="utf-8")

    with pytest.raises(ValueError, match="no supported texture files"):
        validate_wtd_source_directory(source)


def test_validate_source_directory_rejects_texture_name_collision(tmp_path):
    source = tmp_path / "collision.wtd"
    source.mkdir()
    (source / "RADIO.png").write_bytes(b"one")
    (source / "radio.dds").write_bytes(b"two")

    with pytest.raises(ValueError, match="collide case-insensitively"):
        validate_wtd_source_directory(source)


def test_make_build_command_matches_resource_builder_contract(tmp_path):
    source = _texture_dir(tmp_path)
    output = tmp_path / "output" / "radio_hud.wtd"

    command = make_fftdc_build_command(["fftdc.exe"], output, source)

    assert command == (
        "fftdc.exe",
        "-c_wtd_v8",
        str(output.resolve()),
        "-f",
        str(source.resolve()),
    )


def test_build_wtd_runs_tool_and_commits_verified_output(tmp_path):
    source = _texture_dir(tmp_path)
    output = tmp_path / "built" / "radio_hud.wtd"
    tool = _fake_builder(
        tmp_path,
        """
import pathlib
import sys
args = sys.argv[1:]
output = pathlib.Path(args[args.index('-c_wtd_v8') + 1])
source = pathlib.Path(args[args.index('-f') + 1])
output.parent.mkdir(parents=True, exist_ok=True)
payload = b'FAKE-WTD\\0' + b'|'.join(path.name.encode() for path in sorted(source.iterdir()))
output.write_bytes(payload)
print('builder stdout')
print('builder stderr', file=sys.stderr)
""".strip(),
    )

    result = build_wtd(source, output, fftdc_command=tool)

    assert output.read_bytes().startswith(b"FAKE-WTD\0")
    assert result.output_path == str(output.resolve())
    assert result.file_size == output.stat().st_size
    assert len(result.sha256) == 64
    assert "builder stdout" in result.stdout
    assert "builder stderr" in result.stderr
    assert ".gtaiv_wtd_build_" in result.command[result.command.index("-c_wtd_v8") + 1]


def test_build_wtd_refuses_existing_output_without_overwrite(tmp_path):
    source = _texture_dir(tmp_path)
    output = tmp_path / "built" / "radio_hud.wtd"
    output.parent.mkdir()
    output.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        build_wtd(source, output, fftdc_command=[sys.executable])


def test_build_wtd_overwrites_atomically_when_enabled(tmp_path):
    source = _texture_dir(tmp_path)
    output = tmp_path / "built" / "radio_hud.wtd"
    output.parent.mkdir()
    output.write_bytes(b"old")
    tool = _fake_builder(
        tmp_path,
        """
import pathlib
import sys
args = sys.argv[1:]
pathlib.Path(args[args.index('-c_wtd_v8') + 1]).write_bytes(b'new-wtd')
""".strip(),
    )

    build_wtd(source, output, fftdc_command=tool, overwrite=True)

    assert output.read_bytes() == b"new-wtd"


def test_build_wtd_reports_nonzero_exit_with_tool_output(tmp_path):
    source = _texture_dir(tmp_path)
    tool = _fake_builder(
        tmp_path,
        """
import sys
print('failed stdout')
print('failed stderr', file=sys.stderr)
raise SystemExit(7)
""".strip(),
    )

    with pytest.raises(WtdBuildError) as error:
        build_wtd(source, tmp_path / "built" / "radio_hud.wtd", fftdc_command=tool)

    message = str(error.value)
    assert "exit code 7" in message
    assert "failed stdout" in message
    assert "failed stderr" in message


def test_build_wtd_rejects_success_without_output(tmp_path):
    source = _texture_dir(tmp_path)
    tool = _fake_builder(tmp_path, "print('no output')")

    with pytest.raises(WtdBuildError, match="did not create a non-empty WTD"):
        build_wtd(source, tmp_path / "built" / "radio_hud.wtd", fftdc_command=tool)


def test_build_wtd_requires_wtd_extension(tmp_path):
    source = _texture_dir(tmp_path)

    with pytest.raises(ValueError, match=r"\.wtd extension"):
        build_wtd(source, tmp_path / "radio_hud.bin", fftdc_command=[sys.executable])
