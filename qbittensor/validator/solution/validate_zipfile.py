# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import os
import zipfile

import bittensor as bt

from .constants import (
    VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_DEFAULT,
    VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_ENV,
)


def _max_uncompressed_bytes() -> int:
    raw = os.getenv(
        VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_ENV,
        str(VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_DEFAULT),
    ).strip()
    try:
        return int(raw)
    except ValueError:
        bt.logging.warning(
            f"\tInvalid {VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_ENV}={raw!r}; "
            f"using default {VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_DEFAULT} bytes."
        )
        return VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_DEFAULT


def _estimated_uncompressed_bytes(filepath: str) -> int:
    """Sum declared uncompressed sizes from the zip central directory (no extraction)."""
    with zipfile.ZipFile(filepath, "r") as zf:
        return sum(info.file_size for info in zf.infolist())


def validate_zip(filepath: str) -> bool:
    """Validate the locally downloaded file

    Args:
        filepath (str): Location of the downloaded file

    Returns:
        bool: Whether or not the file passes validation
    """
    bt.logging.info("🛠️ Validating .zip file")
    if not zipfile.is_zipfile(filepath):
        bt.logging.error(f"\t❌ Zip validation failed: '{filepath}' is not a zip file.")
        return False

    max_bytes = _max_uncompressed_bytes()
    try:
        uncompressed_bytes = _estimated_uncompressed_bytes(filepath)
    except zipfile.BadZipFile as e:
        bt.logging.error(f"\t❌ Zip validation failed: corrupt archive '{filepath}': {e}")
        return False
    except OSError as e:
        bt.logging.error(f"\t❌ Zip validation failed: could not read '{filepath}': {e}")
        return False

    if uncompressed_bytes > max_bytes:
        bt.logging.error(
            f"\t❌ Zip validation failed: estimated uncompressed size "
            f"{uncompressed_bytes} bytes exceeds limit {max_bytes} bytes."
        )
        return False

    bt.logging.info(f"\t✅ '{filepath}' validated.")
    return True
