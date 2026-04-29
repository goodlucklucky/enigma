# coding: utf-8
"""
Protocol for Challenges
"""
import typing
import bittensor as bt

class ChallengeSynapseBase(bt.Synapse):
    """Common metadata carried by every circuit-related synapse."""

    # Required request input, filled by sending dendrite caller.
    dummy_input: int

    # Optional request output, filled by receiving axon.
    dummy_output: typing.Optional[int] = None

    def deserialize(self) -> int:
        """Deserialize the synapse"""
        return self.dummy_output
