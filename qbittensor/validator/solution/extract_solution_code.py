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

import bittensor as bt
import os
import shutil
import zipfile

from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.exceptions.validation_errors import ValidationErrors
from qbittensor.validator.solution.constants import (
    VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_ENV,
    VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_DEFAULT,
)
from qbittensor.validator.solution.run_solution import _safe_extract_zip


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


def unzip(folder_name: str, source_filepath: str) -> None:
    """Unzip the .zip file into a new folder

    """
    destination: str = f"{folder_name}/code"
    try:
        max_bytes = _max_uncompressed_bytes()
        with zipfile.ZipFile(source_filepath, "r") as zip_ref:
            _safe_extract_zip(zip_ref, destination, max_bytes=max_bytes)
        _flatten_single_top_level_dir(destination)
        bt.logging.info("✅ Code extracted from zip")
    except Exception as e:
        bt.logging.error(f"❌ Failed to unzip the file: {e}")
        raise InvalidSolutionError(message=str(ValidationErrors.INVALID_ZIP))


def _flatten_single_top_level_dir(destination: str) -> None:
    """Flatten extracted content when zip has one root folder."""
    entries = [entry for entry in os.listdir(destination) if entry != "__MACOSX"]
    if len(entries) != 1:
        return

    root_dir_name = entries[0]
    root_dir_path = os.path.join(destination, root_dir_name)

    if os.path.islink(root_dir_path):
        raise RuntimeError(
            f"Refusing to flatten: top-level entry '{root_dir_name}' is a symlink"
        )

    if not os.path.isdir(root_dir_path):
        return

    for item in os.listdir(root_dir_path):
        source = os.path.join(root_dir_path, item)
        target = os.path.join(destination, item)
        if os.path.islink(source):
            raise RuntimeError(
                f"Refusing to flatten: entry '{root_dir_name}/{item}' is a symlink"
            )
        if os.path.isdir(source):
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)

    bt.logging.info(f"🗑️ Removing temporary extraction dir: {root_dir_path}")
    try:
        shutil.rmtree(root_dir_path)
        bt.logging.info(f"🗑️ Removed temporary extraction dir: {root_dir_path}")
    except Exception as e:
        bt.logging.warning(f"⚠️ Failed to remove temporary extraction dir {root_dir_path}: {e}")

    bt.logging.info(f"📂 Flattened top-level extracted directory: {root_dir_name}")
