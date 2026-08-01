from core.app_preferences import AppPreferences, load_preferences, save_preferences
from core.archive_backups import DEFAULT_ROLLING_BACKUP_LIMIT


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


def test_backup_limit_defaults_and_round_trips():
    assert load_preferences(MemorySettings()).rolling_backup_limit == DEFAULT_ROLLING_BACKUP_LIMIT

    settings = MemorySettings()
    expected = AppPreferences(rolling_backup_limit=7)
    save_preferences(expected, settings)

    assert settings.values["preferences/rolling_archive_backups"] == 7
    assert load_preferences(settings) == expected


def test_invalid_saved_backup_limit_falls_back_to_default():
    settings = MemorySettings({"preferences/rolling_archive_backups": "broken"})
    assert load_preferences(settings).rolling_backup_limit == DEFAULT_ROLLING_BACKUP_LIMIT
