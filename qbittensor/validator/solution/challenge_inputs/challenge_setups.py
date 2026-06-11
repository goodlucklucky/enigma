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

from __future__ import annotations

import bittensor as bt

from qbittensor.validator.solution.milestones import get_milestone_handlers
from qbittensor.utils.services.challenges import ChallengesClient


def run_challenge_setup(
    challenge_id: str,
    solution_folder_path: str,
    configuration: dict | None = None,
    platform_client: ChallengesClient | None = None,
    milestone_id: str | None = None,
) -> str:
    """Run the input setup logic for a given challenge.

    Args:
        challenge_id: The platform challenge id (UUID). Used to look up the
            registered handlers for this challenge.
        solution_folder_path: Absolute path to the challenge input directory.
        configuration: Milestone configuration from the platform API
            (e.g. {"difficulty": 320, "max_solution_runtime": 14400}).
        platform_client: Optional authenticated client (used by some setups
            such as HQP to fetch protected per-validator metadata).
        milestone_id: The specific milestone ID (UUID). Passed explicitly to
            setup implementations (e.g. HQP) so they can fetch per-milestone
            private data without needing it embedded in the configuration dict.

    Assumes the challenge has already been validated as supported via assert_milestone_supported().
    """
    handlers = get_milestone_handlers(challenge_id)
    if not handlers or not handlers.setup:
        bt.logging.error(f"❌ No setup handler for challenge_id '{challenge_id}'")
        return False

    cfg = configuration or {}
    return handlers.setup(
        solution_folder_path,
        cfg,
        platform_client=platform_client,
        milestone_id=milestone_id,
        challenge_id=challenge_id,
    )
