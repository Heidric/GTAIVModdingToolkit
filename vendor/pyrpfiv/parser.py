import json
import os
import struct
import zlib

from Crypto.Cipher import AES

from .constants import ENTRY_SIZE, HEADER_SIZE, TOC_START_OFFSET
from .crypto import decrypt_toc, extract_aes_key, normalize_aes_key
from .exceptions import (
    FileExtractionError,
    FileNotFoundInRPFError,
    HashesFileNotFoundError,
    InvalidTOCEntryError,
    RPFParsingError,
)


_RPF2_IDENTIFIER = "RPF2"
_RPF3_IDENTIFIER = "RPF3"
_SUPPORTED_IDENTIFIERS = frozenset({_RPF2_IDENTIFIER, _RPF3_IDENTIFIER})

_DIRECTORY_FLAG = 0x80000000
_DIRECTORY_COUNT_MASK = 0x0FFFFFFF
_RPF2_RESOURCE_FLAG_MASK = 0xC0000000
_RPF2_RESOURCE_FLAG_VALUE = 0xC0000000
_RPF2_COMPRESSED_FLAG = 0x40000000
_RPF2_STORED_SIZE_MASK = 0xBFFFFFFF
_RPF2_RESOURCE_OFFSET_MASK = 0x7FFFFF00
_RPF_FILE_OFFSET_MASK = 0x7FFFFFFF
_RSC5_MAGIC = 0x05435352


