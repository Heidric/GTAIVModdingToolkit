from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


FFTDC_ENVIRONMENT_VARIABLE = "GTAIV_FFTDC_PATH"
DEFAULT_TIMEOUT_SECONDS = 120
SUPPORTED_WTD_SOURCE_EXTENSIONS = frozenset({".bmp", ".dds", ".png", ".tga"})


class WtdResourceBuilderError(RuntimeError):
    """Base error raised by the GTA IV WTD ResourceBuilder adapter."""


class FftdcNotFoundError(WtdResourceBuilderError):
    """Raised when fftdc.exe cannot be resolved."""


class WtdBuildError(WtdResourceBuilderError):
    """Raised when fftdc.exe fails to create a valid WTD file."""


@dataclass(frozen=True)
class WtdBuildResult:
    source_directory: str
    output_path: str
    command: tuple[str, ...]
    file_size: int
    sha256: str
    stdout: str
    stderr: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_command(value: str | os.PathLike[str] | Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, os.PathLike)):
        command = (os.fspath(value),)
    else:
        command = tuple(os.fspath(part) for part in value)

    if not command or any(not part.strip() for part in command):
        raise ValueError("fftdc command must contain at least one non-empty argument.")
    return command


def _resolve_program(program: str) -> str | None:
    candidate = Path(program).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    return shutil.which(program)


