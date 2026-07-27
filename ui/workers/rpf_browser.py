"""Qt workers for RPF and WTD browser operations."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.rpf_archive import export_rpf_entry, inspect_rpf_archive
from core.rpf_wtd import replace_rpf_wtd_texture_from_image_transactional
from core.wtd_archive import (
    export_wtd_texture,
    inspect_wtd_archive,
    render_wtd_texture_preview,
)


class RPFInspectWorker(QThread):
    completed = Signal(int, object)
    error = Signal(int, str)

    def __init__(self, request_id: int, archive_path: str, gtaiv_exe_path: str):
        super().__init__()
        self.request_id = request_id
        self.archive_path = archive_path
        self.gtaiv_exe_path = gtaiv_exe_path

    def run(self):
        try:
            snapshot = inspect_rpf_archive(
                self.archive_path,
                self.gtaiv_exe_path,
            )
        except Exception as exc:
            self.error.emit(self.request_id, str(exc))
            return
        self.completed.emit(self.request_id, snapshot)


class RPFWTDInspectWorker(QThread):
    completed = Signal(int, str, object, str)
    error = Signal(int, str, str)

    def __init__(
        self,
        request_id: int,
        archive_path: str,
        gtaiv_exe_path: str,
        entry_path: str,
        extracted_path: str,
    ):
        super().__init__()
        self.request_id = request_id
        self.archive_path = archive_path
        self.gtaiv_exe_path = gtaiv_exe_path
        self.entry_path = entry_path
        self.extracted_path = extracted_path

    def run(self):
        try:
            local_path = export_rpf_entry(
                self.archive_path,
                self.gtaiv_exe_path,
                self.entry_path,
                self.extracted_path,
                overwrite=True,
            )
            snapshot = inspect_wtd_archive(local_path)
        except Exception as exc:
            self.error.emit(self.request_id, self.entry_path, str(exc))
            return
        self.completed.emit(
            self.request_id,
            self.entry_path,
            snapshot,
            str(Path(local_path).resolve()),
        )


class RPFEntryExportWorker(QThread):
    completed = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        archive_path: str,
        gtaiv_exe_path: str,
        entry_path: str,
        destination_path: str,
        *,
        overwrite: bool,
    ):
        super().__init__()
        self.archive_path = archive_path
        self.gtaiv_exe_path = gtaiv_exe_path
        self.entry_path = entry_path
        self.destination_path = destination_path
        self.overwrite = overwrite

    def run(self):
        try:
            result = export_rpf_entry(
                self.archive_path,
                self.gtaiv_exe_path,
                self.entry_path,
                self.destination_path,
                overwrite=self.overwrite,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.completed.emit(str(result))


class WTDTexturePreviewWorker(QThread):
    completed = Signal(int, int, object)
    error = Signal(int, int, str)

    def __init__(
        self,
        request_id: int,
        wtd_path: str,
        texture_index: int,
        *,
        max_dimension: int = 512,
    ):
        super().__init__()
        self.request_id = request_id
        self.wtd_path = wtd_path
        self.texture_index = texture_index
        self.max_dimension = max_dimension

    def run(self):
        try:
            preview = render_wtd_texture_preview(
                self.wtd_path,
                self.texture_index,
                max_dimension=self.max_dimension,
            )
        except Exception as exc:
            self.error.emit(self.request_id, self.texture_index, str(exc))
            return
        self.completed.emit(self.request_id, self.texture_index, preview)


class WTDTextureExportWorker(QThread):
    completed = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        wtd_path: str,
        texture_index: int,
        destination_path: str,
        *,
        overwrite: bool,
    ):
        super().__init__()
        self.wtd_path = wtd_path
        self.texture_index = texture_index
        self.destination_path = destination_path
        self.overwrite = overwrite

    def run(self):
        try:
            result = export_wtd_texture(
                self.wtd_path,
                self.texture_index,
                self.destination_path,
                overwrite=self.overwrite,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.completed.emit(str(result))


class RPFWTDTextureReplaceWorker(QThread):
    completed = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        archive_path: str,
        gtaiv_exe_path: str,
        entry_path: str,
        texture_index: int,
        image_path: str,
        *,
        quality: float = 0.9,
    ):
        super().__init__()
        self.archive_path = archive_path
        self.gtaiv_exe_path = gtaiv_exe_path
        self.entry_path = entry_path
        self.texture_index = texture_index
        self.image_path = image_path
        self.quality = quality

    def run(self):
        try:
            result = replace_rpf_wtd_texture_from_image_transactional(
                self.archive_path,
                self.gtaiv_exe_path,
                self.entry_path,
                self.texture_index,
                self.image_path,
                quality=self.quality,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.completed.emit(result)
