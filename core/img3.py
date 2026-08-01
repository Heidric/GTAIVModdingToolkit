"""Parser and in-place entry writer for GTA IV IMG3 archives."""

from __future__ import annotations

import os
import struct
from pathlib import Path

from vendor.pyrpfiv.crypto import (
    decrypt_toc,
    encrypt_toc,
    extract_aes_key,
    normalize_aes_key,
)
from vendor.pyrpfiv.exceptions import (
    FileExtractionError,
    FileNotFoundInRPFError,
    InvalidTOCEntryError,
    RPFParsingError,
)


IMG3_MAGIC = 0xA94E2A52
IMG3_VERSION = 3
IMG3_HEADER_SIZE = 20
IMG3_ENTRY_SIZE = 16
IMG3_SECTOR_SIZE = 0x800
RSC5_MAGIC = 0x05435352
_RESOURCE_FLAG_MASK = 0xC0000000
_PADDING_MASK = 0x07FF
_MAX_SECTOR_OFFSET = 0x7FFFFFFF
_MAX_USED_BLOCKS = 0xFFFF


class IMG3Parser:
    """Inspect, extract, and replace existing IMG3 entries.

    The class exposes the ``paths``, ``read_file``, and ``add_file`` interface
    used by the existing archive browser backend. ``add_file`` only replaces
    an existing entry; it never adds, removes, or renames archive entries.
    """

    supports_replacement = True

    def __init__(self, img_filename, gtaiv_exe_path=None, aes_key=None):
        self.rpf_filename = str(Path(img_filename).expanduser().resolve())
        self.gtaiv_exe_path = gtaiv_exe_path
        self.entries = []
        self.paths = []
        self.encrypted = False
        self.aes_key = None
        self.version = None
        self.entry_count = None
        self.toc_size = None
        self.toc_entry_size = None
        self.unknown = None
        self._toc_data = b""

        if aes_key is not None:
            self.aes_key = normalize_aes_key(aes_key)
        elif gtaiv_exe_path:
            self.aes_key = extract_aes_key(gtaiv_exe_path)
        else:
            raise ValueError("Either gtaiv_exe_path or aes_key must be provided.")

        self.parse()

    @staticmethod
    def _transform_aligned_prefix(
        payload: bytes,
        aes_key: bytes,
        transform,
    ) -> bytes:
        """Transform complete AES blocks and preserve the trailing bytes.

        GTA IV IMG3 encryption only covers the largest 16-byte-aligned prefix.
        Any remaining bytes at the end of the header or TOC are stored verbatim.
        """
        aligned_size = len(payload) & ~0x0F
        if aligned_size == 0:
            return payload
        return transform(payload[:aligned_size], aes_key) + payload[aligned_size:]

    @classmethod
    def _decode_header(cls, raw_header: bytes, aes_key: bytes) -> tuple[bytes, bool]:
        if len(raw_header) != IMG3_HEADER_SIZE:
            raise RPFParsingError(
                f"Invalid IMG3 header size: expected {IMG3_HEADER_SIZE}, "
                f"got {len(raw_header)}"
            )

        if int.from_bytes(raw_header[:4], "little") == IMG3_MAGIC:
            return raw_header, False

        return cls._transform_aligned_prefix(raw_header, aes_key, decrypt_toc), True

    @staticmethod
    def _read_names(blob: bytes, entry_count: int) -> tuple[list[str], int]:
        names = []
        cursor = 0
        for index in range(entry_count):
            terminator = blob.find(b"\x00", cursor)
            if terminator < 0:
                raise InvalidTOCEntryError(
                    f"IMG3 name table is missing a terminator for entry {index}"
                )
            raw_name = blob[cursor:terminator]
            if not raw_name:
                raise InvalidTOCEntryError(f"IMG3 entry {index} has an empty name")
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError as exc:
                raise InvalidTOCEntryError(
                    f"IMG3 entry {index} has a non-ASCII name"
                ) from exc
            names.append(name)
            cursor = terminator + 1
        return names, cursor

    @staticmethod
    def _align_up(value: int, alignment: int = IMG3_SECTOR_SIZE) -> int:
        return (value + alignment - 1) & ~(alignment - 1)

    @staticmethod
    def _replacement_metadata(entry: dict, payload: bytes) -> tuple[int, int]:
        if not entry["is_resource"]:
            return len(payload), entry["resource_type"]

        if len(payload) < 12:
            raise RPFParsingError(
                "IMG3 resource replacement is shorter than the RSC5 header"
            )
        magic, resource_type, resource_flags = struct.unpack_from("<III", payload, 0)
        if magic != RSC5_MAGIC:
            raise RPFParsingError(
                f"IMG3 resource replacement has invalid RSC5 magic: 0x{magic:08X}"
            )
        if not resource_flags & _RESOURCE_FLAG_MASK:
            raise RPFParsingError(
                "IMG3 resource replacement header does not contain resource flags"
            )
        return resource_flags, resource_type

    def parse(self):
        archive_path = Path(self.rpf_filename)
        if not archive_path.is_file():
            raise FileNotFoundError(f"IMG3 archive not found: {archive_path}")

        with archive_path.open("rb") as source:
            raw_header = source.read(IMG3_HEADER_SIZE)
            header, self.encrypted = self._decode_header(
                raw_header,
                self.aes_key,
            )
            (
                magic,
                self.version,
                self.entry_count,
                self.toc_size,
                self.toc_entry_size,
                self.unknown,
            ) = struct.unpack("<IIIIHH", header)

            if magic != IMG3_MAGIC:
                raise RPFParsingError(
                    f"Unsupported IMG3 identifier: 0x{magic:08X}"
                )
            if self.version != IMG3_VERSION:
                raise RPFParsingError(
                    f"Unsupported IMG3 version: {self.version}"
                )
            if self.toc_entry_size != IMG3_ENTRY_SIZE:
                raise RPFParsingError(
                    f"Unsupported IMG3 TOC entry size: {self.toc_entry_size}"
                )
            if self.entry_count < 0:
                raise RPFParsingError(
                    f"Invalid IMG3 entry count: {self.entry_count}"
                )

            entries_size = self.entry_count * self.toc_entry_size
            if self.toc_size < entries_size:
                raise RPFParsingError(
                    "IMG3 TOC is smaller than its fixed-size entry array"
                )

            raw_toc = source.read(self.toc_size)

        if len(raw_toc) != self.toc_size:
            raise RPFParsingError(
                f"Invalid IMG3 TOC size: expected {self.toc_size}, "
                f"got {len(raw_toc)}"
            )
        if self.encrypted:
            toc = self._transform_aligned_prefix(
                raw_toc,
                self.aes_key,
                decrypt_toc,
            )
        else:
            toc = raw_toc
        self._toc_data = toc

        entries_size = self.entry_count * self.toc_entry_size
        names, names_used = self._read_names(toc[entries_size:], self.entry_count)
        trailing_names = toc[entries_size + names_used:]
        if trailing_names.strip(b"\x00"):
            raise InvalidTOCEntryError(
                "IMG3 name table contains unexpected non-zero trailing bytes"
            )

        archive_size = archive_path.stat().st_size
        seen_names = set()
        self.entries = []
        self.paths = []

        for index, name in enumerate(names):
            if name in seen_names:
                raise InvalidTOCEntryError(
                    f"IMG3 archive contains a duplicate entry name: {name}"
                )
            seen_names.add(name)

            entry_offset = index * self.toc_entry_size
            (
                raw_size_or_flags,
                resource_type,
                sector_offset,
                used_blocks,
                flags,
            ) = struct.unpack_from("<IIIHH", toc, entry_offset)

            is_resource = bool(raw_size_or_flags & _RESOURCE_FLAG_MASK)
            padding_count = flags & _PADDING_MASK
            if is_resource:
                allocated_size = used_blocks * IMG3_SECTOR_SIZE
                if padding_count > allocated_size:
                    raise InvalidTOCEntryError(
                        f"IMG3 entry {name} has invalid padding {padding_count} "
                        f"for {allocated_size} allocated bytes"
                    )
                size = allocated_size - padding_count
            else:
                size = raw_size_or_flags

            byte_offset = sector_offset * IMG3_SECTOR_SIZE
            if byte_offset < 0 or size < 0 or byte_offset + size > archive_size:
                raise InvalidTOCEntryError(
                    f"IMG3 entry {name} exceeds archive bounds: "
                    f"offset={byte_offset}, size={size}, "
                    f"archive_size={archive_size}"
                )

            entry = {
                "type": "file",
                "index": index,
                "name": name,
                "path": name,
                "size": size,
                "offset": byte_offset,
                "raw_size_or_flags": raw_size_or_flags,
                "resource_type": resource_type,
                "sector_offset": sector_offset,
                "used_blocks": used_blocks,
                "flags": flags,
                "padding": padding_count,
                "is_resource": is_resource,
            }
            self.entries.append(entry)
            self.paths.append(
                {
                    "path": name,
                    "size": size,
                    "offset": byte_offset,
                }
            )

    def _get_entry(self, file_path: str) -> dict:
        entry = next(
            (candidate for candidate in self.entries if candidate["path"] == file_path),
            None,
        )
        if entry is None:
            raise FileNotFoundInRPFError(
                f"File not found in IMG3 archive: {file_path}"
            )
        return entry

    def read_file(self, file_path: str) -> bytes:
        """Return the exact unpadded bytes for one IMG3 entry."""
        entry = self._get_entry(file_path)
        offset = entry["offset"]
        size = entry["size"]

        with open(self.rpf_filename, "rb") as archive:
            archive.seek(offset)
            data = archive.read(size)

        if len(data) != size:
            raise FileExtractionError(
                f"Could not read the complete IMG3 entry {file_path}: "
                f"expected {size} bytes, read {len(data)}"
            )
        return data

    def add_file(self, source_file: str, rpf_path: str) -> None:
        """Replace one existing IMG3 entry and rewrite its TOC metadata."""
        source = Path(source_file).expanduser().resolve()
        archive_path = Path(self.rpf_filename)
        if not source.is_file():
            raise FileNotFoundError(f"Source file not found: {source}")
        if source == archive_path:
            raise ValueError("IMG3 replacement file must not be the archive itself")

        payload = source.read_bytes()
        if not payload:
            raise ValueError("IMG3 replacement file must not be empty")

        entry = self._get_entry(rpf_path)
        raw_size_or_flags, resource_type = self._replacement_metadata(entry, payload)
        used_blocks = (len(payload) + IMG3_SECTOR_SIZE - 1) // IMG3_SECTOR_SIZE
        if used_blocks > _MAX_USED_BLOCKS:
            raise RPFParsingError(
                f"IMG3 replacement requires {used_blocks} blocks, exceeding "
                f"the {_MAX_USED_BLOCKS}-block TOC limit"
            )

        allocated_size = used_blocks * IMG3_SECTOR_SIZE
        padding = allocated_size - len(payload)
        flags = (entry["flags"] & ~_PADDING_MASK) | padding
        archive_size = archive_path.stat().st_size

        if used_blocks <= entry["used_blocks"]:
            target_sector = entry["sector_offset"]
        else:
            target_sector = self._align_up(archive_size) // IMG3_SECTOR_SIZE
            if target_sector > _MAX_SECTOR_OFFSET:
                raise RPFParsingError(
                    f"Relocated IMG3 sector 0x{target_sector:X} exceeds "
                    "the supported signed 32-bit range"
                )

        toc = bytearray(self._toc_data)
        struct.pack_into(
            "<IIIHH",
            toc,
            entry["index"] * self.toc_entry_size,
            raw_size_or_flags,
            resource_type,
            target_sector,
            used_blocks,
            flags,
        )
        stored_toc = (
            self._transform_aligned_prefix(bytes(toc), self.aes_key, encrypt_toc)
            if self.encrypted
            else bytes(toc)
        )

        old_capacity = entry["used_blocks"] * IMG3_SECTOR_SIZE
        target_offset = target_sector * IMG3_SECTOR_SIZE
        with archive_path.open("r+b") as archive:
            archive.seek(entry["offset"])
            archive.write(b"\x00" * old_capacity)

            archive.seek(0, os.SEEK_END)
            current_size = archive.tell()
            if target_offset > current_size:
                archive.write(b"\x00" * (target_offset - current_size))

            archive.seek(target_offset)
            archive.write(payload)
            archive.write(b"\x00" * padding)

            archive.seek(IMG3_HEADER_SIZE)
            archive.write(stored_toc)
            archive.flush()
            os.fsync(archive.fileno())

        self.parse()
