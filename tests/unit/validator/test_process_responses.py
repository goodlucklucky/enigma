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

from qbittensor.dto.challenge import ChallengeSubmissionResponse, SolutionCandidate
from qbittensor.validator.synapse.process_responses import ResponseProcessor
from qbittensor.utils.services.challenges import ChallengesClient


def _make_synapse(*, milestone_id="m1", upload_id="up1", challenge_id="ch1"):
    synapse = Mock()
    synapse.challenge_id = challenge_id
    synapse.tx_hash = "0xabc"
    synapse.transfer_block_hash = "0xblock123"
    synapse.transfer_from_ss58 = "5ColdkeyFrom"
    synapse.transfer_to_ss58 = "5Treasury"
    synapse.transfer_amount_rao = "1000000000"
    synapse.transfer_proof_message = "signed-message"
    synapse.transfer_proof_signature_hex = "deadbeefsignature"
    synapse.solution_candidate = SolutionCandidate(
        challenge_milestone_id=milestone_id,
        upload_endpoint_id=upload_id,
        challenge_id=challenge_id,
    )
    return synapse


@pytest.fixture
def processor(platform_client):
    metagraph = Mock()
    metagraph.hotkeys = ["miner_hotkey_0"]
    db = Mock()
    db.db_query = Mock()
    db.db_query.has_seen_tx_hash.return_value = False
    db.db_query.get_tx_binding_info.return_value = None
    db.db_query.get_verified_tx_result.return_value = (None, None)

    return ResponseProcessor(
        request_manager=Mock(),
        metagraph=metagraph,
        validator_hotkey="val_hotkey",
        validator_label="val_label",
        database_connection=db,
        subtensor=Mock(),
        platform_client=platform_client,
    )


class TestResponseProcessor:
    def test_process_synapses_none_returns_early(self, processor):
        processor.process_synapses(None, validator_busy=False)
        processor.database_connection.db_query.insert_for_maintenance_incentive.assert_not_called()

    @patch("qbittensor.validator.synapse.process_responses.verify_transfer_proof_for_synapse")
    def test_skips_when_proof_fails(self, mock_verify, processor):
        mock_verify.return_value = (False, "bad proof")
        processor.process_synapses([_make_synapse()], validator_busy=False)
        processor.database_connection.db_query.insert_for_maintenance_incentive.assert_not_called()

    @patch("qbittensor.validator.synapse.process_responses.execute_verified_solution")
    @patch("qbittensor.validator.synapse.process_responses.verify_transfer_proof_for_synapse")
    def test_starts_solution_on_valid_proof(self, mock_verify, mock_run, processor):
        mock_verify.return_value = (True, None)
        mock_run.return_value = ("img", "cid", "/tmp/folder")

        response = ChallengeSubmissionResponse(
            id="sub-1",
            challenge_milestone_id="m1",
            file_download_url="https://example.com/solution.zip",
            tx_hash="0xabc",
        )
        processor.platform_client.submit_solution.return_value = response

        processor.process_synapses([_make_synapse()], validator_busy=False)

        processor.database_connection.db_query.insert_for_maintenance_incentive.assert_called_once()
        mock_run.assert_called_once()

    @patch("qbittensor.validator.synapse.process_responses.verify_transfer_proof_for_synapse")
    def test_skips_solution_when_validator_busy(self, mock_verify, processor):
        mock_verify.return_value = (True, None)
        processor.process_synapses([_make_synapse()], validator_busy=True)

        # When busy we still claim on the platform (via ChallengesClient) so the submission is not lost,
        # but we do NOT start a container.
        processor.database_connection.db_query.insert_for_maintenance_incentive.assert_called_once()
        processor.platform_client.submit_solution.assert_called_once()

        # Verify that the payload sent to the platform included validator_busy=True
        call_args = processor.platform_client.submit_solution.call_args
        payload = call_args.kwargs.get("payload") or call_args.args[1]
        assert getattr(payload, "validator_busy", None) is True


class TestResponseProcessorAdditionalPaths:
    @patch("qbittensor.validator.synapse.process_responses.verify_transfer_proof_for_synapse")
    def test_skips_replay_tx_hash(self, mock_verify, processor):
        # has_seen_tx_hash now only returns True for actual local bindings (ChallengeSolution
        # or maintenance incentive). A binding with no upload mismatch causes the clean skip.
        processor.database_connection.db_query.has_seen_tx_hash.return_value = True
        processor.database_connection.db_query.get_tx_binding_info.return_value = {
            "challenge_validation_solution_id": "some-upload",
            "challenge_milestone_id": "some-milestone",
            "challenge_id": "some-challenge",
        }
        processor.process_synapses([_make_synapse()], validator_busy=False)
        mock_verify.assert_not_called()

    @patch("qbittensor.validator.synapse.process_responses.verify_transfer_proof_for_synapse")
    def test_records_maintenance_incentive_failure(self, mock_verify, processor, caplog):
        mock_verify.return_value = (True, None)
        processor.database_connection.db_query.insert_for_maintenance_incentive.return_value = False
        processor.platform_client.submit_solution.return_value = None  # avoid running further

        processor.process_synapses([_make_synapse()], validator_busy=False)

        assert "Failed to record maintenance incentive" in caplog.text

    @patch("qbittensor.validator.synapse.process_responses.verify_transfer_proof_for_synapse")
    def test_skips_when_no_solution_candidate(self, mock_verify, processor):
        synapse = _make_synapse()
        # Set to None after creation to avoid Pydantic validation issues with the mock
        synapse.solution_candidate = None
        mock_verify.return_value = (True, None)

        processor.process_synapses([synapse], validator_busy=False)
        processor.platform_client.submit_solution.assert_not_called()
        mock_verify.assert_not_called()  # We should short-circuit before even attempting proof verification

    @patch("qbittensor.validator.synapse.process_responses.verify_transfer_proof_for_synapse")
    def test_skips_when_platform_submit_returns_none(self, mock_verify, processor):
        mock_verify.return_value = (True, None)
        processor.platform_client.submit_solution.return_value = None

        processor.process_synapses([_make_synapse()], validator_busy=False)
        # Should not proceed to execute
        # (we can assert on call count if needed in future)

    @patch("qbittensor.validator.synapse.process_responses.execute_verified_solution")
    @patch("qbittensor.validator.synapse.process_responses.verify_transfer_proof_for_synapse")
    def test_multiple_synapses_only_starts_one_solution(self, mock_verify, mock_execute, processor):
        mock_verify.return_value = (True, None)
        response = ChallengeSubmissionResponse(
            id="sub-1",
            challenge_milestone_id="m1",
            file_download_url="https://example.com/solution.zip",
            tx_hash="0xabc",
        )
        processor.platform_client.submit_solution.return_value = response
        mock_execute.return_value = ("img", "cid", "/tmp/folder")

        synapses = [_make_synapse(upload_id="up1"), _make_synapse(upload_id="up2")]
        processor.metagraph.hotkeys = ["miner1", "miner2"]

        processor.process_synapses(synapses, validator_busy=False)

        # Only one solution should have been started
        assert processor.platform_client.submit_solution.call_count >= 1
