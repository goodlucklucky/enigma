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
from qbittensor.validator.solution.challenge_inputs.breaking_rsa_setup import breaking_rsa_setup
from qbittensor.validator.solution.solution_validations.breaking_rsa_solution import run as run_breaking_rsa
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.exceptions.validation_errors import ValidationErrors


@dataclass(frozen=True)
class MilestoneHandlers:
    """Holds the optional setup and validation functions for a given milestone.

    Setup signature:   (solution_folder_path: str, configuration: dict) -> str
    Validate signature: (solution_folder_path: str) -> tuple[bool, str | None]
    """

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

# Registered challenge IDs (UUIDs from the platform, or synthetic for staging).
MOCK_CHALLENGE_ID = "b513b40c-ecab-4d9c-b146-e1ffb357113b"
BREAKING_RSA_CHALLENGE_ID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"

MILESTONE_REGISTRY: dict[str, MilestoneHandlers] = {
    # Mock challenge used for testing private/public key (Ed25519) validation.
    # Handlers are registered by challenge_id (the UUID from the platform).
    # Adding support for a new challenge_id requires updating this registry (and redeploy).
    MOCK_CHALLENGE_ID: MilestoneHandlers(
        setup=mock_solution_setup,
        validate=run_mock_solution,
    ),
    # Breaking RSA (semiprime factorization) challenge.
    # Requires gmpy2 for prime generation during setup.
    BREAKING_RSA_CHALLENGE_ID: MilestoneHandlers(
        setup=breaking_rsa_setup,
        validate=run_breaking_rsa,
    ),
}


def get_milestone_handlers(challenge_id: str) -> MilestoneHandlers | None:
    """Return the handlers for a challenge id, or None if unknown."""
    return MILESTONE_REGISTRY.get(challenge_id)


def assert_milestone_supported(challenge_id: str) -> None:
    """
    Enforce that a challenge id has registered handlers for both setup and validation.

    Per design, every runnable milestone must have these handlers.
    If not present, the solution cannot be executed.
    This check should be performed very early (before downloading or building images).
    The lookup is by challenge_id because handlers are selected per challenge.
    """
    handlers = get_milestone_handlers(challenge_id)
    if not handlers:
        raise InvalidSolutionError(
            message=ValidationErrors.INVALID_PROGRAM.value,
            details=f"No handlers registered for challenge_id '{challenge_id}'.",
        )

    if not handlers.setup:
        raise InvalidSolutionError(
            message=ValidationErrors.INVALID_PROGRAM.value,
            details=f"Challenge_id '{challenge_id}' is missing a setup handler.",
        )

    if not handlers.validate:
        raise InvalidSolutionError(
            message=ValidationErrors.INVALID_PROGRAM.value,
            details=f"Challenge_id '{challenge_id}' is missing a validation handler.",
        )
