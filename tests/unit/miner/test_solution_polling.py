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

from unittest.mock import Mock

from qbittensor.miner.solution_polling import SolutionPoller
from qbittensor.database.miner.db_models import MinerSubmission


class TestSolutionPoller:
    def test_poll_returns_next_submission(self):
        submission = Mock(spec=MinerSubmission)
        db_query = Mock()
        db_query.get_next_miner_submission.return_value = submission
        poller = SolutionPoller(db_query)
        assert poller.poll() is submission
        db_query.get_next_miner_submission.assert_called_once()

    def test_poll_returns_none_when_empty(self):
        db_query = Mock()
        db_query.get_next_miner_submission.return_value = None
        poller = SolutionPoller(db_query)
        assert poller.poll() is None
