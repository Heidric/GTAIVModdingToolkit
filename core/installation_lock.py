"""Cross-process locks for GTA IV installation mutations."""

from __future__ import annotations

import json
import os
import re
import socket
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable, Iterator, TypeVar

_LOCK_DIRECTORY = Path(".gtaiv_toolkit") / "locks"
_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_LOCK_BYTE_COUNT = 1

Result = TypeVar("Result")


class InstallationBusyError(RuntimeError):
    """Raised when another toolkit process owns an installation mutation lock."""


@dataclass(frozen=True)
class InstallationLockOwner:
    pid: int
    hostname: str
    operation: str
    acquired_at_utc: str

    @classmethod
    def from_dict(cls, value: object) -> "InstallationLockOwner | None":
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                pid=int(value["pid"]),
                hostname=str(value["hostname"]),
                operation=str(value["operation"]),
                acquired_at_utc=str(value["acquired_at_utc"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def installation_lock_path(
    gtaiv_path: str | os.PathLike[str],
    *,
    scope: str = "audio",
) -> Path:
    root = Path(gtaiv_path).expanduser().resolve()
    normalized_scope = scope.strip().casefold()
    if not _SCOPE_PATTERN.fullmatch(normalized_scope):
        raise ValueError(
            "lock scope must start with an alphanumeric character and contain only "
            "letters, digits, dots, underscores, or hyphens"
        )
    return root / _LOCK_DIRECTORY / f"{normalized_scope}.lock"


def _ensure_lock_byte(stream) -> None:
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
        os.fsync(stream.fileno())


def _acquire_file_lock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTE_COUNT)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_file_lock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTE_COUNT)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _write_owner(stream, owner: InstallationLockOwner) -> None:
    payload = json.dumps(asdict(owner), indent=2, sort_keys=True).encode("utf-8")
    stream.seek(_LOCK_BYTE_COUNT)
    stream.truncate()
    stream.write(payload + b"\n")
    stream.flush()
    os.fsync(stream.fileno())


def _read_owner(path: Path) -> InstallationLockOwner | None:
    try:
        with path.open("rb") as stream:
            stream.seek(_LOCK_BYTE_COUNT)
            payload = stream.read().decode("utf-8")
        return InstallationLockOwner.from_dict(json.loads(payload))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _busy_message(path: Path, owner: InstallationLockOwner | None) -> str:
    if owner is None:
        return (
            "Another GTA IV Modding Toolkit process is already modifying audio files "
            f"for this installation. Lock: {path}"
        )
    return (
        "Another GTA IV Modding Toolkit process is already modifying audio files "
        f"for this installation: {owner.operation} on {owner.hostname} "
        f"(PID {owner.pid}, since {owner.acquired_at_utc})."
    )


@contextmanager
def installation_lock(
    gtaiv_path: str | os.PathLike[str],
    *,
    operation: str,
    scope: str = "audio",
) -> Iterator[InstallationLockOwner]:
    """Acquire one non-blocking cross-process lock for a GTA IV installation."""

    root = Path(gtaiv_path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"GTA IV directory not found: {root}")

    operation_name = operation.strip()
    if not operation_name:
        raise ValueError("operation must not be empty")

    lock_path = installation_lock_path(root, scope=scope)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    acquired = False

    try:
        _ensure_lock_byte(stream)
        try:
            _acquire_file_lock(stream)
            acquired = True
        except OSError as exc:
            raise InstallationBusyError(
                _busy_message(lock_path, _read_owner(lock_path))
            ) from exc

        owner = InstallationLockOwner(
            pid=os.getpid(),
            hostname=socket.gethostname(),
            operation=operation_name,
            acquired_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        _write_owner(stream, owner)
        yield owner
    finally:
        if acquired:
            try:
                _release_file_lock(stream)
            finally:
                stream.close()
        else:
            stream.close()


def locked_installation_operation(
    operation: str,
    *,
    scope: str = "audio",
) -> Callable[[Callable[..., Result]], Callable[..., Result]]:
    """Decorate a synchronous operation whose first argument is a GTA IV path."""

    def decorator(function: Callable[..., Result]) -> Callable[..., Result]:
        @wraps(function)
        def wrapper(gtaiv_path, *args, **kwargs):
            root = Path(gtaiv_path).expanduser().resolve()
            if not root.is_dir():
                return function(gtaiv_path, *args, **kwargs)
            with installation_lock(root, operation=operation, scope=scope):
                return function(gtaiv_path, *args, **kwargs)

        return wrapper

    return decorator
