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
Challenge setup for Hardening Quantum Proof.

Fetches the challenge metadata for this milestone by calling the authenticated
platform client and writes the challenge input (for the
miner container) and the verification data (for the validator's post-run check).
"""

import json
import os

import bittensor as bt

from qbittensor.challenges.hardening_quantum_proof import Problem


def hqp_setup(
    absolute_output_folder_location: str,
    configuration: dict | None = None,
    platform_client: "object | None" = None,
    milestone_id: str | None = None,
    challenge_id: str | None = None,
) -> str:
    """Fetch an HQP circuit from the platform and write it to the mount directory.

    The milestone_id is passed explicitly by the caller (preferred).
    A fallback to configuration["milestone_id"] is kept only for direct/test calls.

    The actual download is performed exclusively by calling
    platform_client.get_milestone_metadata(milestone_id) using the authenticated
    client (validator JWT + client mapping).

    The returned metadata contains the circuit QASM ("circuit") and the secret
    peaked state ("result").
    """
    from qbittensor.utils.services.challenges import ChallengesClient

    config = configuration or {}

    difficulty = config.get("difficulty")
    if difficulty is None:
        bt.logging.critical(
            "HQP setup failed: 'difficulty' missing from milestone configuration. "
            f"Configuration received: {config}"
        )
        raise RuntimeError(
            "HQP setup requires 'difficulty' in milestone configuration, but none was provided."
        )
    difficulty = int(difficulty)

    mid = milestone_id or config.get("milestone_id")
    if not mid:
        bt.logging.critical(
            "HQP setup failed: no milestone_id provided. "
            f"milestone_id param={milestone_id}, config keys={list(config.keys())}"
        )
        raise RuntimeError(
            "HQP setup requires a milestone_id (passed explicitly to the setup function)."
        )

    bt.logging.info(f"Downloading HQP metadata for difficulty {difficulty}...")

    if not platform_client or not isinstance(platform_client, ChallengesClient):
        bt.logging.critical(
            "HQP setup failed: authenticated platform_client is required to fetch "
            "milestone metadata."
        )
        raise RuntimeError(
            "HQP setup requires an authenticated ChallengesClient (with validator "
            "credentials) to call get_milestone_metadata()."
        )

    try:
        metadata = platform_client.get_milestone_metadata(mid)
    except Exception as e:
        bt.logging.critical(f"Failed to download HQP metadata via authenticated client: {e}")
        raise RuntimeError(f"Failed to download HQP metadata: {e}") from e

    qasm_content = metadata.get("circuit")
    if not qasm_content:
        bt.logging.critical(
            "HQP setup failed: 'circuit' not found in metadata. "
            "Cannot generate challenge without a QASM circuit."
        )
        raise RuntimeError(
            "HQP metadata is missing 'circuit'. "
            "Check that the metadata has the correct format."
        )

    peaked_state = metadata.get("result")
    if not peaked_state:
        bt.logging.critical(
            "HQP setup failed: 'result' not found in metadata. "
            "Cannot verify miner solutions without the expected peaked state."
        )
        raise RuntimeError(
            "HQP metadata is missing 'result'. "
            "Check that the metadata has the correct format."
        )

    bt.logging.info(
        f"Downloaded HQP circuit ({len(qasm_content)} bytes, difficulty={difficulty})"
    )

    qasm_path = os.path.join(absolute_output_folder_location, "circuit.qasm")
    with open(qasm_path, "w") as f:
        f.write(qasm_content)

    problem = Problem(
        difficulty=difficulty,
        qasm_file="/challenge_input/circuit.qasm",
    )
    challenge_input = {
        "challenge_id": challenge_id or "",
        **problem.to_dict(),
    }
    challenge_path = os.path.join(absolute_output_folder_location, "challenge_input.json")
    with open(challenge_path, "w") as f:
        json.dump(challenge_input, f)

    workspace_dir = os.path.dirname(absolute_output_folder_location)
    verif_data = {
        "peaked_state": peaked_state,
        "difficulty": difficulty,
    }
    verif_path = os.path.join(workspace_dir, "verif.json")
    with open(verif_path, "w") as f:
        json.dump(verif_data, f)

    bt.logging.info(f"HQP challenge input written to {challenge_path}")
    return challenge_path
