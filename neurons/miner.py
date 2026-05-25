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

import argparse
import time
import typing
from types import SimpleNamespace

from qbittensor.utils.env import get_api_config

_api_cfg = get_api_config()

import bittensor as bt

# import base miner class which takes care of most of the boilerplate
from qbittensor.database.db_connection import DBConnection
from qbittensor.database.miner.db_query import DBQueryMiner
from qbittensor.base.miner import BaseMinerNeuron
from qbittensor.utils.services.telemetry import TelemetryService
from qbittensor.miner.solution_polling import SolutionPoller
from qbittensor.database.miner.db_models import MinerSubmission
from qbittensor.dto.challenge import SolutionCandidate
from qbittensor.utils.time import timestamp_str
from qbittensor.protocol import SolutionSynapse
from qbittensor.cli.miner.utils.constants import MINER_DB_TABLE_PREFIX
from qbittensor.utils.transfer_proof import build_transfer_proof_message


class Miner(BaseMinerNeuron):

    def __init__(self, config=None):
        # Call to super ctor
        super(Miner, self).__init__(config=config)

        # TelemetryService owns its own RequestManager (pointed at the telemetry service).
        self.telemetry_service: TelemetryService = TelemetryService(
            keypair=self.wallet.hotkey,
            base_url=_api_cfg.telemetry_api_url,
            tensorauth_url=_api_cfg.tensorauth_url,
            netuid=self.config.netuid,
            service_name=f"bittensor.sn{self.config.netuid}.miner",
            network=self.subtensor.network,
        )

        # Build solution poller
        self.db_query: DBQueryMiner = DBConnection(database_name_prefix=MINER_DB_TABLE_PREFIX, hotkey=self.wallet.hotkey.ss58_address).db_query_miner
        self.solution_poller: SolutionPoller = SolutionPoller(db_query=self.db_query)

    async def forward(self, synapse: SolutionSynapse) -> SolutionSynapse:
        """Processes an incoming SolutionSynapse from a validator.

        This is the main entrypoint for the miner when receiving work from validators.
        It records submission statuses, checks if the validator is busy, polls the local
        database for a ready solution, attaches a signed transfer proof, and returns the
        enriched synapse.
        """
        bt.logging.info("⏩ Running forward pass for miner")

        validator_hotkey: str | None = synapse.dendrite.hotkey if synapse.dendrite else None
        if not validator_hotkey:
            bt.logging.warning("⛔ Received synapse without a valid dendrite hotkey. Skipping processing.")
            return synapse

        bt.logging.info(f"🔄 Processing synapse from validator {validator_hotkey}")

        if synapse.submission_statuses:
            for submission_status in synapse.submission_statuses:
                bt.logging.info(f"🔄 Submission status: {submission_status.status} for submission {submission_status.tx_hash}")
                self.db_query.insert_miner_submission_status(
                    challenge_milestone_id=submission_status.challenge_milestone_id,
                    solution_status=submission_status.status,
                    validator_hotkey=validator_hotkey,
                    tx_hash=submission_status.tx_hash,
                )

        if synapse.validator_busy:
            bt.logging.info("⚠️ Validator is currently busy, not checking for a new solution")
            return synapse

        # Get the data from the local database
        miner_submission: MinerSubmission | None = self.solution_poller.poll()

        if miner_submission is None:
            bt.logging.warning("🌿 No miner submission found, skipping synapse")
            return synapse

        # If we got a solution from the challenge API

        if not (
            miner_submission.tx_hash
            and miner_submission.transfer_block_hash
            and miner_submission.transfer_from_ss58
            and miner_submission.transfer_to_ss58
            and miner_submission.transfer_amount_rao
        ):
            bt.logging.warning(
                "Submission row missing transfer proof fields "
                "(tx_hash / transfer_block_hash / from / to / amount); "
                "cannot attach signed proof. Re-run mine_enigma CLI or migrate DB."
            )
            return synapse

        # Transform the miner submission into a solution candidate
        solution_candidate: SolutionCandidate = SolutionCandidate.from_miner_submission(miner_submission)
        bt.logging.info(f"✅ Received solution candidate: {solution_candidate}")

        proof_message = build_transfer_proof_message(
            miner_hotkey=self.wallet.hotkey.ss58_address,
            milestone_id=solution_candidate.challenge_milestone_id,
            upload_id=solution_candidate.upload_endpoint_id,
            tx_hash=miner_submission.tx_hash,
            transfer_from_ss58=miner_submission.transfer_from_ss58,
            transfer_to_ss58=miner_submission.transfer_to_ss58,
            transfer_amount_rao=miner_submission.transfer_amount_rao,
        )
        signature = self.wallet.hotkey.sign(proof_message.encode("utf-8"))

        synapse.solution_candidate = solution_candidate
        synapse.tx_hash = miner_submission.tx_hash
        synapse.transfer_block_hash = miner_submission.transfer_block_hash
        synapse.transfer_from_ss58 = miner_submission.transfer_from_ss58
        synapse.transfer_to_ss58 = miner_submission.transfer_to_ss58
        synapse.transfer_amount_rao = miner_submission.transfer_amount_rao
        synapse.transfer_proof_message = proof_message
        synapse.transfer_proof_signature_hex = signature.hex()

        self.db_query.record_solution_served_to_validator(miner_submission.tx_hash)

        # Return the synapse
        return synapse

    async def blacklist(self, synapse: SolutionSynapse) -> typing.Tuple[bool, str]:
        """Determines whether an incoming request should be blacklisted.

        Security checks (in order):
        - Must have a valid dendrite + hotkey
        - Hotkey must be registered in the metagraph
        - Validator must have sufficient stake (currently >= 0.0, effectively any staked validator)
        """

        # Check if synapse hotkey is in the metagraph
        if not synapse.dendrite or synapse.dendrite.hotkey not in self.metagraph.hotkeys:
            validator_hotkey = synapse.dendrite.hotkey if synapse.dendrite else "UNKNOWN"
            bt.logging.info(f"❗Blacklisted unknown hotkey: {validator_hotkey}")
            return True, f"❗Hotkey {validator_hotkey} was not found from metagraph.hotkeys",

        stake, uid = self.get_validator_stake_and_uid(synapse.dendrite.hotkey)

        # Check if validator has sufficient stake
        validator_min_stake = 0.0
        if stake < validator_min_stake:
            bt.logging.info(f"❗Blacklisted validator {synapse.dendrite.hotkey} with insufficient stake: {stake}")
            return True, f"❗Hotkey {synapse.dendrite.hotkey} has insufficient stake: {stake}",

        # Valid hotkey
        bt.logging.info(f"✅ Accepted hotkey: {synapse.dendrite.hotkey} (UID: {uid} - Stake: {stake})")
        return False, f"✅ Accepted hotkey: {synapse.dendrite.hotkey}"

    async def priority(self, synapse: SolutionSynapse) -> float:
        """Returns the priority for processing this synapse.

        Higher values are processed first. Currently prioritizes by the calling
        validator's stake in the metagraph.
        """
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            bt.logging.warning(
                "Received a request without a dendrite or hotkey."
            )
            return 0.0

        bt.logging.debug(f"🧮 Calculating priority for synapse from {synapse.dendrite.hotkey}")
        stake, uid = self.get_validator_stake_and_uid(synapse.dendrite.hotkey)
        bt.logging.debug(f"🏆 Prioritized: {synapse.dendrite.hotkey} (UID: {uid} - Stake: {stake})")
        return stake

    def get_validator_stake_and_uid(self, hotkey):
        uid = self.metagraph.hotkeys.index(hotkey)  # get uid
        return float(self.metagraph.S[uid]), uid  # return validator stake

    def resync_metagraph(self):
        """Resync metagraph without emitting the base class info log."""
        self.metagraph.sync(subtensor=self.subtensor)

    def save_state(self):
        """No-op.

        This miner does not persist scores or hotkey state to disk.
        All solution state lives in the local database and is rebuilt on demand.
        State saving is inherited from the template but provides no value here.
        """
        pass

    def load_state(self):
        """No-op.

        This miner does not persist scores or hotkey state to disk.
        All solution state lives in the local database and is rebuilt on demand.
        State loading is inherited from the template but provides no value here.
        """
        pass

    @classmethod
    def _apply_secure_blacklist_defaults(cls, config: "bt.Config") -> "bt.Config":
        """Force secure defaults for blacklist settings.

        This prevents the base class from emitting security warnings and ensures
        we only accept requests from registered validators with stake.
        """
        if not hasattr(config, "blacklist") or config.blacklist is None:
            config.blacklist = bt.Config()
        # Defensive: in case bt.Config() returned None in some edge case
        if getattr(config, "blacklist", None) is None:
            config.blacklist = SimpleNamespace()

        config.blacklist.allow_non_registered = False
        config.blacklist.force_validator_permit = True
        return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    Miner.add_args(parser)
    config = bt.Config(parser)
    config = Miner._apply_secure_blacklist_defaults(config)

    with Miner(config=config) as miner:
        while True:
            bt.logging.info(f"Miner running... {timestamp_str()}")
            time.sleep(5)
