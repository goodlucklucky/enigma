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

from qbittensor.challenges.mock_challenge import (
    Solution as MockSolution,
    Verif as MockVerif,
    validate_mock_solution,
)

# Default for the platform's mock challenge (can be overridden via env).
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


def run(file_path: str) -> tuple[bool, str | None]:
    """
    Validate mock challenge output by checking signature + timestamp freshness.

    Returns (success, failure_reason).
    The failure_reason (when present) is a human-readable explanation suitable
    for reporting to the platform.
    """
    try:
        solution = _read_solution_payload(file_path)
    except Exception as e:
        msg = f"Failed to read mock solution output: {e}"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    status = solution.get("status")
    signature_hex = solution.get("signature")
    payload = solution.get("payload")
    if status != "success" or not signature_hex or not payload:
        msg = "Invalid mock solution fields (status/signature/payload)"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    public_key_hex = os.getenv("ENIGMA_MOCK_PUBLIC_KEY") or ENIGMA_MOCK_PUBLIC_KEY

    # Delegate crypto + timestamp checks to the shared implementation in
    # qbittensor.challenges.mock_challenge (used by MockChallenge.verify for the
    # offline workbench too). This eliminates the previous duplicated logic
    # (and risk of the two getting out of sync).
    try:
        sol = MockSolution(status=status, signature=signature_hex, payload=payload)
        ver = MockVerif(public_key_hex=public_key_hex)
        success, reason = validate_mock_solution(sol, ver)
    except Exception as e:
        msg = f"Invalid mock signature verification: {e}"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    if not success:
        bt.logging.error(f"❌ {reason}")
        return False, reason

    bt.logging.info("✅ Mock challenge output is valid")
    return True, None
