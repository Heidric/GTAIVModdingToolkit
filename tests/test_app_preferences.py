from core.app_preferences import (
    AppPreferences,
    ReplacementMode,
    load_preferences,
    save_preferences,
)


class MemorySettings:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.synced = False

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        self.synced = True


def test_preferences_default_to_safe_mode_and_auto_detection():
    preferences = load_preferences(MemorySettings())

    assert preferences.replacement_mode is ReplacementMode.FUSIONFIX
    assert preferences.auto_detect_installation is True
    assert preferences.use_direct is False


def test_preferences_round_trip():
    settings = MemorySettings()
    expected = AppPreferences(
        replacement_mode=ReplacementMode.DIRECT,
        auto_detect_installation=False,
    )

    save_preferences(expected, settings)

    assert settings.synced is True
    assert load_preferences(settings) == expected


def test_unknown_replacement_mode_falls_back_to_fusionfix():
    preferences = load_preferences(
        MemorySettings({"preferences/replacement_mode": "unsupported"})
    )

    assert preferences.replacement_mode is ReplacementMode.FUSIONFIX
