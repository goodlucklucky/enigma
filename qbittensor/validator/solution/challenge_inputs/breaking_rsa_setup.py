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
Challenge setup for Breaking RSA.

Generates a semiprime of the bit-width specified by the milestone's
``configuration.difficulty`` and writes both the challenge input
(for the miner container) and the verification data (for the
validator's post-run check) into the challenge input mount directory.

Files written:
    challenge_input.json  — JSON with {difficulty, num, num_bits} read by the miner
    verif.json            — JSON with {n, p, q} used by the validator to verify the solution
"""

import json
import os
import secrets

import bittensor as bt

from qbittensor.challenges.breaking_rsa import BreakingRSA


def breaking_rsa_setup(
    absolute_output_folder_location: str,
    configuration: dict | None = None,
    platform_client: "object | None" = None,
    milestone_id: str | None = None,
    challenge_id: str | None = None,
) -> str:
    """Generate a Breaking RSA challenge and write it to the mount directory.

    Reads ``difficulty`` (bit-width of the semiprime) from the milestone
    configuration dict. Raises if difficulty is missing — the validator
    must not generate challenges without a known difficulty.
    """
    config = configuration or {}
    difficulty = config.get("difficulty")

    if difficulty is None:
        bt.logging.critical(
            "❌ Breaking RSA setup failed: 'difficulty' missing from milestone configuration. "
            "Cannot generate challenge without a known bit-width. "
            f"Configuration received: {config}"
        )
        raise RuntimeError(
            "Breaking RSA setup requires 'difficulty' in milestone configuration, but none was provided. "
            "Check that the platform API is returning configuration for this milestone."
        )

    num_bits = int(difficulty)
    seed = secrets.randbits(256)

    bt.logging.info(f"🔐 Generating {num_bits}-bit semiprime (seed bit-length={seed.bit_length()})...")
    challenge = BreakingRSA(difficulty=num_bits, num_bits=num_bits)
    problem, verif_data = challenge.generate(seed)
    bt.logging.info(
        f"🔐 Generated {num_bits}-bit semiprime ({len(str(problem.num))} digits)"
    )

    # Challenge input for the miner container
    challenge_path = f"{absolute_output_folder_location}/challenge_input.json"
    with open(challenge_path, "w") as f:
        json.dump(problem.to_dict(), f)

    # Verification data for the validator — written to the PARENT of the
    # mount directory so it is NOT visible inside the miner container.
    # The mount dir (absolute_output_folder_location) is mounted read-only
    # at /challenge_input; anything outside it is inaccessible to the miner.
    workspace_dir = os.path.dirname(absolute_output_folder_location)
    verif = verif_data.to_dict()
    verif["num_bits"] = num_bits
    verif["seed"] = seed
    verif_path = os.path.join(workspace_dir, "verif.json")
    with open(verif_path, "w") as f:
        json.dump(verif, f)

    bt.logging.info(f"🔐 Challenge input written to {challenge_path}")
    return challenge_path
