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

import json
import os
import time

import bittensor as bt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Validator requires the signed timestamp to fall within the last hour (with small future skew).
MOCK_TIMESTAMP_MAX_AGE_SECONDS = 3600
MOCK_TIMESTAMP_MAX_FUTURE_SKEW_SECONDS = 60
ENIGMA_MOCK_PUBLIC_KEY = "5a557ee758020954a512c632993637761bdf933a3f59b080981a98e7ba33d191"


def _read_solution_payload(file_path: str) -> dict:
    """Load mock solution JSON from a file path or from result.json / output.txt under a folder."""
    if os.path.isfile(file_path):
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)

    result_json = os.path.join(file_path, "result.json")
    output_txt = os.path.join(file_path, "output.txt")

    if os.path.isfile(result_json):
        with open(result_json, "r", encoding="utf-8") as file:
            return json.load(file)

    with open(output_txt, "r", encoding="utf-8") as file:
        return json.load(file)


def run(file_path: str) -> bool:
    """
    Validate mock challenge output by checking signature + timestamp freshness.

    Loads JSON from ``file_path`` if it is a file; otherwise from ``result.json``
    or ``output.txt`` inside the directory ``file_path``. Verifies an Ed25519
    signature using ``ENIGMA_MOCK_PUBLIC_KEY`` (or the default dev key if unset),
    and requires ``ts`` to be at most one hour in the past (and not far in the future).

    Expected output format:
    {
      "status": "success",
      "signature": "<hex>",
      "payload": "{\"ts\": 1710000000, \"challenge\": \"mock\"}"
    }
    """
    try:
        solution = _read_solution_payload(file_path)
    except Exception as e:
        bt.logging.error(f"❌ Failed to read mock solution output: {e}")
        return False

    status = solution.get("status")
    signature_hex = solution.get("signature")
    payload = solution.get("payload")
    if status != "success" or not signature_hex or not payload:
        bt.logging.error("❌ Invalid mock solution fields (status/signature/payload)")
        return False

    try:
        payload_data = json.loads(payload)
        ts = int(payload_data["ts"])
    except Exception as e:
        bt.logging.error(f"❌ Invalid mock payload JSON: {e}")
        return False

    now = time.time()
    skew_seconds = ts - now
    if skew_seconds > MOCK_TIMESTAMP_MAX_FUTURE_SKEW_SECONDS:
        bt.logging.error(
            "❌ Mock payload timestamp is too far in the future "
            f"({skew_seconds:.0f}s > {MOCK_TIMESTAMP_MAX_FUTURE_SKEW_SECONDS}s skew allowed)"
        )
        return False

    age_seconds = now - ts
    if age_seconds > MOCK_TIMESTAMP_MAX_AGE_SECONDS:
        bt.logging.error(
            "❌ Mock payload timestamp older than one hour "
            f"({age_seconds:.0f}s > {MOCK_TIMESTAMP_MAX_AGE_SECONDS}s)"
        )
        return False

    public_key_hex = os.getenv("ENIGMA_MOCK_PUBLIC_KEY") or ENIGMA_MOCK_PUBLIC_KEY
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), payload.encode("utf-8"))
    except Exception as e:
        bt.logging.error(f"❌ Invalid mock signature verification: {e}")
        return False

    bt.logging.info("✅ Mock challenge output is valid")
    return True
