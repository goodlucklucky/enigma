# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import numpy as np
import pytest

from qbittensor.base.utils.weight_utils import (
    convert_weights_and_uids_for_emit,
    U16_MAX,
)


class TestConvertWeightsAndUidsForEmit:
    """Contract tests for the u16 quantization used for on-chain weight emission."""

    def test_single_dust_with_dominant_treasury_sums_to_exactly_u16_max(self):
        """The classic off-by-2 case: treasury ~0.999975 + one dust 2.5e-5.

        Before the post-quantization correction this produced [65535, 2] (sum=65537).
        After the fix it must produce a total of exactly 65535 while keeping dust > 0.
        """
        # This is the exact float distribution the validator produces for 1 maintenance miner
        scores = np.array([0.999975, 0.0, 0.000025], dtype=np.float32)
        uids = np.array([87, 100, 171], dtype=np.int64)

        # Simulate the L1 normalization that BaseValidatorNeuron.set_weights does
        norm = np.linalg.norm(scores, ord=1)
        raw_weights = scores / norm

        uint_uids, uint_weights = convert_weights_and_uids_for_emit(
            uids=uids, weights=raw_weights
        )

        total = sum(uint_weights)
        assert total == U16_MAX, f"Expected sum {U16_MAX}, got {total}"
        assert total <= U16_MAX

        # Dust must survive (this is the keep-alive requirement)
        emitted = dict(zip([int(u) for u in uint_uids], uint_weights))
        assert emitted.get(171, 0) > 0
        # In this scenario the dust should quantize to 2 (or 1 in edge rounding)
        assert emitted.get(171, 0) in (1, 2)

    def test_many_dust_weights_still_sum_leq_u16_max(self):
        """Stress case: 1 dominant + 49 tiny dust weights."""
        n = 50
        scores = np.zeros(n, dtype=np.float32)
        scores[0] = 1.0 - (n - 1) * 1e-5
        for i in range(1, n):
            scores[i] = 1e-5

        norm = np.linalg.norm(scores, ord=1)
        raw = scores / norm

        uint_uids, uint_weights = convert_weights_and_uids_for_emit(
            uids=np.arange(n), weights=raw
        )

        total = sum(uint_weights)
        assert total <= U16_MAX, f"Sum {total} exceeded U16_MAX"
        # Every dust must have survived
        assert len(uint_weights) == n  # treasury + 49 dust

    def test_all_weights_zero_returns_empty(self):
        scores = np.zeros(5, dtype=np.float32)
        uint_uids, uint_weights = convert_weights_and_uids_for_emit(
            uids=np.arange(5), weights=scores
        )
        assert uint_uids == []
        assert uint_weights == []

    def test_exact_u16_max_single_weight(self):
        """Edge case: exactly one weight of 1.0 after normalization."""
        scores = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        uint_uids, uint_weights = convert_weights_and_uids_for_emit(
            uids=np.array([0, 1, 2]), weights=scores
        )
        assert sum(uint_weights) == U16_MAX
        assert uint_weights[0] == U16_MAX
