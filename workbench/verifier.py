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

from dataclasses import dataclass
from typing import Optional


@dataclass
class VerifyResult:
    passed: bool
    message: str
    expected: str = ""
    actual: str = ""


def verify_breaking_rsa(problem, solution, verif) -> VerifyResult:
    """Verify a Breaking RSA solution."""
    from qbittensor.challenges.breaking_rsa import BreakingRSA

    try:
        challenge = BreakingRSA(
            difficulty=problem.difficulty,
            num_bits=problem.num_bits,
        )
        success = challenge.verify(problem, solution, verif)
    except Exception as e:
        return VerifyResult(
            passed=False,
            message=f"Verification error: {e}",
        )

    if success:
        return VerifyResult(
            passed=True,
            message=f"Correct factorization: p={solution.p} q={solution.q}",
        )
    else:
        if solution.p is None or solution.q is None:
            return VerifyResult(
                passed=False,
                message=f"Verification error: solution has missing factors (p={solution.p}, q={solution.q})",
                expected=f"p={verif.p} q={verif.q}",
                actual=f"p={solution.p} q={solution.q}",
            )
        return VerifyResult(
            passed=False,
            message="Incorrect factorization",
            expected=f"p={verif.p} q={verif.q}",
            actual=f"p={solution.p} q={solution.q}",
        )


def verify_mock(problem, solution, verif) -> VerifyResult:
    """Verify a mock challenge solution (Ed25519 signature + timestamp)."""
    from qbittensor.challenges.mock_challenge import MockChallenge

    if solution.signature is None or solution.payload is None:
        return VerifyResult(
            passed=False,
            message="Verification error: signature or payload is None",
            expected="valid signature",
            actual=f"signature={solution.signature}, payload={solution.payload}",
        )

    try:
        challenge = MockChallenge(
            difficulty=problem.difficulty,
            public_key_hex=verif.public_key_hex,
        )
        success = challenge.verify(problem, solution, verif)
    except Exception as e:
        return VerifyResult(
            passed=False,
            message=f"Verification error: {e}",
        )

    if success:
        return VerifyResult(
            passed=True,
            message="Valid signature, timestamp within window",
        )
    else:
        return VerifyResult(
            passed=False,
            message="Invalid signature or expired timestamp",
        )
