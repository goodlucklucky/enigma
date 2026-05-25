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

from dataclasses import dataclass
from typing import Callable, Optional

from qbittensor.validator.solution.challenge_inputs.mock_solution_setup import mock_solution_setup
from qbittensor.validator.solution.solution_validations.mock_solution import run as run_mock_solution


@dataclass(frozen=True)
class MilestoneHandlers:
    """Holds the optional setup and validation functions for a given milestone."""

    setup: Optional[Callable] = None
    validate: Optional[Callable] = None


# =============================================================================
# Central Registry
# =============================================================================
#
# This is the single source of truth for which milestones are supported
# and what their input setup + output validation functions are.
#
# Using one registry prevents the previous problem of having two separate maps
# (challenge_setup_map and solution_map) that had to be kept in sync manually.
# =============================================================================

MILESTONE_REGISTRY: dict[str, MilestoneHandlers] = {
    # Mock challenge used for testing private/public key (Ed25519) validation
    "75358eeb-0345-4938-8ea6-7e7e657487c7": MilestoneHandlers(
        setup=mock_solution_setup,
        validate=run_mock_solution,
    ),
}


def get_milestone_handlers(milestone_id: str) -> MilestoneHandlers | None:
    """Return the handlers for a milestone, or None if the milestone is unknown."""
    return MILESTONE_REGISTRY.get(milestone_id)
