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

import asyncio
from typing import Any, List
import time

import numpy as np

from qbittensor.utils.services.telemetry import TelemetryService
from qbittensor.utils.env import get_api_config

_api_cfg = get_api_config()

import bittensor as bt
from qbittensor.validator.solution.solution_container_manager import SolutionContainerManager
from qbittensor.base.validator import BaseValidatorNeuron
from qbittensor.protocol import SolutionSynapse
from qbittensor.validator.synapse.process_responses import ResponseProcessor
from qbittensor.validator.solution.solution_cross_check import SolutionCrossChecker
from qbittensor.utils.services.challenges import ChallengesClient
from qbittensor.database.db_connection import DBConnection
from qbittensor.validator.solution.constants import CHALLENGE_SOLTION_PREFIX
from qbittensor.protocol import MinerSubmissionStatus

TREASURY_HOTKEY: str = "5DCLafsAKaLeZwm9hjMHvrQNjtucSwBhKyTLYnYmMvhxF2Uc"
TREASURY_WALLET_AMOUNT: float = 0.99
PRIVATE_MINER_HOTKEY: str = "5HmQDNh8BrbDeT1bjgqXZ3KGAEb9n6doozNL2mQiJ9rYmuqh"
MIN_DUST_FLOOR: float = 2.5e-5


