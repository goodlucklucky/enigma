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

"""
Value objects for carrying solution execution identity and state.

SolutionExecution holds the stable identity for a solution execution:
the claim fields from the platform + the DB primary key (solution_id).
It is only constructed after create_challenge_solution succeeds.

SolutionPostProcessInfo is the frozen post-completion carrier. It embeds a
SolutionExecution for the overlapping identity fields, plus the runtime
details needed for extract/validate/report/clean (container_name, workspace,
image_id). It is built in the container manager from docker discovery + DB row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SolutionPostProcessInfo:
    """Frozen snapshot for post-completion processing of a solution.

    Embeds a SolutionExecution (for the stable identity/claim fields that
    overlap) plus the runtime details (container_name, workspace_path, image_id)
    needed after the container has exited.

    Used by the container manager for _extract_outputs, _validate_and_report,
    and _clean_up. All fields are non-optional for completed solutions.
    """

    execution: SolutionExecution
    container_name: str
    workspace_path: str
    image_id: str

    # Convenience delegates for the identity fields (keeps call sites simple)
    @property
    def id(self) -> str:
        return self.execution.solution_id

    @property
    def submission_id(self) -> str:
        return self.execution.submission_id

    @property
    def challenge_milestone_id(self) -> str:
        return self.execution.challenge_milestone_id

    @property
    def challenge_id(self) -> str:
        return self.execution.challenge_id


@dataclass(frozen=True)
class SolutionExecution:
    """Value object for the stable identity of a solution execution.

    Contains exactly the required fields (the claim/identity values from the
    platform submission + the solution_id PK obtained from create_challenge_solution).

    Construct via SolutionExecution.create(...) or the dataclass constructor.
    The dataclass is frozen (immutable).
    """

    solution_id: str
    tx_hash: str
    submission_id: str
    challenge_validation_solution_id: str  # the upload_endpoint_id / "file upload" id
    challenge_id: str
    challenge_milestone_id: str
    miner_hotkey: str
    download_url: str

    @classmethod
    def create(
        cls,
        tx_hash: str,
        submission_id: str,
        challenge_validation_solution_id: str,
        challenge_id: str,
        challenge_milestone_id: str,
        miner_hotkey: str,
        download_url: str,
        solution_id: str,
    ) -> "SolutionExecution":
        """Constructor for the full set of required fields."""
        return cls(
            tx_hash=tx_hash,
            submission_id=submission_id,
            challenge_validation_solution_id=challenge_validation_solution_id,
            challenge_id=challenge_id,
            challenge_milestone_id=challenge_milestone_id,
            miner_hotkey=miner_hotkey,
            download_url=download_url,
            solution_id=solution_id,
        )

    def ensure_solution_id(self) -> str:
        return self.solution_id

    def to_labels(self) -> dict[str, str]:
        """Produce the --label key=value pairs for `docker run --label`.

        Always includes the solution_id for stable correlation.
        """
        return {
            "submission_id": self.submission_id,
            "tx_hash": self.tx_hash,
            "challenge_validation_solution_id": self.challenge_validation_solution_id,
            "solution_id": self.solution_id,
        }

    def __repr__(self) -> str:
        return (
            f"SolutionExecution(id={self.solution_id}, sub={self.submission_id}, "
            f"milestone={self.challenge_milestone_id}, tx={self.tx_hash[:12]}..., "
            f"miner={self.miner_hotkey})"
        )

    def __str__(self) -> str:
        return self.__repr__()
