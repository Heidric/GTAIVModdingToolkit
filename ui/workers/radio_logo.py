"""Qt workers for radio-logo installation and recovery."""

from PySide6.QtCore import QThread, Signal

from core.radio_logo.installer import (
    install_radio_logo_pack,
    restore_previous_radio_logo_pack,
)
from core.radio_logo.workflow import install_station_logo_from_image


class StationLogoInstallWorker(QThread):
    completed = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        gtaiv_path,
        target,
        station_base,
        source_image,
        use_direct,
        fit_mode,
        padding_ratio,
    ):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.target = target
        self.station_base = station_base
        self.source_image = source_image
        self.use_direct = use_direct
        self.fit_mode = fit_mode
        self.padding_ratio = padding_ratio

    def run(self):
        try:
            result = install_station_logo_from_image(
                self.gtaiv_path,
                self.target,
                self.station_base,
                self.source_image,
                use_direct=self.use_direct,
                fit_mode=self.fit_mode,
                padding_ratio=self.padding_ratio,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return

        self.completed.emit(result)


class PreparedPackInstallWorker(QThread):
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, gtaiv_path, source_files, target, use_direct):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.source_files = list(source_files)
        self.target = target
        self.use_direct = use_direct

    def run(self):
        try:
            result = install_radio_logo_pack(
                self.gtaiv_path,
                self.source_files,
                self.target,
                use_direct=self.use_direct,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return

        self.completed.emit(result)


class RadioLogoRecoveryWorker(QThread):
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, gtaiv_path, target, use_direct):
        super().__init__()
        self.gtaiv_path = gtaiv_path
        self.target = target
        self.use_direct = use_direct

    def run(self):
        try:
            result = restore_previous_radio_logo_pack(
                self.gtaiv_path,
                self.target,
                use_direct=self.use_direct,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return

        self.completed.emit(result)
