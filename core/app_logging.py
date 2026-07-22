"""Persistent application logging and uncaught-exception capture."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path
from typing import TextIO

_LOGGER_NAME = "gtaiv_toolkit"
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 5

_original_stdout: TextIO | None = None
_original_stderr: TextIO | None = None
_original_sys_excepthook = None
_original_threading_excepthook = None


def app_data_directory() -> Path:
    """Return the writable per-user application directory."""

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data).expanduser().resolve() / "GTAIVModdingToolkit"
    return Path.home().resolve() / ".gtaiv_modding_toolkit"


def application_log_directory() -> Path:
    return app_data_directory() / "logs"


def application_log_path() -> Path:
    return application_log_directory() / "app.log"


def get_application_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


class _LoggingTextStream:
    """Forward text to the original stream and mirror complete lines to a logger."""

    def __init__(self, logger: logging.Logger, level: int, original: TextIO | None):
        self._logger = logger
        self._level = level
        self._original = original
        self._buffer = ""

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", None) or "utf-8"

    @property
    def errors(self) -> str:
        return getattr(self._original, "errors", None) or "replace"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return bool(self._original and self._original.isatty())

    def fileno(self) -> int:
        if self._original is None:
            raise OSError("stream has no file descriptor")
        return self._original.fileno()

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            text = str(text)

        if self._original is not None:
            self._original.write(text)

        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.rstrip("\r"):
                self._logger.log(self._level, line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            line = self._buffer.rstrip("\r")
            self._buffer = ""
            if line:
                self._logger.log(self._level, line)
        if self._original is not None:
            self._original.flush()


def _install_exception_hooks(logger: logging.Logger) -> None:
    global _original_sys_excepthook, _original_threading_excepthook

    if _original_sys_excepthook is None:
        _original_sys_excepthook = sys.excepthook

        def sys_hook(exc_type, exc_value, exc_traceback):
            logger.critical(
                "Unhandled exception",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
            if _original_sys_excepthook is not None:
                _original_sys_excepthook(exc_type, exc_value, exc_traceback)

        sys.excepthook = sys_hook

    if hasattr(threading, "excepthook") and _original_threading_excepthook is None:
        _original_threading_excepthook = threading.excepthook

        def thread_hook(args):
            logger.critical(
                "Unhandled exception in thread %s",
                getattr(args.thread, "name", "<unknown>"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            if _original_threading_excepthook is not None:
                _original_threading_excepthook(args)

        threading.excepthook = thread_hook


def configure_application_logging(
    *,
    log_directory: str | os.PathLike[str] | None = None,
    capture_streams: bool = True,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
) -> Path:
    """Configure one rotating log file and return its path."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if backup_count < 0:
        raise ValueError("backup_count must not be negative")

    directory = (
        Path(log_directory).expanduser().resolve()
        if log_directory is not None
        else application_log_directory()
    )
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "app.log"

    logger = get_application_logger()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    matching_handler = None
    for handler in list(logger.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            existing = Path(handler.baseFilename).resolve()
            if existing == log_path:
                matching_handler = handler
                continue
        handler.flush()
        handler.close()
        logger.removeHandler(handler)

    if matching_handler is None:
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        logger.addHandler(handler)

    _install_exception_hooks(logger)

    if capture_streams:
        global _original_stdout, _original_stderr
        if not isinstance(sys.stdout, _LoggingTextStream):
            _original_stdout = sys.stdout
            sys.stdout = _LoggingTextStream(logger, logging.INFO, _original_stdout)
        if not isinstance(sys.stderr, _LoggingTextStream):
            _original_stderr = sys.stderr
            sys.stderr = _LoggingTextStream(logger, logging.ERROR, _original_stderr)

    logger.info("Application logging initialized: %s", log_path)
    return log_path


def flush_application_logs() -> None:
    for handler in get_application_logger().handlers:
        handler.flush()


def shutdown_application_logging() -> None:
    """Flush handlers and restore streams and exception hooks."""

    global _original_stdout, _original_stderr
    global _original_sys_excepthook, _original_threading_excepthook

    if isinstance(sys.stdout, _LoggingTextStream):
        sys.stdout.flush()
        sys.stdout = _original_stdout
    if isinstance(sys.stderr, _LoggingTextStream):
        sys.stderr.flush()
        sys.stderr = _original_stderr
    _original_stdout = None
    _original_stderr = None

    if _original_sys_excepthook is not None:
        sys.excepthook = _original_sys_excepthook
        _original_sys_excepthook = None
    if _original_threading_excepthook is not None:
        threading.excepthook = _original_threading_excepthook
        _original_threading_excepthook = None

    logger = get_application_logger()
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
