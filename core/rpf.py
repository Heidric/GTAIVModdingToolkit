"""Application-facing GTA IV archive parser facade.

The toolkit intentionally imports archive support through this module instead
of using format-specific implementations directly. That keeps compatibility
logic isolated from the UI layer and makes parser replacement safer.
"""

from pathlib import Path

from core.img3 import IMG3Parser as _IMG3Parser
from vendor.pyrpfiv import RPFParser as _VendorRPFParser
from vendor.pyrpfiv.exceptions import AESKeyExtractionError, PyrpfivError


def RPFParser(archive_filename, gtaiv_exe_path=None, aes_key=None):
    """Open an RPF3 or IMG3 archive through a compatible parser interface."""
    parser_type = (
        _IMG3Parser
        if Path(archive_filename).suffix.casefold() == ".img"
        else _VendorRPFParser
    )
    return parser_type(
        archive_filename,
        gtaiv_exe_path=gtaiv_exe_path,
        aes_key=aes_key,
    )


__all__ = ["AESKeyExtractionError", "PyrpfivError", "RPFParser"]