class Validator(BaseValidatorNeuron):

    def __init__(self, config=None):
        super(Validator, self).__init__(config=config)

        # Each high-level client owns its own RequestManager (per the design).
        # This eliminates the old shared RM with multiple service URLs.
        self.telemetry_service = TelemetryService(
            keypair=self.wallet.hotkey,
            base_url=_api_cfg.telemetry_api_url,
            tensorauth_url=_api_cfg.tensorauth_url,
            netuid=self.config.netuid,
            device=self.device,
            service_name=f"bittensor.sn{self.config.netuid}.validator",
            network=self.subtensor.network,
        )

        # Send startup metrics
        self.telemetry_service.record_startup_metrics()

        my_hotkey = self.wallet.hotkey.ss58_address
        VALIDATOR_LABEL: str = f"{CHALLENGE_SOLTION_PREFIX}_{my_hotkey[0:5]}"

        self.database_connection: DBConnection = DBConnection(database_name_prefix="challenge_solutions", hotkey=my_hotkey)

        # Single source of truth for all platform (challenges) API calls.
        # ChallengesClient creates and owns its own RequestManager.
        self.platform_client = ChallengesClient(
            keypair=self.wallet.hotkey,
            base_url=_api_cfg.challenges_api_url,
            tensorauth_url=_api_cfg.tensorauth_url,
            netuid=self.config.netuid,
        )

        # For any internal paths that still need a raw challenges-scoped RM,
        # derive it from the platform client (the RM has the correct base URL baked in).
        challenges_rm = self.platform_client.request_manager
        self.request_manager = challenges_rm  # compatibility for anything reading validator.request_manager

        self.response_processor: ResponseProcessor = ResponseProcessor(
            challenges_rm,
            self.metagraph,
            my_hotkey,
            VALIDATOR_LABEL,
            self.database_connection,
            self.subtensor,
            self.platform_client,
        )
        self.solution_container_manager: SolutionContainerManager = SolutionContainerManager(
            self.platform_client, self.database_connection, VALIDATOR_LABEL
        )

        self.cross_check: SolutionCrossChecker = SolutionCrossChecker(
            VALIDATOR_LABEL,
            self.platform_client,
            self.solution_container_manager,
            self.database_connection,
            self.subtensor,
        )

        # Persistent loop for the validator background thread (see _run_async).
        self._async_loop: asyncio.AbstractEventLoop | None = None

    def _ensure_async_loop(self) -> asyncio.AbstractEventLoop:
        if self._async_loop is None or self._async_loop.is_closed():
            self._async_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._async_loop)
        return self._async_loop

    def _run_async(self, coro):
        return self._ensure_async_loop().run_until_complete(coro)

    def forward(self):
        """Forward function for the validator.

        The weight / score computation that used to live behind a custom weights_timer
        has been moved to the canonical set_weights() override. The base class sync()
        (called after forward in the run loop) now owns the decision of when to set weights.
        """
        bt.logging.info("⏩ Running forward pass")

        # Check timers (these must tick on the forward cadence)
        self.solution_container_manager.timer.check_timer()        # pruning overdue containers + completed solutions
        self.cross_check.timer.check_timer()                       # looking for solutions to cross-check
        self.telemetry_service.heartbeat_timer.check_timer()       # sending the heartbeat
        self.telemetry_service.system_metrics_timer.check_timer()  # sending the system metrics

        # Build the synapse
        validator_busy: bool = self.solution_container_manager.validator_is_busy()

        miner_synapse_responses: List[Any] = self._run_async(
            self._gather_miner_synapses(validator_busy)
        )

        if validator_busy:
            bt.logging.info("🐝 Validator is currently busy running other solutions. Not accepting more solutions yet, checking transactions for maintenance incentive")

        # Process miner responses
        self.response_processor.process_synapses(miner_synapse_responses, validator_busy=validator_busy)

    async def _gather_miner_synapses(self, validator_busy: bool) -> List[Any]:
        # Drop any aiohttp session tied to a previously closed loop.
        if self.dendrite._session is not None:
            await self.dendrite.aclose_session()

        responses: List[Any] = []
        try:
            for uid, hotkey in enumerate(self.metagraph.hotkeys):
                responses.append(
                    await self.handle_miner_synapse(uid, hotkey, validator_busy)
                )
            return responses
        finally:
            if self.dendrite._session is not None:
                await self.dendrite.aclose_session()

    async def handle_miner_synapse(
        self, uid: int, hotkey: str, validator_busy: bool
    ) -> Any:
        statuses: list[MinerSubmissionStatus] = (
            self.database_connection.db_query.get_miner_submission_statuses(hotkey)
        )
        synapse: SolutionSynapse = SolutionSynapse(
            validator_busy=validator_busy,
            solution_candidate=None,
            submission_statuses=statuses,
        )

        return await self.dendrite.forward(
            axons=self.metagraph.axons[uid],
            synapse=synapse,
            deserialize=True,
            timeout=45,
        )

    def set_weights(self):
        """Compute maintenance incentive + treasury weights from recent verified miners (DB),
        then delegate to BaseValidatorNeuron.set_weights() which normalizes and submits on-chain.
        If the treasury hotkey cannot be located, we refuse to set weights this round.
        """
        try:
            # Use numpy array (matching the clean template pattern) for scores
            n = len(self.metagraph.uids) if hasattr(self.metagraph, "uids") else int(self.metagraph.n)
            weights = np.zeros(n, dtype=np.float32)

            # Get list of miners from db who have verified transactions within the last 3 weeks
            hotkeys_to_maintain: List[str] = self.database_connection.db_query.get_active_miners()
            if PRIVATE_MINER_HOTKEY not in hotkeys_to_maintain:
                hotkeys_to_maintain.append(PRIVATE_MINER_HOTKEY)

            n_maintain = len(hotkeys_to_maintain)

            floor = MIN_DUST_FLOOR if n_maintain > 0 else 0.0
            for uid, hotkey in enumerate(self.metagraph.hotkeys):
                if hotkey in hotkeys_to_maintain:
                    weights[uid] = floor

            mass_reserved_for_miners = floor * n_maintain
            remaining = max(0.0, 1.0 - mass_reserved_for_miners)

            treasury_assigned = remaining

            # Prune old maintenance incentive rows from the db (always run for hygiene)
            self.database_connection.db_query.prune_old_miner_solutions()

            # Set treasury weight by looking up its hotkey
            treasury_uid = None
            if TREASURY_HOTKEY in self.metagraph.hotkeys:
                treasury_uid = self.metagraph.hotkeys.index(TREASURY_HOTKEY)
                weights[treasury_uid] = treasury_assigned
            else:
                bt.logging.error(
                    f"CRITICAL: Treasury hotkey {TREASURY_HOTKEY} not found in current metagraph. "
                    "Refusing to set weights this round to avoid emitting incorrect distribution. "
                    f"Intended dust recipients: {n_maintain} (including forced private miner)."
                )

            self.scores = weights

            bt.logging.info(f"🔢 Setting weights: {self.scores}")

            # Only proceed to on-chain set if we successfully placed the treasury weight
            if treasury_uid is None:
                return

            # Helpful post-assignment visibility for the private miner (the one we care most about not losing)
            if PRIVATE_MINER_HOTKEY in self.metagraph.hotkeys:
                pm_uid = self.metagraph.hotkeys.index(PRIVATE_MINER_HOTKEY)
                actual = float(weights[pm_uid])
                bt.logging.info(
                    f"Private miner dust: {PRIVATE_MINER_HOTKEY} @ UID {pm_uid} "
                    f"assigned {actual:.8f} (floor={floor:.8f}) before normalization."
                )
            else:
                bt.logging.error(
                    f"PRIVATE MINER HOTKEY NOT FOUND IN METAGRAPH: {PRIVATE_MINER_HOTKEY} "
                    "— 0 weight will be emitted for it this round. "
                    "Verify the miner is registered on this netuid using this exact hotkey."
                )

        except Exception as exc:
            if "NeuronNoValidatorPermit" in str(exc):
                bt.logging.warning("⚠️ No validator permit")
            else:
                bt.logging.error(f"❌ Weight-setting error: {exc}", exc_info=True)
            # Do not proceed to the on-chain set on hard failure paths
            return

        super().set_weights()

    def save_state(self):
        """No-op.

        This validator recomputes all state (active miners for maintenance weights, scores)
        from the DB on demand. State saving is inherited from the template but provides
        no value here.
        """
        pass

    def load_state(self):
        """No-op.

        This validator recomputes all state (active miners for maintenance weights, scores)
        from the DB on demand. State loading is inherited from the template but provides
        no value here.
        """
        pass


# The main function parses the configuration and runs the validator.
if __name__ == "__main__":
    with Validator() as validator:
        while validator.is_running and validator.thread and validator.thread.is_alive():
            bt.logging.info("Validator running... {}".format(time.time()))
            time.sleep(5)
        bt.logging.warning("Validator background thread has stopped. Exiting.")
