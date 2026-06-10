# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including but not limited to
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
Lightweight capacity tracker for solution execution.

This was extracted from SolutionContainerManager to keep the "am I allowed to run
another solution right now?" logic small, focused, and easy to test in isolation.

It combines:
- Live count of running Docker containers (provided by the caller)
- In-flight launches (committed but not yet visible in `docker ps`, e.g. during build)

Used to enforce MAX_SOLUTIONS=1 with no gaps between "we decided to run this"
and "the container is actually visible".
"""

from contextlib import contextmanager
from typing import Iterator


class SolutionCapacity:
    """Tracks whether the validator currently has capacity to run a solution.

    This class is intentionally tiny and has no dependencies on Docker, DB, or
    the platform. It only knows about "how many we think are running + how many
    we have just committed to launching".
    """

    def __init__(self, max_solutions: int = 1) -> None:
        self._max_solutions = max_solutions
        self._in_flight_launches: int = 0

    def is_busy(self, docker_running_count: int) -> bool:
        """Return True if we are at or above capacity.

        docker_running_count should come from a fresh `docker ps` (or equivalent)
        for containers matching the validator's label.
        """
        return (docker_running_count + self._in_flight_launches) >= self._max_solutions

    def note_launching_solution(self) -> None:
        """Call immediately when we have decided to run a solution.

        This makes is_busy() return True right away, before the container is
        visible in docker ps (e.g. during image build + `docker run -d`).
        """
        self._in_flight_launches += 1

    def note_launch_completed(self) -> None:
        """Call after the launch attempt completes (success or failure).

        If the launch succeeded, the caller should now have a running container
        that will be counted by docker. If it failed early, this prevents us
        from staying artificially busy.
        """
        if self._in_flight_launches > 0:
            self._in_flight_launches -= 1

    @property
    def in_flight_count(self) -> int:
        """For observability / testing only."""
        return self._in_flight_launches

    @contextmanager
    def launching(self) -> Iterator[None]:
        """Context manager that marks a launch for its duration.

        Usage (preferred at call sites):

            with container_manager.launching():
                image, cid, folder = execute_verified_solution(...)

        This guarantees note_launching / note_completed even if the execute
        raises, and makes the "we have committed to running one" intent obvious.
        """
        self.note_launching_solution()
        try:
            yield
        finally:
            self.note_launch_completed()