class RPFParser:
    def __init__(self, rpf_filename, gtaiv_exe_path=None, aes_key=None):
        self.rpf_filename = rpf_filename
        self.gtaiv_exe_path = gtaiv_exe_path
        self.entries = []
        self.known_filenames = {}
        self.paths = []
        self._path_metadata = {}
        self.aes_key = None
        self.identifier = None
        self.toc_size = 0
        self.entry_count = 0
        self.header_unknown = 0
        self.encrypted = False
        self._name_string_table = b""

        if aes_key is not None:
            self.aes_key = normalize_aes_key(aes_key)
        elif self.gtaiv_exe_path:
            self.aes_key = extract_aes_key(self.gtaiv_exe_path)
        else:
            raise ValueError("Either gtaiv_exe_path or aes_key must be provided.")
        self.parse()

    def init_known_filenames(self):
        """Initialize RPF3 filename hashes from ``hashes.ini``."""
        self.known_filenames = {}
        package_dir = os.path.dirname(os.path.abspath(__file__))
        hashes_path = os.path.join(package_dir, "hashes.ini")

        if not os.path.exists(hashes_path):
            raise HashesFileNotFoundError(
                f"hashes.ini not found at {hashes_path}. Some filenames may not be resolved."
            )

        with open(hashes_path, "r", encoding="utf-8") as source:
            for line in source:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                hash_text, name = line.split("=", 1)
                try:
                    self.known_filenames[int(hash_text)] = name
                except ValueError:
                    continue

    def get_name(self, name_hash):
        """Resolve one RPF3 filename hash."""
        return self.known_filenames.get(name_hash, f"0x{name_hash:X}")

    @staticmethod
    def _read_exact(stream, size, description):
        data = stream.read(size)
        if len(data) != size:
            raise RPFParsingError(
                f"{description} is truncated: expected {size} bytes, got {len(data)}"
            )
        return data

    def _rpf2_name(self, name_offset, entry_index):
        if name_offset < 0 or name_offset >= len(self._name_string_table):
            raise InvalidTOCEntryError(
                f"RPF2 TOC entry {entry_index} has invalid name offset 0x{name_offset:X}"
            )
        end = self._name_string_table.find(b"\x00", name_offset)
        if end < 0:
            raise InvalidTOCEntryError(
                f"RPF2 TOC entry {entry_index} name is not null-terminated"
            )
        raw_name = self._name_string_table[name_offset:end]
        try:
            name = raw_name.decode("ascii")
        except UnicodeDecodeError as exc:
            raise InvalidTOCEntryError(
                f"RPF2 TOC entry {entry_index} name is not ASCII"
            ) from exc
        if entry_index == 0:
            return name or "/"
        if not name:
            raise InvalidTOCEntryError(
                f"RPF2 TOC entry {entry_index} has an empty filename"
            )
        if "/" in name or "\\" in name:
            raise InvalidTOCEntryError(
                f"RPF2 TOC entry {entry_index} filename contains a path separator: "
                f"{name!r}"
            )
        return name

    def _parse_directory_entry(self, index, name_field, data1, data2, data3):
        if self.identifier == _RPF2_IDENTIFIER:
            return {
                "type": "directory",
                "name_offset": name_field,
                "flags": data1,
                "content_index": data2 & _RPF_FILE_OFFSET_MASK,
                "content_count": data3 & _DIRECTORY_COUNT_MASK,
                "raw_data3": data3,
                "index": index,
                "name": self._rpf2_name(name_field, index),
            }
        return {
            "type": "directory",
            "name_hash": name_field,
            "content_count": data1,
            "content_index": data2 & _RPF_FILE_OFFSET_MASK,
            "raw_data3": data3,
            "index": index,
            "name": self.get_name(name_field),
        }

    def _parse_file_entry(self, index, name_field, size, raw_offset, data3):
        if self.identifier == _RPF3_IDENTIFIER:
            return {
                "type": "file",
                "name_hash": name_field,
                "size": size,
                "stored_size": size,
                "offset": raw_offset & _RPF_FILE_OFFSET_MASK,
                "raw_offset": raw_offset,
                "compressed": False,
                "resource": False,
                "resource_type": None,
                "resource_flags": None,
                "raw_data3": data3,
                "index": index,
                "name": self.get_name(name_field),
            }

        is_resource = (
            data3 & _RPF2_RESOURCE_FLAG_MASK
        ) == _RPF2_RESOURCE_FLAG_VALUE
        if is_resource:
            offset = raw_offset & _RPF2_RESOURCE_OFFSET_MASK
            stored_size = size
            resource_type = raw_offset & 0xFF
            compressed = False
            resource_flags = data3
        else:
            offset = raw_offset & _RPF_FILE_OFFSET_MASK
            stored_size = data3 & _RPF2_STORED_SIZE_MASK
            resource_type = None
            compressed = bool(data3 & _RPF2_COMPRESSED_FLAG)
            resource_flags = None

        return {
            "type": "file",
            "name_offset": name_field,
            "size": size,
            "stored_size": stored_size,
            "offset": offset,
            "raw_offset": raw_offset,
            "compressed": compressed,
            "resource": is_resource,
            "resource_type": resource_type,
            "resource_flags": resource_flags,
            "raw_data3": data3,
            "index": index,
            "name": self._rpf2_name(name_field, index),
        }

    def parse(self):
        """Parse an RPF2 or RPF3 archive and build its logical file tree."""
        with open(self.rpf_filename, "rb") as stream:
            header_data = self._read_exact(stream, HEADER_SIZE, "RPF header")
            try:
                identifier = header_data[:4].decode("ascii")
            except UnicodeDecodeError as exc:
                raise RPFParsingError("RPF identifier is not ASCII") from exc
            if identifier not in _SUPPORTED_IDENTIFIERS:
                raise RPFParsingError(f"Unsupported RPF identifier: {identifier!r}")

            toc_size, entry_count, unknown, encrypted = struct.unpack(
                "<IiiI", header_data[4:20]
            )
            if entry_count <= 0:
                raise RPFParsingError(f"Invalid RPF entry count: {entry_count}")
            minimum_toc_size = entry_count * ENTRY_SIZE
            if toc_size < minimum_toc_size:
                raise RPFParsingError(
                    f"RPF TOC is too small for {entry_count} entries: {toc_size} bytes"
                )
            if encrypted and toc_size % AES.block_size != 0:
                raise RPFParsingError(
                    "Encrypted RPF TOC size is not a multiple of the AES block size"
                )

            self.identifier = identifier
            self.toc_size = toc_size
            self.entry_count = entry_count
            self.header_unknown = unknown
            self.encrypted = encrypted != 0

            stream.seek(TOC_START_OFFSET)
            toc_data = self._read_exact(stream, toc_size, "RPF TOC")

        if self.encrypted:
            toc_data = decrypt_toc(toc_data, self.aes_key)

        if self.identifier == _RPF3_IDENTIFIER:
            self.init_known_filenames()
            self._name_string_table = b""
        else:
            self.known_filenames = {}
            self._name_string_table = toc_data[minimum_toc_size:]
            if not self._name_string_table:
                raise RPFParsingError("RPF2 TOC does not contain a filename string table")

        self.entries = []
        for index in range(entry_count):
            offset = index * ENTRY_SIZE
            entry_data = toc_data[offset:offset + ENTRY_SIZE]
            if len(entry_data) != ENTRY_SIZE:
                raise InvalidTOCEntryError(
                    f"Invalid TOC entry size at index {index}"
                )
            name_field, data1, data2, data3 = struct.unpack("<IIII", entry_data)
            if data2 & _DIRECTORY_FLAG:
                entry = self._parse_directory_entry(
                    index, name_field, data1, data2, data3
                )
            else:
                entry = self._parse_file_entry(
                    index, name_field, data1, data2, data3
                )
            self.entries.append(entry)

        self.build_file_list()

    def build_file_list(self):
        """Build complete paths by traversing the RPF directory table."""
        directories = [entry for entry in self.entries if entry["type"] == "directory"]
        self.paths = []
        self._path_metadata = {}
        if not directories:
            raise InvalidTOCEntryError("RPF archive does not contain a directory entry")

        root = self.entries[0] if self.entries[0]["type"] == "directory" else directories[0]
        active_directories = set()
        visited_directories = set()

        def walk(directory, parent_path):
            directory_index = directory["index"]
            if directory_index in active_directories:
                raise InvalidTOCEntryError(
                    f"Directory cycle detected at TOC entry {directory_index}"
                )
            if directory_index in visited_directories:
                raise InvalidTOCEntryError(
                    f"Directory TOC entry {directory_index} is referenced more than once"
                )

            content_index = directory["content_index"]
            content_count = directory["content_count"]
            content_end = content_index + content_count
            if (
                content_index < 0
                or content_count < 0
                or content_end > len(self.entries)
            ):
                raise InvalidTOCEntryError(
                    f"Directory TOC entry {directory_index} references invalid child range "
                    f"[{content_index}, {content_end})"
                )

            active_directories.add(directory_index)
            visited_directories.add(directory_index)
            try:
                for child in self.entries[content_index:content_end]:
                    child_path = (
                        f"{parent_path}/{child['name']}" if parent_path else child["name"]
                    )
                    if child["type"] == "directory":
                        walk(child, child_path)
                    elif child["type"] == "file":
                        self.paths.append(
                            {
                                "path": child_path,
                                "size": child["size"],
                                "offset": child["offset"],
                            }
                        )
                        metadata = dict(child)
                        metadata["path"] = child_path
                        self._path_metadata[child_path] = metadata
                    else:
                        raise InvalidTOCEntryError(
                            f"Unsupported TOC entry type at index {child['index']}: "
                            f"{child['type']!r}"
                        )
            finally:
                active_directories.remove(directory_index)

        walk(root, "")

    def get_json_output(self, directories_=None):
        """Return a JSON-compatible representation of the archive."""
        if directories_ is None:
            directories_ = {
                "rpf_info": {
                    "filename": self.rpf_filename,
                    "version": self.identifier,
                    "toc_size": self.toc_size,
                    "entry_count": len(self.entries),
                    "encrypted": self.encrypted,
                },
                "directories": [],
            }
        output = directories_

        files_by_dir = {}
        for entry in self.paths:
            directory_name, separator, file_name = entry["path"].rpartition("/")
            if not separator:
                directory_name = "/"
                file_name = entry["path"]
            files_by_dir.setdefault(directory_name, []).append(
                {
                    "name": file_name,
                    "size": entry["size"],
                    "offset": f"0x{entry['offset']:X}",
                }
            )

        for directory_name, files in files_by_dir.items():
            output["directories"].append(
                {
                    "name": directory_name,
                    "files": sorted(files, key=lambda value: value["name"]),
                }
            )
        return output

    def save_json(self, output_file):
        """Save the RPF representation as JSON."""
        with open(output_file, "w", encoding="utf-8") as output:
            json.dump(self.get_json_output(), output, indent=2)

    def _get_file_entry(self, file_path):
        metadata = getattr(self, "_path_metadata", {}).get(file_path)
        if metadata is not None:
            return metadata

        file_entry = next(
            (entry for entry in self.paths if entry["path"] == file_path),
            None,
        )
        if not file_entry:
            raise FileNotFoundInRPFError(
                f"File not found in RPF archive: {file_path}"
            )

        # Preserve compatibility with callers and tests that construct a minimal
        # parser object containing only the historical path/size/offset fields.
        fallback = dict(file_entry)
        fallback.setdefault("stored_size", fallback["size"])
        fallback.setdefault("compressed", False)
        fallback.setdefault("resource", False)
        fallback.setdefault("resource_type", None)
        fallback.setdefault("resource_flags", None)
        return fallback

    @staticmethod
    def _validate_rsc5(data, file_path, expected_type=None, expected_flags=None):
        if len(data) < 12:
            raise FileExtractionError(f"RSC5 resource is truncated: {file_path}")
        magic, resource_type, flags = struct.unpack_from("<III", data, 0)
        if magic != _RSC5_MAGIC:
            raise FileExtractionError(
                f"RPF resource entry does not contain an RSC5 header: {file_path}"
            )
        if expected_type is not None and resource_type != expected_type:
            raise FileExtractionError(
                f"RSC5 resource type mismatch for {file_path}: "
                f"TOC=0x{expected_type:X}, header=0x{resource_type:X}"
            )
        if expected_flags is not None and flags != expected_flags:
            raise FileExtractionError(
                f"RSC5 resource flags mismatch for {file_path}: "
                f"TOC=0x{expected_flags:08X}, header=0x{flags:08X}"
            )
        return resource_type, flags

    def read_file(self, file_path):
        """Return the logical bytes represented by one RPF file entry."""
        file_entry = self._get_file_entry(file_path)
        offset = file_entry["offset"]
        stored_size = file_entry["stored_size"]
        logical_size = file_entry["size"]
        if offset < 0 or stored_size < 0 or logical_size < 0:
            raise FileExtractionError(
                f"Invalid byte range for {file_path}: offset={offset}, "
                f"stored_size={stored_size}, size={logical_size}"
            )

        with open(self.rpf_filename, "rb") as archive:
            archive.seek(0, os.SEEK_END)
            total_size = archive.tell()
            if offset + stored_size > total_size:
                raise FileExtractionError(
                    f"File entry {file_path} exceeds the RPF archive bounds: "
                    f"offset={offset}, stored_size={stored_size}, archive_size={total_size}"
                )
            archive.seek(offset)
            stored_data = archive.read(stored_size)

        if len(stored_data) != stored_size:
            raise FileExtractionError(
                f"Could not read the complete RPF entry {file_path}: "
                f"expected {stored_size} stored bytes, read {len(stored_data)}"
            )

        if file_entry["compressed"]:
            try:
                data = zlib.decompress(stored_data, -zlib.MAX_WBITS)
            except zlib.error as exc:
                raise FileExtractionError(
                    f"Could not decompress RPF2 entry {file_path}: {exc}"
                ) from exc
        else:
            data = stored_data

        if len(data) != logical_size:
            raise FileExtractionError(
                f"Logical size mismatch for {file_path}: expected {logical_size}, "
                f"got {len(data)}"
            )

        if file_entry["resource"]:
            self._validate_rsc5(
                data,
                file_path,
                expected_type=file_entry["resource_type"],
                expected_flags=file_entry["resource_flags"],
            )
        return data

    def extract_file(self, file_path, output_dir):
        """Extract one RPF entry into ``output_dir``."""
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, os.path.basename(file_path))
        data = self.read_file(file_path)
        with open(output_path, "wb") as output:
            output.write(data)
        return True

    def get_file_capacity(self, file_path):
        """Return the writable stored-byte range at the file's current offset."""
        file_entry = self._get_file_entry(file_path)
        later_offsets = [
            entry["offset"]
            for entry in self.paths
            if entry["offset"] > file_entry["offset"]
        ]
        if later_offsets:
            return min(later_offsets) - file_entry["offset"]
        return os.path.getsize(self.rpf_filename) - file_entry["offset"]

    @staticmethod
    def _align_up(value, alignment=0x800):
        return (value + alignment - 1) & ~(alignment - 1)

    def _prepare_replacement(self, entry, file_path, data):
        if self.identifier == _RPF3_IDENTIFIER:
            return data, len(data), entry["raw_data3"], None

        if entry["resource"]:
            resource_type, resource_flags = self._validate_rsc5(data, file_path)
            if resource_type != entry["resource_type"]:
                raise RPFParsingError(
                    f"Replacement resource type 0x{resource_type:X} does not match "
                    f"RPF2 entry type 0x{entry['resource_type']:X}: {file_path}"
                )
            if (
                resource_flags & _RPF2_RESOURCE_FLAG_MASK
            ) != _RPF2_RESOURCE_FLAG_VALUE:
                raise RPFParsingError(
                    f"Replacement RSC5 flags are not valid RPF2 resource flags: "
                    f"0x{resource_flags:08X}"
                )
            return data, len(data), resource_flags, resource_type

        if entry["compressed"]:
            compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
            stored_data = compressor.compress(data) + compressor.flush()
            data3 = len(stored_data) | _RPF2_COMPRESSED_FLAG
            return stored_data, len(data), data3, None

        return data, len(data), len(data), None

    def _pack_file_toc_entry(
        self,
        toc_entry,
        logical_size,
        stored_size,
        target_offset,
        data3,
        resource_type,
    ):
        if self.identifier == _RPF3_IDENTIFIER:
            return struct.pack(
                "<IIII",
                toc_entry["name_hash"],
                logical_size,
                target_offset,
                data3,
            )

        if toc_entry["resource"]:
            if target_offset & 0xFF:
                raise RPFParsingError(
                    f"RPF2 resource offset is not 0x100-aligned: 0x{target_offset:X}"
                )
            raw_offset = target_offset | resource_type
        else:
            raw_offset = target_offset
            expected_stored_size = data3 & _RPF2_STORED_SIZE_MASK
            if expected_stored_size != stored_size:
                raise RPFParsingError(
                    "RPF2 replacement stored size does not match the TOC metadata"
                )

        return struct.pack(
            "<IIII",
            toc_entry["name_offset"],
            logical_size,
            raw_offset,
            data3,
        )

    def _encrypt_toc(self, toc_data):
        if not self.encrypted:
            return bytes(toc_data)
        encrypted_toc = bytes(toc_data)
        cipher = AES.new(self.aes_key, AES.MODE_ECB)
        for _ in range(16):
            encrypted_toc = cipher.encrypt(encrypted_toc)
        return encrypted_toc

    def add_file(self, source_file, rpf_path):
        """Replace one existing file while preserving the RPF tree and name identity."""
        if not os.path.exists(source_file):
            raise FileNotFoundError(f"Source file not found: {source_file}")

        existing_entry = self._get_file_entry(rpf_path)
        toc_entry = self.entries[existing_entry["index"]]
        if toc_entry["type"] != "file":
            raise RPFParsingError(f"RPF target is not a file entry: {rpf_path}")

        with open(source_file, "rb") as source:
            source_data = source.read()
        stored_data, logical_size, data3, resource_type = self._prepare_replacement(
            toc_entry, rpf_path, source_data
        )
        stored_size = len(stored_data)
        current_capacity = self.get_file_capacity(rpf_path)
        target_offset = existing_entry["offset"]

        with open(self.rpf_filename, "rb+") as archive:
            archive.seek(TOC_START_OFFSET)
            original_toc = self._read_exact(archive, self.toc_size, "RPF TOC")
            if self.encrypted:
                original_toc = decrypt_toc(original_toc, self.aes_key)
            toc_data = bytearray(original_toc)

            if stored_size > current_capacity:
                archive.seek(0, os.SEEK_END)
                end_offset = archive.tell()
                target_offset = self._align_up(end_offset)
                maximum_offset = (
                    _RPF2_RESOURCE_OFFSET_MASK
                    if self.identifier == _RPF2_IDENTIFIER and toc_entry["resource"]
                    else _RPF_FILE_OFFSET_MASK
                )
                if target_offset > maximum_offset:
                    raise RPFParsingError(
                        f"Relocated offset 0x{target_offset:X} exceeds the "
                        f"{self.identifier} file offset range."
                    )
                if target_offset > end_offset:
                    archive.write(b"\x00" * (target_offset - end_offset))

            archive.seek(target_offset)
            archive.write(stored_data)

            entry_data = self._pack_file_toc_entry(
                toc_entry,
                logical_size,
                stored_size,
                target_offset,
                data3,
                resource_type,
            )
            entry_index = toc_entry["index"]
            start = entry_index * ENTRY_SIZE
            toc_data[start:start + ENTRY_SIZE] = entry_data

            archive.seek(TOC_START_OFFSET)
            archive.write(self._encrypt_toc(toc_data))

        existing_entry.update(
            {
                "size": logical_size,
                "stored_size": stored_size,
                "offset": target_offset,
                "raw_offset": (
                    target_offset | resource_type
                    if toc_entry["resource"]
                    else target_offset
                ),
                "resource_flags": data3 if toc_entry["resource"] else None,
                "raw_data3": data3,
            }
        )
        toc_entry.update(existing_entry)
        public_entry = next(
            entry for entry in self.paths if entry["path"] == rpf_path
        )
        public_entry["size"] = logical_size
        public_entry["offset"] = target_offset
