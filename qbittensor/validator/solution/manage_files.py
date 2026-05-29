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
from pathlib import Path
import shutil
import time
from .constants import CHALLENGE_SOLTION_PREFIX


"""
Create folder(s) for solution management. Delete them when done.
"""

FOLDER_NAME = ""
# FOLDER_PREFIX: str = CHALLENGE_SOLTION_PREFIX


def setup(validator_label: str, challenge_validation_solution_id: str) -> tuple[str, str]:
    """Setup folder for solution management

    Args:
        validator_label (str): Label for the validator
        challenge_validation_solution_id (str): ID for the challenge validation solution

    Returns:
        tuple[str, str]: The solution tag and folder name
    """
    bt.logging.info("🛠️ Setting up file structure")
    seconds_since_epoch = int(time.time())
    folder_name = f"{validator_label}_{challenge_validation_solution_id}_{seconds_since_epoch}"
    try:
        Path(folder_name).mkdir(exist_ok=True)
        bt.logging.info(f"\t✅ Created folder '{folder_name}'")
    except Exception as e:
        bt.logging.error(f"\t❌ Failed to create file structure: {e}")

    return folder_name, folder_name


def cleanup(folder_name: str) -> None:
    """Remove generated folders"""
    bt.logging.info(f"🗑️ Cleaning up solution folder: {folder_name}")
    try:
        shutil.rmtree(folder_name, ignore_errors=False)
        bt.logging.info(f"🗑️ Successfully removed folder {folder_name}")
    except Exception as e:
        bt.logging.warning(f"⚠️ Failed to remove folder {folder_name}: {e} (ignored)")
