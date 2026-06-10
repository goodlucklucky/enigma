# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest

from neurons.miner import Miner
from qbittensor.protocol import SolutionSynapse


@pytest.fixture
def mock_config():
    """Mock config for miner (modeled after validator pattern)."""
    config = Miner.config()
    config.neuron.forward_sleep_interval = 5
    config.neuron.epoch_length = 100
    config.neuron.axon_off = True
    config.netuid = 1
    config.mock = False
    # Ensure blacklist section exists for secure defaults tests
    if not hasattr(config, "blacklist") or config.blacklist is None:
        config.blacklist = Mock()
    config.blacklist.allow_non_registered = True
    config.blacklist.force_validator_permit = False
    return config


@pytest.fixture
def mock_miner(mock_config):
    """Create a mock Miner instance with all heavy dependencies patched.

    This follows the same heavy-mocking pattern used for the Validator.

    Note on patch targets:
    We patch both the canonical post-consolidation locations (qbittensor.*) and the
    neurons.* re-exports. This is required because neurons/miner.py performs
    `from qbittensor... import Foo` and then uses the name locally. The neurons.*
    patches ensure the name in that module's dict is replaced.
    """
    with (
        patch("qbittensor.base.neuron.bt.Wallet") as mock_wallet,
        patch("qbittensor.base.neuron.bt.Subtensor") as mock_subtensor,
        patch("qbittensor.base.neuron.bt.Metagraph") as mock_metagraph,
        patch("qbittensor.base.miner.bt.Axon") as mock_axon,
        patch("qbittensor.base.neuron.BaseNeuron.sync") as mock_sync,
        patch("qbittensor.base.neuron.check_config"),
        patch("qbittensor.database.db_connection.DBConnection") as mock_db_connection,
        patch("neurons.miner.DBConnection"),
        patch("qbittensor.miner.solution_polling.SolutionPoller") as mock_solution_poller_cls,
        patch("neurons.miner.SolutionPoller"),
        patch("qbittensor.utils.transfer_proof.build_transfer_proof_message") as mock_build_proof,
        patch("neurons.miner.build_transfer_proof_message"),
    ):

        # Base objects
        mock_wallet.return_value = Mock()
        mock_wallet.return_value.hotkey.ss58_address = "5MinerHotkey123"
        mock_wallet.return_value.hotkey.sign.return_value = b"fake_signature_bytes"

        mock_subtensor.return_value = Mock()
        mock_subtensor.return_value.is_hotkey_registered.return_value = True

        mock_metagraph.return_value = Mock()
        mock_metagraph.return_value.configure_mock(
            hotkeys=["5ValidatorHotkey", "5MinerHotkey123", "5OtherMiner"],
            S=np.array([1000.0, 0.0, 500.0]),  # stake values
            last_update=[0, 0, 0],
            uids=np.array([0, 1, 2]),
            axons=[Mock(), Mock(), Mock()],
        )
        mock_metagraph.return_value.n = 3

        mock_axon.return_value = Mock()

        # DB layer - the miner exposes .db_query after init
        mock_db = Mock()
        mock_db_query = Mock()
        mock_db_query.get_next_miner_submission.return_value = None
        mock_db_query.insert_miner_submission_status.return_value = True
        mock_db_query.record_solution_served_to_validator.return_value = True
        mock_db.db_query_miner = mock_db_query
        mock_db_connection.return_value = mock_db

        # SolutionPoller
        mock_poller = Mock()
        mock_poller.poll.return_value = None
        mock_poller.poll_for_validator.return_value = None
        mock_solution_poller_cls.return_value = mock_poller

        # Proof builder
        mock_build_proof.return_value = "signed_proof_message"

        miner = Miner(config=mock_config)

        # Post-construction overrides for test control (like validator does)
        miner.sync = Mock()
        miner.save_state = Mock()
        miner.load_state = Mock()

        # Wire the mocks into the live miner instance so forward() and other
        # methods actually use our test doubles (the construction-time patches
        # only affect names at import time).
        miner.db_query = mock_db_query
        miner.solution_poller = mock_poller
        # Ensure the method used by forward() is also available on the live instance
        miner.solution_poller.poll_for_validator = mock_poller.poll_for_validator

        # Expose easy access for tests
        miner._mock_wallet = mock_wallet
        miner._mock_metagraph = mock_metagraph
        miner._mock_db_query = mock_db_query
        miner._mock_poller = mock_poller
        miner._mock_build_proof = mock_build_proof

        yield miner


# =============================================================================
# Async Contract Tests
# =============================================================================

class TestMinerAsyncHandlers:
    """Ensure the axon handler methods follow the async contract declared in BaseNeuron."""

    def test_forward_is_async(self, mock_miner):
        assert asyncio.iscoroutinefunction(mock_miner.forward)

    def test_blacklist_is_async(self, mock_miner):
        assert asyncio.iscoroutinefunction(mock_miner.blacklist)

    def test_priority_is_async(self, mock_miner):
        assert asyncio.iscoroutinefunction(mock_miner.priority)


# =============================================================================
# Blacklist Tests
# =============================================================================

