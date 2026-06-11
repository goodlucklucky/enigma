# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

"""
Validation for Hardening Quantum Proof solutions.

Reads the miner's result.json from the solution output, loads the
expected peaked state from verif.json (written during setup), and checks
that the submitted peaked_state matches (exact or reversed bit order).
"""

import json
import os

import bittensor as bt

from qbittensor.challenges.hardening_quantum_proof import (
    Solution,
    Verif,
    validate_hqp_solution,
)
from qbittensor.validator.solution.constants import (
    CONTAINER_OUTPUT_DIRNAME,
    CONTAINER_SOLUTION_DIRNAME,
)


def _find_result_json(solution_folder_path: str) -> str | None:
    """Locate result.json in the solution output directory."""
    candidates = [
        os.path.join(solution_folder_path, CONTAINER_OUTPUT_DIRNAME, CONTAINER_SOLUTION_DIRNAME, "result.json"),
        os.path.join(solution_folder_path, CONTAINER_OUTPUT_DIRNAME, "result.json"),
        os.path.join(solution_folder_path, "result.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _find_verif_json(solution_folder_path: str) -> str | None:
    """Locate verif.json in the workspace directory.

    verif.json is written to the workspace root by hqp_setup
    (NOT inside the challenge_input_mount that the miner can see).
    """
    current = os.path.abspath(solution_folder_path)
    for _ in range(5):
        candidate = os.path.join(current, "verif.json")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def run(solution_folder_path: str) -> tuple[bool, str | None]:
    """
    Validate a Hardening Quantum Proof solution by checking the peaked state.

    Returns (success, failure_reason).
    """
    # Load verification data
    verif_path = _find_verif_json(solution_folder_path)
    if not verif_path:
        msg = "verif.json not found -- cannot verify HQP solution"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    try:
        with open(verif_path, "r") as f:
            verif = json.load(f)
    except Exception as e:
        msg = f"Failed to read verif.json: {e}"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    # Load miner's solution
    result_path = _find_result_json(solution_folder_path)
    if not result_path:
        msg = "result.json not found in solution output"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    try:
        with open(result_path, "r") as f:
            solution = json.load(f)
    except Exception as e:
        msg = f"Failed to read result.json: {e}"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    # Delegate to the shared validation logic in qbittensor.challenges.hardening_quantum_proof
    try:
        verif_obj = Verif(peaked_state=verif["peaked_state"])
        sol_obj = Solution(
            status=solution.get("status"),
            peaked_state=solution.get("peaked_state"),
        )
        ok, reason = validate_hqp_solution(sol_obj, verif_obj, require_success_status=True)
    except Exception as e:
        msg = f"Failed to validate HQP solution: {e}"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    if not ok:
        bt.logging.error(f"❌ {reason}")
        return False, reason

    bt.logging.info(
        f"✅ HQP solution verified: peaked_state matches "
        f"(difficulty={verif.get('difficulty', '?')})"
    )
    return True, None
