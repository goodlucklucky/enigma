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
import urllib.request
from typing import Optional


def download_zip(url: str, folder_name: str) -> Optional[str]:
    """Download the vip file from the url. Validate that the file type is .zip

    Args:
        url (str): the URL where the zip can be downloaded from
        folder_name (str): The name of the folder where the files will go

    Returns:
        On Success | Filepath to the locally downloaded file
        On Failure | None
    """
    bt.logging.info("📥 Downloading .zip file")
    bt.logging.info(f"Downloading zip from URL: {url}")
    try:
        local_filename, _ = urllib.request.urlretrieve(url, filename=f"{folder_name}/solution.zip")
        bt.logging.info(f"\t✅ File downloaded to '{local_filename}'")
        return local_filename
    except Exception as e:
        bt.logging.error(f"\t❌ Failed to download solution file: {e}")
        return None
