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

import pytest
from workbench.challenges.breaking_rsa import generate_breaking_rsa


class TestBreakingRSAGeneration:
    def test_deterministic_with_seed(self):
        p1, v1, s1 = generate_breaking_rsa(num_bits=64, difficulty=1, seed=42)
        p2, v2, s2 = generate_breaking_rsa(num_bits=64, difficulty=1, seed=42)
        assert s1 == s2
        assert p1.num == p2.num
        assert v1.p == v2.p
        assert v1.q == v2.q

    def test_valid_problem(self):
        problem, verif, seed = generate_breaking_rsa(num_bits=64, difficulty=1, seed=123)
        assert problem.num > 0
        assert problem.num_bits == 64
        assert verif.p * verif.q == verif.n
        assert problem.num == verif.n
