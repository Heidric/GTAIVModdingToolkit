"""Persistent user preferences shared by the start and settings pages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

_SETTINGS_ORGANIZATION = "Heidric"
_SETTINGS_APPLICATION = "GTAIVModdingToolkit"
_REPLACEMENT_MODE_KEY = "preferences/replacement_mode"
_AUTO_DETECT_KEY = "preferences/auto_detect_installation"


class SettingsStore(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...

    def sync(self) -> None: ...


class ReplacementMode(str, Enum):
    FUSIONFIX = "fusionfix"
    DIRECT = "direct"

    @classmethod
    def from_value(cls, value: object) -> "ReplacementMode":
        try:
            return cls(str(value).strip().casefold())
        except ValueError:
            return cls.FUSIONFIX


@dataclass(frozen=True)
class AppPreferences:
    replacement_mode: ReplacementMode = ReplacementMode.FUSIONFIX
    auto_detect_installation: bool = True

    @property
    def use_direct(self) -> bool:
        return self.replacement_mode is ReplacementMode.DIRECT


def _default_settings() -> SettingsStore:
    from PySide6.QtCore import QSettings

    return QSettings(_SETTINGS_ORGANIZATION, _SETTINGS_APPLICATION)


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def load_preferences(settings: SettingsStore | None = None) -> AppPreferences:
    store = settings or _default_settings()
    mode = ReplacementMode.from_value(
        store.value(_REPLACEMENT_MODE_KEY, ReplacementMode.FUSIONFIX.value)
    )
    auto_detect = _as_bool(store.value(_AUTO_DETECT_KEY, True), True)
    return AppPreferences(
        replacement_mode=mode,
        auto_detect_installation=auto_detect,
    )


def save_preferences(
    preferences: AppPreferences,
    settings: SettingsStore | None = None,
) -> None:
    store = settings or _default_settings()
    store.setValue(_REPLACEMENT_MODE_KEY, preferences.replacement_mode.value)
    store.setValue(_AUTO_DETECT_KEY, preferences.auto_detect_installation)
    store.sync()
