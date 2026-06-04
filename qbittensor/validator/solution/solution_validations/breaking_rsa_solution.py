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
Validation for Breaking RSA solutions.

Reads the miner's result.json from the solution output, loads the
expected factors from verif.json (written during setup), and checks
that p * q == n.
"""

import json
import os

import bittensor as bt

from qbittensor.challenges.breaking_rsa import (
    Solution,
    Verif,
    validate_breaking_rsa_solution,
)
from qbittensor.validator.solution.constants import (
    CHALLENGE_INPUT_DIRNAME,
    CONTAINER_OUTPUT_DIRNAME,
    CONTAINER_SOLUTION_DIRNAME,
)


def _find_result_json(solution_folder_path: str) -> str | None:
    """Locate result.json in the solution output directory."""
    # Standard path: <workspace>/output/solution_artifacts/result.json
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

    verif.json is written to the workspace root by breaking_rsa_setup
    (NOT inside the challenge_input_mount that the miner can see).

    solution_folder_path is typically <workspace>/output/solution_artifacts,
    so we walk up to find verif.json at the workspace root.
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
    Validate a Breaking RSA solution by checking the submitted factors.

    Returns (success, failure_reason).
    """
    # Load verification data
    verif_path = _find_verif_json(solution_folder_path)
    if not verif_path:
        msg = "verif.json not found — cannot verify Breaking RSA solution"
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

    # Delegate the core factor-matching logic to the shared implementation in
    # qbittensor.challenges.breaking_rsa to avoid duplication with
    # BreakingRSA.verify (used by the offline workbench).
    try:
        verif_obj = Verif(n=verif["n"], p=verif["p"], q=verif["q"])
        sol_obj = Solution(
            status=solution.get("status"),
            p=solution.get("p"),
            q=solution.get("q"),
        )
        ok, reason = validate_breaking_rsa_solution(
            sol_obj, verif_obj, prob=None, require_success_status=True
        )
    except Exception as e:
        msg = f"Failed to validate solution factors: {e}"
        bt.logging.error(f"❌ {msg}")
        return False, msg

    if not ok:
        bt.logging.error(f"❌ {reason}")
        return False, reason

    bt.logging.info(
        f"✅ Breaking RSA solution verified: "
        f"{verif.get('num_bits', '?')}-bit semiprime factored correctly"
    )
    return True, None
