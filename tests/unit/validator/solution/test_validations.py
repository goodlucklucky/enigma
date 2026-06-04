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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from qbittensor.validator.solution.challenge_inputs.challenge_setups import (
    run_challenge_setup,
)
from qbittensor.validator.solution.challenge_inputs.mock_solution_setup import mock_solution_setup
from qbittensor.validator.solution.solution_validations.mock_solution import run as run_mock
from qbittensor.validator.solution.solution_validations.solution_validator import (
    validate_output,
)
from qbittensor.validator.solution.milestones import MOCK_CHALLENGE_ID
from qbittensor.challenges.mock_challenge import MOCK_TIMESTAMP_MAX_FUTURE_SKEW_SECONDS


class TestChallengeSetups:
    def test_mock_solution_setup_writes_challenge_input(self, tmp_path):
        path = mock_solution_setup(str(tmp_path))
        assert path == str(tmp_path / "challenge_input.txt")
        content = (tmp_path / "challenge_input.txt").read_text()
        assert "Hello" in content

    def test_run_challenge_setup_known_challenge(self, tmp_path):
        result = run_challenge_setup(MOCK_CHALLENGE_ID, str(tmp_path), {"difficulty": 1})
        assert isinstance(result, str)

    def test_run_challenge_setup_unknown_challenge(self, tmp_path):
        result = run_challenge_setup("00000000-0000-0000-0000-000000000000", str(tmp_path), {})
        assert result is False


class TestMockSolutionValidation:
    def _signed_solution(self, private_key: Ed25519PrivateKey) -> dict:
        payload = json.dumps({"ts": int(time.time()), "challenge": "mock"})
        signature = private_key.sign(payload.encode("utf-8"))
        return {
            "status": "success",
            "signature": signature.hex(),
            "payload": payload,
        }

    def test_valid_signed_output(self, tmp_path, monkeypatch):
        private_key = Ed25519PrivateKey.generate()
        public_hex = private_key.public_key().public_bytes_raw().hex()
        monkeypatch.setenv("ENIGMA_MOCK_PUBLIC_KEY", public_hex)

        solution = self._signed_solution(private_key)
        (tmp_path / "result.json").write_text(json.dumps(solution))
        success, reason = run_mock(str(tmp_path))
        assert success is True
        assert reason is None

    def test_rejects_expired_timestamp(self, tmp_path, monkeypatch):
        private_key = Ed25519PrivateKey.generate()
        public_hex = private_key.public_key().public_bytes_raw().hex()
        monkeypatch.setenv("ENIGMA_MOCK_PUBLIC_KEY", public_hex)

        old_ts = int(time.time()) - 7200
        payload = json.dumps({"ts": old_ts, "challenge": "mock"})
        signature = private_key.sign(payload.encode("utf-8"))
        solution = {"status": "success", "signature": signature.hex(), "payload": payload}
        (tmp_path / "result.json").write_text(json.dumps(solution))
        success, reason = run_mock(str(tmp_path))
        assert success is False
        assert reason is not None
        assert "older than one hour" in reason

    def test_rejects_future_timestamp(self, tmp_path, monkeypatch):
        private_key = Ed25519PrivateKey.generate()
        public_hex = private_key.public_key().public_bytes_raw().hex()
        monkeypatch.setenv("ENIGMA_MOCK_PUBLIC_KEY", public_hex)

        future_ts = int(time.time()) + MOCK_TIMESTAMP_MAX_FUTURE_SKEW_SECONDS + 10
        payload = json.dumps({"ts": future_ts, "challenge": "mock"})
        signature = private_key.sign(payload.encode("utf-8"))
        solution = {"status": "success", "signature": signature.hex(), "payload": payload}
        (tmp_path / "result.json").write_text(json.dumps(solution))
        success, reason = run_mock(str(tmp_path))
        assert success is False
        assert reason is not None
        assert "too far in the future" in reason


class TestSolutionValidator:
    def test_unknown_challenge_returns_false(self, tmp_path):
        success, reason = validate_output(str(tmp_path), "00000000-0000-0000-0000-000000000000")
        assert success is False
        assert reason is not None and "No validation handler" in reason

    def test_known_challenge_dispatches(self, tmp_path, monkeypatch):
        private_key = Ed25519PrivateKey.generate()
        public_hex = private_key.public_key().public_bytes_raw().hex()
        monkeypatch.setenv("ENIGMA_MOCK_PUBLIC_KEY", public_hex)

        payload = json.dumps({"ts": int(time.time()), "challenge": "mock"})
        signature = private_key.sign(payload.encode("utf-8"))
        solution = {
            "status": "success",
            "signature": signature.hex(),
            "payload": payload,
        }
        (tmp_path / "result.json").write_text(json.dumps(solution))
        success, reason = validate_output(str(tmp_path), MOCK_CHALLENGE_ID)
        assert success is True
        assert reason is None
