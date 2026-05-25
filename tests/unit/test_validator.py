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

from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest

from qbittensor.utils.services.challenges import ChallengesClient

from neurons.validator import (
    PRIVATE_MINER_HOTKEY,
    TREASURY_HOTKEY,
    TREASURY_WALLET_AMOUNT,
    Validator,
)


@pytest.fixture
def mock_config():
    """Mock config for validator."""
    config = Validator.config()
    config.neuron.forward_sleep_interval = 5
    config.neuron.epoch_length = 100
    config.neuron.disable_set_weights = False
    config.neuron.moving_average_alpha = 0.1
    config.neuron.axon_off = True
    config.netuid = 1
    config.mock = False
    return config


@pytest.fixture
def mock_validator(mock_config):
    """Create a mock validator instance with mocked dependencies.

    Patch strategy (post platform API consolidation):
    - Primary patches target the canonical locations under qbittensor.*.
    - Secondary patches on neurons.* are still required for classes that
      neurons/validator.py imports locally (the name lookup happens in that
      module's globals). This is transitional technical debt.
    - ChallengesClient is now explicitly patched (and wired as .platform_client)
      so that Validator construction and forward() paths are properly isolated.
    """
    with (
        patch("qbittensor.base.neuron.bt.Wallet") as mock_wallet,
        patch("qbittensor.base.neuron.bt.Subtensor") as mock_subtensor,
        patch("qbittensor.base.neuron.bt.Metagraph") as mock_metagraph,
        patch("qbittensor.base.validator.bt.Dendrite") as mock_dendrite,
        patch("qbittensor.base.validator.bt.Axon") as mock_axon,
        patch("qbittensor.base.neuron.BaseNeuron.sync") as mock_sync,
        patch("qbittensor.base.validator.BaseValidatorNeuron.load_state") as mock_load_state,
        patch("qbittensor.base.neuron.check_config"),
        patch("qbittensor.utils.services.telemetry.TelemetryService") as mock_telemetry_service,
        patch("neurons.validator.TelemetryService"),
        # Explicitly isolate the platform client (new in post-consolidation Validator)
        patch("qbittensor.utils.services.challenges.ChallengesClient") as mock_challenges_client,
        patch("qbittensor.database.db_connection.DBConnection") as mock_db_connection,
        patch("qbittensor.validator.synapse.process_responses.ResponseProcessor") as mock_response_processor_cls,
        patch("qbittensor.validator.solution.solution_container_manager.SolutionContainerManager") as mock_solution_container_manager_cls,
        patch("qbittensor.validator.solution.solution_cross_check.SolutionCrossChecker") as mock_cross_check_cls,
    ):

        mock_wallet.return_value = Mock()
        mock_wallet.return_value.hotkey.ss58_address = "test_hotkey"
        mock_subtensor.return_value = Mock()
        mock_metagraph.return_value = Mock()
        mock_metagraph.return_value.configure_mock(
            hotkeys=["test_hotkey", "hotkey1", "miner_hotkey"],
            last_update=[0, 0, 0],
            uids=np.array([0, 1, 2]),
            axons=[Mock(), Mock(), Mock()],
        )
        mock_metagraph.return_value.n = 3
        mock_subtensor.return_value.metagraph.return_value = mock_metagraph.return_value
        mock_subtensor.return_value.block = Mock(return_value=1000)
        mock_subtensor.return_value.min_allowed_weights = Mock(return_value=1)
        mock_subtensor.return_value.max_weight_limit = Mock(return_value=1000)
        mock_subtensor.return_value.set_weights = Mock(return_value=(True, "success"))
        mock_subtensor.is_hotkey_registered.return_value = True
        mock_subtensor.serve_axon.return_value = None

        mock_dendrite.return_value = Mock()
        mock_dendrite.return_value._session = None
        mock_dendrite.return_value.aclose_session = AsyncMock()
        mock_dendrite.return_value.forward = AsyncMock(return_value=Mock())
        mock_axon.return_value = Mock()

        mock_telemetry = Mock()
        mock_telemetry.record_startup_metrics = Mock()
        mock_telemetry.heartbeat_timer = Mock()
        mock_telemetry.system_metrics_timer = Mock()
        mock_telemetry_service.return_value = mock_telemetry

        mock_db_connection.return_value = Mock()
        mock_db_connection.return_value.db_query.get_miner_submission_statuses.return_value = []
        mock_db_connection.return_value.db_query.get_active_miners.return_value = []
        mock_db_connection.return_value.db_query.prune_old_miner_solutions.return_value = None
        mock_response_processor_cls.return_value = Mock()
        mock_solution_container_manager_cls.return_value = Mock()
        mock_solution_container_manager_cls.return_value.validator_is_busy.return_value = False
        mock_cross_check_cls.return_value = Mock()

        validator = Validator(config=mock_config)

        # Wire key post-consolidation mocks onto the live instance
        validator.database_connection = mock_db_connection.return_value
        validator.response_processor = mock_response_processor_cls.return_value
        validator.platform_client = mock_challenges_client.return_value

        # Give the ChallengesClient mock some sensible defaults used by forward/cross-check paths
        mock_challenges_client.return_value.submit_solution.return_value = None
        mock_challenges_client.return_value.get_next_cross_check_submission.return_value = None
        mock_challenges_client.return_value.get_milestone_price_tao.return_value = 0.1

        validator.sync = Mock()
        validator.load_state = Mock()
        validator.save_state = Mock()
        yield validator


