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

from unittest.mock import Mock, patch

import pytest

from qbittensor.dto.challenge import ChallengeSubmissionRead
from qbittensor.validator.solution.solution_cross_check import SolutionCrossChecker


@pytest.fixture
def cross_checker(platform_client):
    with patch("qbittensor.validator.solution.solution_cross_check.Timer") as mock_timer:
        mock_timer.return_value = Mock()
        checker = SolutionCrossChecker(
            validator_label="val_label",
            platform_client=platform_client,
            solution_container_manager=Mock(),
            database_connection=Mock(),
        )
    checker.database_connection.db_query = Mock()
    return checker


class TestSolutionCrossChecker:
    def test_run_skips_when_busy(self, cross_checker):
        cross_checker.solution_container_manager.validator_is_busy.return_value = True
        cross_checker.platform_client.get_next_cross_check_submission.return_value = None
        cross_checker.run()
        cross_checker.platform_client.get_next_cross_check_submission.assert_not_called()

    def test_run_starts_cross_check_solution(self, cross_checker):
        cross_checker.solution_container_manager.validator_is_busy.return_value = False

        submission = ChallengeSubmissionRead(
            id="cc-1",
            address="5Miner",
            challenge_milestone_id="m1",
            challenge_id="ch-1",
            challenge_preparation_id=None,
            upload_endpoint_id="upload-xyz",
            tx_hash="0xabc",
            file_download_url="https://example.com/z.zip",
            transfer_block_hash="0xblock",
            transfer_from_ss58="5From",
            transfer_to_ss58="5Treasury",
            transfer_amount_rao="1000000000",
            transfer_proof_message="msg",
            transfer_proof_signature_hex="sig",
        )
        cross_checker.platform_client.get_next_cross_check_submission.return_value = submission

        with patch("qbittensor.validator.solution.solution_cross_check.execute_verified_solution") as mock_run:
            mock_run.return_value = ("img", "cid", "/tmp/f")
            cross_checker.run()

        mock_run.assert_called_once()
        cross_checker.database_connection.db_query.insert_for_maintenance_incentive.assert_called_once()

    def test_run_cross_check_without_challenge_id(self, cross_checker):
        cross_checker.solution_container_manager.validator_is_busy.return_value = False

        submission = ChallengeSubmissionRead(
            id="cc-2",
            address="5Miner",
            challenge_milestone_id="m1",
            challenge_id=None,
            challenge_preparation_id=None,
            upload_endpoint_id="upload-xyz",
            tx_hash="0xdef",
            file_download_url="https://example.com/z.zip",
            transfer_block_hash="0xblock",
            transfer_from_ss58="5From",
            transfer_to_ss58="5Treasury",
            transfer_amount_rao="1000000000",
            transfer_proof_message="msg",
            transfer_proof_signature_hex="sig",
        )
        cross_checker.platform_client.get_next_cross_check_submission.return_value = submission

        with patch("qbittensor.validator.solution.solution_cross_check.execute_verified_solution") as mock_run:
            mock_run.return_value = ("img", "cid", "/tmp/f")
            cross_checker.run()

        mock_run.assert_called_once_with(
            db_conn=cross_checker.database_connection,
            platform_client=cross_checker.platform_client,
            validator_label="val_label",
            download_url=submission.file_download_url,
            challenge_id=None,
            challenge_milestone_id=submission.challenge_milestone_id,
            challenge_validation_solution_id=submission.id,
            submission_id=submission.id,
            tx_hash=submission.tx_hash,
            miner_hotkey=submission.address,
        )
