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
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.exceptions.validation_errors import ValidationErrors


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

MOCK_MILESTONE_ID = "9c869f1e-66da-4ebe-9fe1-4f5a2b9c8228"

MILESTONE_REGISTRY: dict[str, MilestoneHandlers] = {
    # Mock challenge used for testing private/public key (Ed25519) validation
    MOCK_MILESTONE_ID: MilestoneHandlers(
        setup=mock_solution_setup,
        validate=run_mock_solution,
    ),
}


def get_milestone_handlers(milestone_id: str) -> MilestoneHandlers | None:
    """Return the handlers for a milestone, or None if the milestone is unknown."""
    return MILESTONE_REGISTRY.get(milestone_id)


def assert_milestone_supported(challenge_milestone_id: str) -> None:
    """
    Enforce that a milestone has registered handlers for both setup and validation.

    Per design, every runnable milestone must have these handlers.
    If not present, the solution cannot be executed.
    This check should be performed very early (before downloading or building images).
    """
    handlers = get_milestone_handlers(challenge_milestone_id)
    if not handlers:
        raise InvalidSolutionError(
            message=ValidationErrors.INVALID_PROGRAM.value,
            details=f"No handlers registered for milestone '{challenge_milestone_id}'. "
                    "This milestone is not supported for execution.",
        )

    if not handlers.setup:
        raise InvalidSolutionError(
            message=ValidationErrors.INVALID_PROGRAM.value,
            details=f"Milestone '{challenge_milestone_id}' is missing a challenge setup handler.",
        )

    if not handlers.validate:
        raise InvalidSolutionError(
            message=ValidationErrors.INVALID_PROGRAM.value,
            details=f"Milestone '{challenge_milestone_id}' is missing an output validation handler.",
        )
