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

import pytest
import time
import numpy as np
from unittest.mock import Mock, patch, MagicMock

import bittensor as bt
import qbittensor
from neurons.validator import Validator, TREASURY_HOTKEY


@pytest.fixture
def mock_config():
    """Mock config for validator."""
    # Use the validator's config method to get proper config
    config = Validator.config()
    config.neuron.forward_sleep_interval = 5
    config.neuron.epoch_length = 100
    config.neuron.disable_set_weights = False
    config.neuron.moving_average_alpha = 0.1
    config.neuron.axon_off = True
    config.netuid = 1
    config.mock = False  # Use real wallet but mock it
    return config


@pytest.fixture
def mock_validator(mock_config):
    """Create a mock validator instance with mocked dependencies."""
    with patch('qbittensor.base.neuron.bt.Wallet') as mock_wallet, \
         patch('qbittensor.base.neuron.bt.Subtensor') as mock_subtensor, \
         patch('qbittensor.base.neuron.bt.Metagraph') as mock_metagraph, \
         patch('qbittensor.base.validator.bt.Dendrite') as mock_dendrite, \
         patch('qbittensor.base.validator.bt.Axon') as mock_axon, \
         patch('qbittensor.base.neuron.BaseNeuron.sync') as mock_sync, \
         patch('qbittensor.base.validator.BaseValidatorNeuron.load_state') as mock_load_state, \
         patch('neurons.validator.MetricsService') as mock_metrics_service:

        # Mock the dependencies
        mock_wallet.return_value = Mock()
        mock_wallet.return_value.hotkey.ss58_address = 'test_hotkey'
        mock_subtensor.return_value = Mock()
        mock_metagraph.return_value = Mock()
        mock_metagraph.return_value.configure_mock(
            hotkeys=['test_hotkey', 'hotkey1', 'test_treasury_hotkey'],
            last_update=[0, 0, 0],
            uids=np.array([0, 1, 2])
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
        mock_axon.return_value = Mock()

        mock_metrics_service.return_value = Mock()
        mock_metrics_service.return_value.record_startup_metrics = Mock()
        mock_metrics_service.return_value.record_system_metrics = Mock()

        # Create validator
        validator = Validator(config=mock_config)

        # Mock additional methods
        validator.sync = Mock()
        validator.load_state = Mock()
        validator.save_state = Mock()

        yield validator


class TestValidator:
    """Test cases for the Validator class."""

    def test_set_weights_with_treasury_hotkey(self, mock_validator):
        """Test that weights are set correctly when TREASURY_HOTKEY is configured."""
        # Set up treasury hotkey
        global TREASURY_HOTKEY
        original_treasury = TREASURY_HOTKEY
        TREASURY_HOTKEY = 'test_treasury_hotkey'
        mock_validator.treasury_hotkey = TREASURY_HOTKEY

        # Mock metagraph
        mock_validator.metagraph.hotkeys = ['hotkey1', 'hotkey2', 'test_treasury_hotkey']
        mock_validator.metagraph.n = 3

        # Call set_weights
        mock_validator.set_weights()

        # Assert weights are set correctly
        expected_uid = 2  # index of 'test_treasury_hotkey'
        assert mock_validator.scores[expected_uid] == 1.0
        assert np.all(mock_validator.scores[:expected_uid] == 0.0)
        assert np.all(mock_validator.scores[expected_uid+1:] == 0.0)

        # Restore original
        TREASURY_HOTKEY = original_treasury

    def test_set_weights_without_treasury_hotkey(self, mock_validator):
        """Test that no weights are set when TREASURY_HOTKEY is None."""
        # Ensure treasury hotkey is None
        global TREASURY_HOTKEY
        original_treasury = TREASURY_HOTKEY
        TREASURY_HOTKEY = None
        mock_validator.treasury_hotkey = TREASURY_HOTKEY

        # Call set_weights
        mock_validator.set_weights()

        # Assert no weights are set (scores remain zeros)
        assert np.all(mock_validator.scores == 0.0)

        # Restore original
        TREASURY_HOTKEY = original_treasury

    def test_forward_heartbeat_sent_when_due(self, mock_validator):
        """Test that heartbeat is sent when 5 minutes have passed."""
        # Set last heartbeat time to more than 5 minutes ago
        mock_validator.last_heartbeat_time = time.time() - 301

        # Mock the metrics service
        mock_validator.metrics_service.record_heartbeat = Mock()

        # Record time before calling forward
        before_time = time.time()

        # Call forward
        mock_validator.forward()

        # Assert heartbeat was called
        mock_validator.metrics_service.record_heartbeat.assert_called_once_with(qbittensor.__version__)

        # Assert timestamp was updated (should be >= before_time)
        assert mock_validator.last_heartbeat_time >= before_time

    def test_forward_heartbeat_not_sent_too_soon(self, mock_validator):
        """Test that heartbeat is not sent if less than 5 minutes have passed."""
        # Set last heartbeat time to recently
        initial_time = time.time() - 100  # less than 300 seconds
        mock_validator.last_heartbeat_time = initial_time

        # Mock the metrics service
        mock_validator.metrics_service.record_heartbeat = Mock()

        # Call forward
        mock_validator.forward()

        # Assert heartbeat was not called
        mock_validator.metrics_service.record_heartbeat.assert_not_called()

        # Assert timestamp was not updated
        assert mock_validator.last_heartbeat_time == initial_time
