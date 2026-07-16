"""Application-facing RPF parser facade.

The toolkit intentionally imports RPF support through this module instead of
using a third-party package directly. That keeps GTA IV archive compatibility
logic isolated from the UI layer and makes future parser replacement safer.
"""

from vendor.pyrpfiv import RPFParser
from vendor.pyrpfiv.exceptions import AESKeyExtractionError, PyrpfivError

__all__ = ["AESKeyExtractionError", "PyrpfivError", "RPFParser"]
