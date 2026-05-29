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
from qbittensor.database.miner.db_query import DBQueryMiner
from qbittensor.database.miner.db_models import MinerSubmission


class SolutionPoller:

    def __init__(self, db_query: DBQueryMiner):
        self.db_query: DBQueryMiner = db_query

    def poll(self) -> MinerSubmission | None:
        """Return the current submission from the local miner_submissions table (global next)."""
        bt.logging.info("🔊 Polling for a solution candidate")
        return self.db_query.get_next_miner_submission()

    def poll_for_validator(self, validator_hotkey: str) -> MinerSubmission | None:
        """
        Return the next submission that has not yet been offered to this specific validator.
        This enables proper per-validator deduplication.
        """
        bt.logging.info(f"🔊 Polling for a solution candidate not yet offered to {validator_hotkey[:8]}...")
        return self.db_query.get_next_miner_submission_for_validator(validator_hotkey)
