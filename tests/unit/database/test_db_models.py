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

from datetime import datetime, timezone

import pytest

from qbittensor.database.miner.db_models import MinerSubmission, MinerSubmissionStatus
from qbittensor.database.validator.db_models import ChallengeSolution, MinerMaintenanceIncentive


class TestMinerModels:
    def test_miner_submission_repr(self):
        sub = MinerSubmission(
            upload_id="u1",
            challenge_milestone_id="m1",
            miner_hotkey="5Miner",
            tx_hash="0xabc",
        )
        text = repr(sub)
        assert "MinerSubmission" in text
        assert "u1" in text

    def test_miner_submission_status_repr(self):
        status = MinerSubmissionStatus(
            challenge_milestone_id="m1",
            solution_status="Running",
            validator_hotkey="5Val",
            tx_hash="0xabc",
        )
        text = repr(status)
        assert "MinerSubmissionStatus" in text


class TestValidatorModels:
    def test_challenge_solution_repr(self):
        sol = ChallengeSolution(
            challenge_validation_solution_id="cv1",
            container_id="cid",
            container_name="cname",
            image_id="img",
            challenge_milestone_id="m1",
            absolute_path_to_solution="/tmp/sol",
            submission_id="sub1",
            solution_status="Running",
            tx_hash="0xabc",
            miner_hotkey="5Miner",
        )
        assert "ChallengeSolution" in repr(sol)

    def test_miner_maintenance_incentive_repr(self):
        row = MinerMaintenanceIncentive(
            miner_hotkey="5Miner",
            challenge_milestone_id="m1",
            tx_hash="0xabc",
        )
        assert "MinerMaintenanceIncentive" in repr(row)