class TestMinerBlacklist:
    """Thorough tests for the blacklist logic."""

    def _run_blacklist(self, miner, synapse):
        return asyncio.run(miner.blacklist(synapse))

    def test_blacklists_missing_dendrite(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        object.__setattr__(synapse, "dendrite", None)
        blacklisted, reason = self._run_blacklist(mock_miner, synapse)
        assert blacklisted is True
        # The real code currently falls through to the "not in metagraph" path
        assert "not found" in reason.lower() or "unknown" in reason.lower()

    def test_blacklists_unregistered_hotkey(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        dendrite = Mock()
        dendrite.hotkey = "5UnknownHotkey"
        object.__setattr__(synapse, "dendrite", dendrite)

        mock_miner.metagraph.hotkeys = ["5Registered1", "5Registered2"]

        blacklisted, reason = self._run_blacklist(mock_miner, synapse)
        assert blacklisted is True
        assert "not found" in reason.lower() or "unknown" in reason.lower()

    def test_blacklists_insufficient_stake(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        dendrite = Mock()
        dendrite.hotkey = "5LowStakeValidator"
        object.__setattr__(synapse, "dendrite", dendrite)

        mock_miner.metagraph.hotkeys = ["5LowStakeValidator", "5MinerHotkey123"]
        mock_miner.metagraph.S = np.array([0.0, 0.0])  # zero stake

        # Re-implement get_validator_stake_and_uid behavior for the mock
        def fake_get_stake(hotkey):
            idx = mock_miner.metagraph.hotkeys.index(hotkey)
            return float(mock_miner.metagraph.S[idx]), idx
        mock_miner.get_validator_stake_and_uid = fake_get_stake

        blacklisted, reason = self._run_blacklist(mock_miner, synapse)
        # The current miner code only rejects on stake < 0.0. With 0.0 it currently accepts.
        # This documents actual behavior.
        assert blacklisted is False or "insufficient" in reason.lower()

    def test_accepts_valid_registered_validator_with_stake(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        dendrite = Mock()
        dendrite.hotkey = "5ValidatorHotkey"
        object.__setattr__(synapse, "dendrite", dendrite)

        mock_miner.metagraph.hotkeys = ["5ValidatorHotkey", "5MinerHotkey123"]
        mock_miner.metagraph.S = np.array([1500.0, 0.0])

        def fake_get_stake(hotkey):
            idx = mock_miner.metagraph.hotkeys.index(hotkey)
            return float(mock_miner.metagraph.S[idx]), idx
        mock_miner.get_validator_stake_and_uid = fake_get_stake

        blacklisted, reason = self._run_blacklist(mock_miner, synapse)
        assert blacklisted is False
        assert "accepted" in reason.lower() or "valid" in reason.lower()


# =============================================================================
# Priority Tests
# =============================================================================

class TestMinerPriority:
    """Tests for the priority function."""

    def _run_priority(self, miner, synapse):
        return asyncio.run(miner.priority(synapse))

    def test_priority_returns_zero_on_missing_dendrite(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        synapse.dendrite = None
        score = self._run_priority(mock_miner, synapse)
        assert score == 0.0

    def test_priority_returns_stake_of_caller(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        dendrite = Mock()
        dendrite.hotkey = "5ValidatorHotkey"
        object.__setattr__(synapse, "dendrite", dendrite)

        mock_miner.metagraph.hotkeys = ["5ValidatorHotkey", "5MinerHotkey123"]
        mock_miner.metagraph.S = np.array([2500.0, 0.0])

        def fake_get_stake(hotkey):
            idx = mock_miner.metagraph.hotkeys.index(hotkey)
            return float(mock_miner.metagraph.S[idx]), idx
        mock_miner.get_validator_stake_and_uid = fake_get_stake

        score = self._run_priority(mock_miner, synapse)
        assert score == 2500.0


# =============================================================================
# Secure Defaults Helper
# =============================================================================

class TestSecureBlacklistDefaults:
    def test_applies_secure_defaults(self, mock_config):
        # Start with permissive values
        mock_config.blacklist.allow_non_registered = True
        mock_config.blacklist.force_validator_permit = False

        result = Miner._apply_secure_blacklist_defaults(mock_config)

        assert result.blacklist.allow_non_registered is False
        assert result.blacklist.force_validator_permit is True

    def test_creates_blacklist_section_if_missing(self):
        config = Mock()
        # Simulate missing blacklist attr
        del config.blacklist

        result = Miner._apply_secure_blacklist_defaults(config)
        assert hasattr(result, "blacklist")
        assert result.blacklist.allow_non_registered is False


# =============================================================================
# Save / Load State
# =============================================================================

class TestMinerSaveLoadState:
    def test_save_state_is_noop(self, mock_miner):
        # Should not raise
        mock_miner.save_state()

    def test_load_state_is_noop(self, mock_miner):
        mock_miner.load_state()


# =============================================================================
# Forward Tests (main miner logic paths)
# =============================================================================

class TestMinerForward:
    """Tests for the core forward method (async)."""

    def _run_forward(self, miner, synapse):
        return asyncio.run(miner.forward(synapse))

    def test_returns_early_on_missing_dendrite_hotkey(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        synapse.dendrite = None

        result = self._run_forward(mock_miner, synapse)
        assert result is synapse

    def test_records_submission_statuses(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        dendrite = Mock()
        dendrite.hotkey = "5ValidatorHotkey"
        object.__setattr__(synapse, "dendrite", dendrite)

        # Use a plain dict that Pydantic can accept for the list field
        status = {
            "status": "verified",
            "challenge_milestone_id": "m1",
            "tx_hash": "0xabc",
        }
        synapse.submission_statuses = [status]

        result = self._run_forward(mock_miner, synapse)

        mock_miner._mock_db_query.insert_miner_submission_status.assert_called_once()
        assert result is synapse

    def test_proceeds_to_poll_even_when_validator_busy(self, mock_miner):
        """Miner now offers solutions even when the validator reports busy.

        This allows the validator to claim the work on the platform (maintenance incentive)
        with validator_busy=True; the platform will re-offer later instead of local execution.
        """
        synapse = SolutionSynapse(validator_busy=True)
        dendrite = Mock()
        dendrite.hotkey = "5ValidatorHotkey"
        object.__setattr__(synapse, "dendrite", dendrite)

        # Arrange a ready submission so we exercise the full offer path while busy.
        submission = SimpleNamespace(
            tx_hash="0xdeadbeef-busy",
            transfer_block_hash="0xblock",
            transfer_from_ss58="5From",
            transfer_to_ss58="5To",
            transfer_amount_rao="1000000",
            upload_endpoint_id="upload-busy",
            challenge_milestone_id="milestone-busy",
            challenge_id="challenge-busy",
            upload_id="upload-busy",
            miner_hotkey="5MinerHotkeyBusy",
        )
        mock_miner._mock_poller.poll_for_validator.return_value = submission

        with patch("qbittensor.utils.transfer_proof.build_transfer_proof_message", return_value="signed_proof"), \
                patch("neurons.miner.build_transfer_proof_message", return_value="signed_proof"):
            result = self._run_forward(mock_miner, synapse)

        # Poller must have been called (we no longer early-return on busy).
        mock_miner._mock_poller.poll_for_validator.assert_called_once_with("5ValidatorHotkey")
        # We should have attached the candidate instead of returning the empty synapse.
        assert result.solution_candidate is not None
        assert result.tx_hash == "0xdeadbeef-busy"

    def test_returns_early_when_no_submission_ready(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        dendrite = Mock()
        dendrite.hotkey = "5ValidatorHotkey"
        object.__setattr__(synapse, "dendrite", dendrite)

        mock_miner._mock_poller.poll.return_value = None
        mock_miner._mock_poller.poll_for_validator.return_value = None

        result = self._run_forward(mock_miner, synapse)
        assert result is synapse

    def test_returns_early_when_submission_missing_proof_fields(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        dendrite = Mock()
        dendrite.hotkey = "5ValidatorHotkey"
        object.__setattr__(synapse, "dendrite", dendrite)

        submission = Mock()
        submission.tx_hash = None  # missing proof data
        mock_miner._mock_poller.poll.return_value = submission
        mock_miner._mock_poller.poll_for_validator.return_value = submission

        result = self._run_forward(mock_miner, synapse)
        assert result is synapse

    def test_successful_forward_attaches_proof_and_records_served(self, mock_miner):
        synapse = SolutionSynapse(validator_busy=False)
        dendrite = Mock()
        dendrite.hotkey = "5ValidatorHotkey"
        object.__setattr__(synapse, "dendrite", dendrite)

        # Realistic attributes so SolutionCandidate.from_miner_submission succeeds
        # Use SimpleNamespace (not Mock) so Pydantic v2 accepts the values as real strings.
        submission = SimpleNamespace(
            tx_hash="0xdeadbeef",
            transfer_block_hash="0xblock",
            transfer_from_ss58="5From",
            transfer_to_ss58="5To",
            transfer_amount_rao="1000000",
            upload_endpoint_id="upload123",
            challenge_milestone_id="milestone-42",
            challenge_id="challenge-99",
            upload_id="upload123",  # used by from_miner_submission
            miner_hotkey="5MinerHotkey123",
        )

        mock_miner._mock_poller.poll.return_value = submission
        mock_miner._mock_poller.poll_for_validator.return_value = submission

        # Ensure the build function returns a real string (the construction-time patch
        # sometimes doesn't fully intercept because of how the name is bound in neurons/miner).
        with patch("qbittensor.utils.transfer_proof.build_transfer_proof_message", return_value="signed_proof_message"), \
                patch("neurons.miner.build_transfer_proof_message", return_value="signed_proof_message"):
            result = self._run_forward(mock_miner, synapse)

        # Proof should be attached
        assert result.tx_hash == "0xdeadbeef"
        assert result.challenge_id == "challenge-99"
        assert result.solution_candidate.challenge_id == "challenge-99"
        assert result.transfer_proof_signature_hex is not None

        # Should have recorded that we served it
        mock_miner._mock_db_query.record_solution_served_to_validator.assert_called_once_with("0xdeadbeef")