def resolve_fftdc_command(
    explicit: str | os.PathLike[str] | Sequence[str] | None = None,
    *,
    environment: dict[str, str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """Resolve the command used to launch FusionFix ResourceBuilder's fftdc.

    Resolution order:
    1. Explicit path or command sequence.
    2. ``GTAIV_FFTDC_PATH``.
    3. ``tools/ResourceBuilder/fftdc.exe`` under the project root.
    4. ``fftdc.exe`` or ``fftdc`` from PATH.

    A command sequence is supported primarily for wrappers and tests, for
    example ``[sys.executable, "fake_fftdc.py"]``.
    """
    env = os.environ if environment is None else environment

    if explicit is not None:
        command = _as_command(explicit)
        resolved = _resolve_program(command[0])
        if resolved is None:
            raise FftdcNotFoundError(f"fftdc executable not found: {command[0]}")
        return (resolved, *command[1:])

    environment_value = env.get(FFTDC_ENVIRONMENT_VARIABLE, "").strip()
    if environment_value:
        return resolve_fftdc_command(environment_value, environment={}, project_root=project_root)

    root = Path(project_root).expanduser().resolve() if project_root else Path(__file__).resolve().parents[2]
    bundled_candidate = root / "tools" / "ResourceBuilder" / "fftdc.exe"
    if bundled_candidate.is_file():
        return (str(bundled_candidate),)

    for program in ("fftdc.exe", "fftdc"):
        resolved = shutil.which(program)
        if resolved:
            return (resolved,)

    raise FftdcNotFoundError(
        "fftdc.exe was not found. Pass an explicit path, set "
        f"{FFTDC_ENVIRONMENT_VARIABLE}, place the tool at "
        "tools/ResourceBuilder/fftdc.exe, or add it to PATH."
    )


def validate_wtd_source_directory(source_directory: str | os.PathLike[str]) -> tuple[Path, tuple[Path, ...]]:
    source = Path(source_directory).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"WTD source directory not found: {source}")

    files = tuple(
        sorted(
            (
                path
                for path in source.iterdir()
                if path.is_file() and path.suffix.casefold() in SUPPORTED_WTD_SOURCE_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
    )
    if not files:
        allowed = ", ".join(sorted(SUPPORTED_WTD_SOURCE_EXTENSIONS))
        raise ValueError(
            f"WTD source directory contains no supported texture files: {source}. "
            f"Expected at least one of: {allowed}."
        )

    empty_files = [path.name for path in files if path.stat().st_size <= 0]
    if empty_files:
        raise ValueError("WTD source contains empty texture files: " + ", ".join(empty_files))

    duplicate_names: dict[str, list[str]] = {}
    for path in files:
        duplicate_names.setdefault(path.stem.casefold(), []).append(path.name)
    collisions = [names for names in duplicate_names.values() if len(names) > 1]
    if collisions:
        detail = "; ".join(", ".join(names) for names in collisions)
        raise ValueError(f"WTD texture names collide case-insensitively: {detail}")

    return source, files


def make_fftdc_build_command(
    fftdc_command: str | os.PathLike[str] | Sequence[str],
    output_path: str | os.PathLike[str],
    source_directory: str | os.PathLike[str],
) -> tuple[str, ...]:
    command = _as_command(fftdc_command)
    return (
        *command,
        "-c_wtd_v8",
        str(Path(output_path).expanduser().resolve()),
        "-f",
        str(Path(source_directory).expanduser().resolve()),
    )


def format_command(command: Iterable[str]) -> str:
    return subprocess.list2cmdline(list(command)) if os.name == "nt" else shlex.join(command)


def _tool_working_directory(command: Sequence[str]) -> str | None:
    executable = Path(command[0])
    return str(executable.parent) if executable.is_absolute() and executable.is_file() else None


def build_wtd(
    source_directory: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    fftdc_command: str | os.PathLike[str] | Sequence[str] | None = None,
    overwrite: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    environment: dict[str, str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
) -> WtdBuildResult:
    """Build a GTA IV WTD file through FusionFix ResourceBuilder transactionally."""
    source, _ = validate_wtd_source_directory(source_directory)
    output = Path(output_path).expanduser().resolve()

    if output.suffix.casefold() != ".wtd":
        raise ValueError("WTD output path must use the .wtd extension.")
    if output.exists() and not overwrite:
        raise FileExistsError(f"WTD output already exists: {output}")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer.")

    command_prefix = resolve_fftdc_command(
        fftdc_command,
        environment=environment,
        project_root=project_root,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gtaiv_wtd_build_", dir=output.parent) as temporary:
        staged_output = Path(temporary) / output.name
        command = make_fftdc_build_command(command_prefix, staged_output, source)

        try:
            completed = subprocess.run(
                command,
                cwd=_tool_working_directory(command_prefix),
                env=None if environment is None else environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise WtdBuildError(
                f"fftdc timed out after {timeout_seconds} seconds. Command: {format_command(command)}"
            ) from exc
        except OSError as exc:
            raise WtdBuildError(
                f"Could not start fftdc. Command: {format_command(command)}; error: {exc}"
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            raise WtdBuildError(
                f"fftdc failed with exit code {completed.returncode}.\n"
                f"Command: {format_command(command)}\n"
                f"stdout: {stdout.strip() or '<empty>'}\n"
                f"stderr: {stderr.strip() or '<empty>'}"
            )

        if not staged_output.is_file() or staged_output.stat().st_size <= 0:
            raise WtdBuildError(
                "fftdc reported success but did not create a non-empty WTD file.\n"
                f"Expected: {staged_output}\n"
                f"stdout: {stdout.strip() or '<empty>'}\n"
                f"stderr: {stderr.strip() or '<empty>'}"
            )

        file_size = staged_output.stat().st_size
        digest = _sha256(staged_output)
        os.replace(staged_output, output)

    if not output.is_file() or output.stat().st_size != file_size or _sha256(output) != digest:
        raise WtdBuildError(f"WTD verification failed after atomic commit: {output}")

    return WtdBuildResult(
        source_directory=str(source),
        output_path=str(output),
        command=command,
        file_size=file_size,
        sha256=digest,
        stdout=stdout,
        stderr=stderr,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a GTA IV WTD from a texture source directory.")
    parser.add_argument("source_directory")
    parser.add_argument("output_path")
    parser.add_argument("--fftdc", help="Path to FusionFix ResourceBuilder fftdc.exe")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the command without running it")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    source, files = validate_wtd_source_directory(args.source_directory)
    command_prefix = resolve_fftdc_command(args.fftdc)
    output = Path(args.output_path).expanduser().resolve()

    if args.dry_run:
        command = make_fftdc_build_command(command_prefix, output, source)
        print(f"Textures: {len(files)}")
        print(format_command(command))
        return 0

    result = build_wtd(
        source,
        output,
        fftdc_command=command_prefix,
        overwrite=args.overwrite,
        timeout_seconds=args.timeout,
    )
    print(f"Built: {result.output_path}")
    print(f"Size: {result.file_size} bytes")
    print(f"SHA-256: {result.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
