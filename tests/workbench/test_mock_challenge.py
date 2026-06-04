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

import json
import time

import pytest
from qbittensor.challenges.mock_challenge import (
    MockChallenge, Problem, Solution, Verif,
    sign_mock_payload, generate_keypair,
    MOCK_TIMESTAMP_MAX_AGE_SECONDS,
    MOCK_TIMESTAMP_MAX_FUTURE_SKEW_SECONDS,
)
from workbench.verifier import verify_mock


@pytest.fixture(scope="module")
def keypair():
    """Generate a keypair once for all tests in this module."""
    private_hex, public_hex = generate_keypair()
    return private_hex, public_hex


class TestMockChallenge:
    def test_generate(self, keypair):
        _, public_hex = keypair
        challenge = MockChallenge(difficulty=1, public_key_hex=public_hex)
        problem, verif = challenge.generate(seed=0)
        assert problem.difficulty == 1
        assert verif.public_key_hex == public_hex

    def test_sign_and_verify(self, keypair):
        private_hex, public_hex = keypair
        challenge = MockChallenge(difficulty=1, public_key_hex=public_hex)
        problem, verif = challenge.generate(seed=0)
        solution = sign_mock_payload(private_hex)
        assert challenge.verify(problem, solution, verif)

    def test_wrong_key_fails(self, keypair):
        _, public_hex = keypair
        other_private, _ = generate_keypair()
        challenge = MockChallenge(difficulty=1, public_key_hex=public_hex)
        problem, verif = challenge.generate(seed=0)
        solution = sign_mock_payload(other_private)
        assert not challenge.verify(problem, solution, verif)

    def test_expired_timestamp_fails(self, keypair):
        private_hex, public_hex = keypair
        challenge = MockChallenge(difficulty=1, public_key_hex=public_hex)
        problem, verif = challenge.generate(seed=0)

        # Manually create a solution with an old timestamp
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        old_ts = int(time.time()) - MOCK_TIMESTAMP_MAX_AGE_SECONDS - 60
        payload = json.dumps({"ts": old_ts, "challenge": "mock"})
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
        sig = key.sign(payload.encode("utf-8"))
        solution = Solution(status="success", signature=sig.hex(), payload=payload)

        assert not challenge.verify(problem, solution, verif)

    def test_future_timestamp_fails(self, keypair):
        private_hex, public_hex = keypair
        challenge = MockChallenge(difficulty=1, public_key_hex=public_hex)
        problem, verif = challenge.generate(seed=0)

        # Manually create a solution with a timestamp too far in the future
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        future_ts = int(time.time()) + MOCK_TIMESTAMP_MAX_FUTURE_SKEW_SECONDS + 10
        payload = json.dumps({"ts": future_ts, "challenge": "mock"})
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
        sig = key.sign(payload.encode("utf-8"))
        solution = Solution(status="success", signature=sig.hex(), payload=payload)

        assert not challenge.verify(problem, solution, verif)

    def test_none_signature_fails(self, keypair):
        _, public_hex = keypair
        challenge = MockChallenge(difficulty=1, public_key_hex=public_hex)
        problem, verif = challenge.generate(seed=0)
        solution = Solution(status="failure", signature=None, payload=None)
        assert not challenge.verify(problem, solution, verif)

    def test_invalid_payload_json_fails(self, keypair):
        _, public_hex = keypair
        challenge = MockChallenge(difficulty=1, public_key_hex=public_hex)
        problem, verif = challenge.generate(seed=0)
        solution = Solution(status="success", signature="abcd", payload="not json")
        assert not challenge.verify(problem, solution, verif)


class TestVerifyMock:
    def test_valid_solution(self, keypair):
        private_hex, public_hex = keypair
        problem = Problem(difficulty=1)
        verif = Verif(public_key_hex=public_hex)
        solution = sign_mock_payload(private_hex)
        result = verify_mock(problem, solution, verif)
        assert result.passed
        assert "Valid signature" in result.message

    def test_none_signature(self, keypair):
        _, public_hex = keypair
        problem = Problem(difficulty=1)
        verif = Verif(public_key_hex=public_hex)
        solution = Solution(status="failure", signature=None, payload=None)
        result = verify_mock(problem, solution, verif)
        assert not result.passed

    def test_wrong_key(self, keypair):
        _, public_hex = keypair
        other_private, _ = generate_keypair()
        problem = Problem(difficulty=1)
        verif = Verif(public_key_hex=public_hex)
        solution = sign_mock_payload(other_private)
        result = verify_mock(problem, solution, verif)
        assert not result.passed


class TestKeypairGeneration:
    def test_keypair_lengths(self):
        private_hex, public_hex = generate_keypair()
        assert len(bytes.fromhex(private_hex)) == 32
        assert len(bytes.fromhex(public_hex)) == 32

    def test_keypairs_are_unique(self):
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        assert kp1[0] != kp2[0]
        assert kp1[1] != kp2[1]


class TestSolutionSerde:
    def test_round_trip(self, keypair):
        private_hex, _ = keypair
        solution = sign_mock_payload(private_hex)
        json_str = solution.to_json()
        restored = Solution.from_json(json_str)
        assert restored.status == solution.status
        assert restored.signature == solution.signature
        assert restored.payload == solution.payload
