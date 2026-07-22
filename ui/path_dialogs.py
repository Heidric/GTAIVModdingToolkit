import os
from pathlib import Path
from PySide6.QtCore import QDir, QSettings
from PySide6.QtWidgets import QFileDialog, QWidget


class PathHistoryKey:
    GTA_IV_INSTALLATION = "gta_iv_installation"
    REPLACEMENT_AUDIO = "replacement_audio"
    BATCH_REPLACEMENT_AUDIO = "batch_replacement_audio"
    RADIO_LOGO_PACK = "radio_logo_pack"
    RADIO_LOGO_IMAGE = "radio_logo_image"
    SUPPORT_BUNDLE = "support_bundle"


_SETTINGS_ORGANIZATION = "Heidric"
_SETTINGS_APPLICATION = "GTAIVModdingToolkit"
_SETTINGS_PREFIX = "path_history"


def _settings() -> QSettings:
    return QSettings(_SETTINGS_ORGANIZATION, _SETTINGS_APPLICATION)


def _settings_key(history_key: str) -> str:
    if not history_key or not history_key.strip():
        raise ValueError("history_key must not be empty")
    return f"{_SETTINGS_PREFIX}/{history_key.strip()}"


def _directory_from_path(path: str) -> str:
    if not path:
        return ""

    candidate = Path(os.path.abspath(os.path.expanduser(path)))
    if candidate.is_file():
        candidate = candidate.parent
    elif not candidate.is_dir():
        candidate = candidate.parent

    return str(candidate) if candidate.is_dir() else ""


def get_remembered_directory(history_key: str) -> str:
    value = _settings().value(_settings_key(history_key), "", type=str)
    return value if value and os.path.isdir(value) else ""


def remember_directory(history_key: str, path: str) -> None:
    directory = _directory_from_path(path)
    if not directory:
        return

    settings = _settings()
    settings.setValue(_settings_key(history_key), directory)
    settings.sync()


def _initial_directory(history_key: str, fallback: str = "") -> str:
    remembered = get_remembered_directory(history_key)
    if remembered:
        return remembered

    fallback_directory = _directory_from_path(fallback)
    return fallback_directory or QDir.homePath()


def select_existing_directory(
    parent: QWidget,
    title: str,
    history_key: str,
    fallback: str = "",
) -> str:
    selected = QFileDialog.getExistingDirectory(
        parent,
        title,
        _initial_directory(history_key, fallback),
    )
    if selected:
        remember_directory(history_key, selected)
    return selected


def select_open_file(
    parent: QWidget,
    title: str,
    history_key: str,
    file_filter: str = "All Files (*)",
    fallback: str = "",
) -> str:
    selected, _ = QFileDialog.getOpenFileName(
        parent,
        title,
        _initial_directory(history_key, fallback),
        file_filter,
    )
    if selected:
        remember_directory(history_key, selected)
    return selected


def select_open_files(
    parent: QWidget,
    title: str,
    history_key: str,
    file_filter: str = "All Files (*)",
    fallback: str = "",
) -> list[str]:
    selected, _ = QFileDialog.getOpenFileNames(
        parent,
        title,
        _initial_directory(history_key, fallback),
        file_filter,
    )
    if selected:
        remember_directory(history_key, selected[0])
    return selected


def select_save_file(
    parent: QWidget,
    title: str,
    history_key: str,
    file_filter: str = "All Files (*)",
    suggested_name: str = "",
    fallback: str = "",
) -> str:
    initial_directory = _initial_directory(history_key, fallback)
    initial_path = os.path.join(initial_directory, suggested_name) if suggested_name else initial_directory
    selected, _ = QFileDialog.getSaveFileName(parent, title, initial_path, file_filter)
    if selected:
        remember_directory(history_key, selected)
    return selected
