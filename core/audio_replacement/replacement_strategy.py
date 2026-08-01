import os
import shutil
import abc
from datetime import datetime


def _timestamped_backup_path(path):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{path}.backup-{timestamp}"


def _backup_file(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    backup_path = _timestamped_backup_path(path)
    shutil.copy2(path, backup_path)
    print(f"Created backup: {backup_path}")
    return backup_path

class ReplacementStrategy(abc.ABC):
    def __init__(self, gtaiv_path):
        self.gtaiv_path = gtaiv_path

    @abc.abstractmethod
    def get_rpf_path(self, radio_file_name):
        pass

    @abc.abstractmethod
    def prepare_rpf(self, radio_file_name):
        pass

    @abc.abstractmethod
    def get_dat15_path(self):
        pass

    @abc.abstractmethod
    def prepare_dat15(self):
        pass


class DirectReplacementStrategy(ReplacementStrategy):
    def get_rpf_path(self, radio_file_name):
        return os.path.join(self.gtaiv_path, "pc", "audio", "sfx", radio_file_name)

    def prepare_rpf(self, radio_file_name):
        _backup_file(self.get_rpf_path(radio_file_name))

    def get_dat15_path(self):
        return os.path.join(self.gtaiv_path, "pc", "audio", "config", "sounds.dat15")

    def prepare_dat15(self):
        _backup_file(self.get_dat15_path())


class FusionFixReplacementStrategy(ReplacementStrategy):
    def get_rpf_path(self, radio_file_name):
        return os.path.join(self.gtaiv_path, "update", "pc", "audio", "sfx", radio_file_name)

    def prepare_rpf(self, radio_file_name):
        target_path = self.get_rpf_path(radio_file_name)
        if not os.path.exists(target_path):
            source_path = os.path.join(self.gtaiv_path, "pc", "audio", "sfx", radio_file_name)
            if not os.path.exists(source_path):
                raise FileNotFoundError(f"Original file not found: {source_path}")

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            print(f"Copying {source_path} to {target_path}...")
            shutil.copy2(source_path, target_path)

    def get_dat15_path(self):
        return os.path.join(self.gtaiv_path, "update", "pc", "audio", "config", "sounds.dat15")

    def prepare_dat15(self):
        target_path = self.get_dat15_path()
        if not os.path.exists(target_path):
            source_path = os.path.join(self.gtaiv_path, "pc", "audio", "config", "sounds.dat15")
            if not os.path.exists(source_path):
                raise FileNotFoundError(f"Original file not found: {source_path}")

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            print(f"Copying {source_path} to {target_path}...")
            shutil.copy2(source_path, target_path)


def check_fusionfix_installed(gtaiv_path):
    # Check for FusionFix ASI file
    # Common paths: plugins/GTAIV.EFLC.FusionFix.asi
    # Also check if plugins folder exists at all.

    # We assume standard structure relative to GTAIV root
    possible_paths = [
        os.path.join(gtaiv_path, "plugins", "GTAIV.EFLC.FusionFix.asi"),
        os.path.join(gtaiv_path, "plugins", "FusionFix.asi") # Fallback just in case name changes or older ver
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return True

    return False
