# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
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


import time
import numpy as np
import bittensor as bt
import qbittensor
from typing import Optional

# import base validator class which takes care of most of the boilerplate
from qbittensor.base.validator import BaseValidatorNeuron

from qbittensor.validator.services.metrics import MetricsService

TREASURY_HOTKEY = "5FbishpQXFZjrVStcjA5x6WJp32NQRvTeKyMDdLkv181VbS5"  # type: Optional[str]


class Validator(BaseValidatorNeuron):

    def __init__(self, config=None):
        super().__init__(config=config)

        self.metrics_service = MetricsService(keypair=self.wallet.hotkey, network=self.subtensor.network, netuid=self.config.netuid, device=self.device)
        self.treasury_hotkey = TREASURY_HOTKEY
        self.last_heartbeat_time = 0
        self.last_system_metrics_time = 0

        if self.treasury_hotkey:
            bt.logging.info("Constant-weight validator initialized")
            bt.logging.info("   -> Will ALWAYS give 100% weight to treasury hotkey: {}".format(self.treasury_hotkey))
        else:
            bt.logging.info("Validator initialized in PASSIVE mode")
            bt.logging.info("   -> No weights will be set (TREASURY_HOTKEY = None)")

        bt.logging.info("load_state()")
        self.load_state()

        # Record startup system metrics
        self.metrics_service.record_startup_metrics()

    def forward(self):
        """Minimal forward – we don’t need to query any miners."""
        bt.logging.debug("forward() called (minimal validator)")

        # Send heartbeat every 5 minutes
        if time.time() - self.last_heartbeat_time >= 300:
            try:
                self.metrics_service.record_heartbeat(qbittensor.__version__)
                bt.logging.info("Heartbeat sent - version {}".format(qbittensor.__version__))
                self.last_heartbeat_time = time.time()
            except Exception as e:
                bt.logging.warning("Failed to send heartbeat: {}".format(e))

        # Send system metrics every 5 minutes
        if time.time() - self.last_system_metrics_time >= 300:
            try:
                self.metrics_service.record_system_metrics()
                self.last_system_metrics_time = time.time()
            except Exception as e:
                bt.logging.warning(f"Failed to send system metrics: {e}")

        return None

    def set_weights(self):
        """Only set weights if TREASURY_HOTKEY is configured."""
        if self.treasury_hotkey is None:
            bt.logging.debug("TREASURY_HOTKEY is None → skipping weight setting entirely")
            return

        bt.logging.info("Setting 100% weight to treasury hotkey: {}".format(self.treasury_hotkey))

        metagraph = self.metagraph

        if self.treasury_hotkey not in metagraph.hotkeys:
            bt.logging.warning("treasury hotkey {} not found in metagraph yet".format(self.treasury_hotkey))
            return

        uid = metagraph.hotkeys.index(self.treasury_hotkey)

        # Force 100% onto this UID only
        self.scores = np.zeros(metagraph.n, dtype=np.float32)
        self.scores[uid] = 1.0

        bt.logging.success("-> Scores updated: 100% on UID {} ({})".format(uid, self.treasury_hotkey))

        super().set_weights()


# The main function parses the configuration and runs the validator.
if __name__ == "__main__":
    with Validator() as validator:
        while True:
            bt.logging.info("Validator running... {}".format(time.time()))
            time.sleep(5)