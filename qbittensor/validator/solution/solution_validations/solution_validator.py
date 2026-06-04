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

import bittensor as bt

from qbittensor.validator.solution.milestones import get_milestone_handlers


def validate_output(solution_folder_path: str, challenge_id: str) -> tuple[bool, str | None]:
    """Run the output validation logic for a given challenge.

    The handler is looked up by challenge_id (per the requirement that handler
    selection is by challenge id).

    Assumes the challenge has already been validated as supported (or handle
    the unknown case gracefully by returning failure).

    Returns:
        (success, failure_reason)
        failure_reason is a descriptive string when success is False, suitable
        for reporting to the platform (includes the actual validation error).
    """
    handlers = get_milestone_handlers(challenge_id)
    if not handlers or not handlers.validate:
        bt.logging.error(f"❌ No validation handler for challenge_id '{challenge_id}'")
        return False, f"No validation handler for challenge_id '{challenge_id}'"
    return handlers.validate(solution_folder_path)