class TestSetWeights:
    """Test cases for weight distribution via the canonical Validator.set_weights().

    This now follows the standard Bittensor pattern (override set_weights, populate
    self.scores, call super().set_weights()). The old calculate_weights + custom timer
    mechanism has been removed.
    """

    def test_set_weights_distributes_maintenance_and_treasury(self, mock_validator):
        """Active miners receive maintenance share; treasury receives the remainder."""
        mock_validator.metagraph.hotkeys = [TREASURY_HOTKEY, "miner1", "miner2"]
        mock_validator.database_connection.db_query.get_active_miners.return_value = ["miner1"]

        with patch("neurons.validator.BaseValidatorNeuron.set_weights") as mock_super:
            mock_validator.set_weights()

        weights = mock_validator.scores
        maintenance_amount = (1.0 - TREASURY_WALLET_AMOUNT) / 2
        treasury_uid = mock_validator.metagraph.hotkeys.index(TREASURY_HOTKEY)

        assert weights[treasury_uid] == TREASURY_WALLET_AMOUNT
        assert weights[1] == maintenance_amount
        assert weights[2] == 0.0
        mock_validator.database_connection.db_query.prune_old_miner_solutions.assert_called_once()
        mock_super.assert_called_once()

    def test_set_weights_includes_private_miner_when_not_in_db(self, mock_validator):
        """Private miner hotkey is always eligible for maintenance weight."""
        mock_validator.metagraph.hotkeys = [TREASURY_HOTKEY, "miner1", PRIVATE_MINER_HOTKEY]
        mock_validator.database_connection.db_query.get_active_miners.return_value = []

        with patch("neurons.validator.BaseValidatorNeuron.set_weights") as mock_super:
            mock_validator.set_weights()

        weights = mock_validator.scores
        maintenance_amount = (1.0 - TREASURY_WALLET_AMOUNT) / 1
        treasury_uid = mock_validator.metagraph.hotkeys.index(TREASURY_HOTKEY)
        private_miner_uid = mock_validator.metagraph.hotkeys.index(PRIVATE_MINER_HOTKEY)

        assert weights[treasury_uid] == TREASURY_WALLET_AMOUNT
        assert weights[private_miner_uid] == maintenance_amount
        mock_super.assert_called_once()


class TestValidator:
    """Test cases for the Validator class."""

    def test_forward_heartbeat_sent_when_due(self, mock_validator):
        """Test that heartbeat is recorded when the timer is due."""
        mock_validator.telemetry_service.record_heartbeat = Mock()

        def check_timer():
            mock_validator.telemetry_service.record_heartbeat()

        mock_validator.telemetry_service.heartbeat_timer.check_timer = Mock(side_effect=check_timer)
        mock_validator.forward()

        mock_validator.telemetry_service.heartbeat_timer.check_timer.assert_called_once()
        mock_validator.telemetry_service.record_heartbeat.assert_called_once()

    def test_forward_heartbeat_not_sent_too_soon(self, mock_validator):
        """Test that heartbeat is not recorded when the timer is not due."""
        mock_validator.telemetry_service.record_heartbeat = Mock()

        mock_validator.forward()

        mock_validator.telemetry_service.heartbeat_timer.check_timer.assert_called_once()
        mock_validator.telemetry_service.record_heartbeat.assert_not_called()
