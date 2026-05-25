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


def _code_dir(folder_name: str) -> Path:
    return Path(folder_name) / "code"


def _dockerfile_path(folder_name: str) -> Path | None:
    code_dir = _code_dir(folder_name)
    for name in ("Dockerfile", "dockerfile"):
        path = code_dir / name
        if path.is_file():
            return path
    return None


def _has_dockerfile(folder_name: str) -> bool:
    """Checks whether or not the code includes a dockerfile."""
    if _dockerfile_path(folder_name) is None:
        bt.logging.error("\t❌ Code validation failed: No dockerfile found.")
        return False
    bt.logging.info("\t✅ Found dockerfile")
    return True


def validate_code(folder_name: str) -> bool:
    """Validate code from tarball."""
    bt.logging.info("🛠️ Validating code")
    return _has_dockerfile(folder_name=folder_name)
