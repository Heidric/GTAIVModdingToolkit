import logging
from pathlib import Path

from core.app_logging import (
    app_data_directory,
    configure_application_logging,
    get_application_logger,
    shutdown_application_logging,
)


def test_app_data_directory_uses_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert app_data_directory() == tmp_path.resolve() / "GTAIVModdingToolkit"


def test_configure_application_logging_writes_rotating_log(tmp_path):
    try:
        log_path = configure_application_logging(
            log_directory=tmp_path,
            capture_streams=False,
            max_bytes=1024,
            backup_count=1,
        )
        logger = get_application_logger()
        logger.info("diagnostic test line")
        for handler in logger.handlers:
            handler.flush()

        assert log_path == tmp_path / "app.log"
        assert "diagnostic test line" in log_path.read_text(encoding="utf-8")
        assert any(
            isinstance(handler, logging.handlers.RotatingFileHandler)
            for handler in logger.handlers
        )
    finally:
        shutdown_application_logging()
