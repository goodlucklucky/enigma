#!/usr/bin/env python3
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

"""
Emit a signed mock-challenge JSON blob compatible with the validator check in
``qbittensor.validator.solution.solution_validations.mock_solution``.

Uses a **fixed dev Ed25519 seed** (``_DEV_MOCK_ED25519_SEED_HEX``) that matches the
default ``ENIGMA_MOCK_PUBLIC_KEY`` baked into that validator module — no environment
variables and no ``docker run -e`` wiring required for local plumbing tests.

Output contract (stdout-only, no shared filesystem with the validator):

  1. Text logs are written to stdout (and stderr) as usual.
  2. After all logs are flushed, a single magic separator line is written on
     its own line. The bytes of this constant MUST match
     ``qbittensor.validator.solution.constants.SOLUTION_OUTPUT_SEPARATOR``.
  3. After the separator, the solution zip is written as **base64-encoded
     ASCII** (standard alphabet, ``=`` padding). Docker's json-file logging
     driver treats stdout as UTF-8 text and corrupts raw binary; base64
     survives the round-trip intact.

The validator captures the container's stdout via ``docker logs`` after the
container exits, splits at the first occurrence of the separator, stores the
prefix as the run's log file, base64-decodes the suffix into the solution
zip, and extracts it into the ``solution_artifacts`` directory used by the
milestone validators.

Inputs still come from ``/challenge_input/...`` (read-only bind mount).

  python3 /app/mock_solution.py
"""

from __future__ import annotations

import base64
import io
import json
import logging
import sys
import time
import zipfile
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Must match qbittensor.validator.solution.constants.SOLUTION_OUTPUT_SEPARATOR.
SOLUTION_OUTPUT_SEPARATOR = (
    b"\n----- ENIGMA-SOLUTION-OUTPUT-BEGIN-a8c7f3e2-9d4b-4c5a-8f1e-2b6d3a4e5f7c -----\n"
)

# Hex-encoded 32-byte Ed25519 private seed; pairs with ``ENIGMA_MOCK_PUBLIC_KEY`` in
# ``qbittensor.validator.solution.solution_validations.mock_solution`` (mock / staging only).
_DEV_MOCK_ED25519_SEED_HEX = (
    "de3cd2caec642aa17b95c27f93676d026ed0fb58c44a12b586c72981768005f0"
)


@dataclass
class _SignedMockSolution:
    status: str
    signature: Optional[str]
    payload: Optional[str]


def sign_mock_payload(private_key_hex: str) -> _SignedMockSolution:
    """Same contract expected by ``qbittensor.validator.solution.solution_validations.mock_solution``."""
    payload = json.dumps({"ts": int(time.time()), "challenge": "mock"})
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    signature = private_key.sign(payload.encode("utf-8"))
    return _SignedMockSolution(
        status="success",
        signature=signature.hex(),
        payload=payload,
    )


def _configure_logging(level: int = logging.INFO) -> None:
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)

    # Logs go to stdout so they end up before the SOLUTION_OUTPUT_SEPARATOR on the
    # same stream that carries the raw zip payload. The validator captures stdout
    # via ``docker logs`` and stores the prefix as the run's log file.
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(stream_handler)


def _build_solution_zip(result_text: str, output_text: str) -> bytes:
    """Pack the mock solution artifacts into an in-memory zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("result.json", result_text)
        zf.writestr("output.txt", output_text)
    return buffer.getvalue()


def _write_solution_output(zip_bytes: bytes) -> None:
    """Flush logs, emit the separator, then write base64-encoded zip bytes.

    Docker's json-file logging driver treats stdout as UTF-8 text, so raw
    binary would be corrupted during the ``docker logs`` round-trip. Base64
    produces pure ASCII that survives intact.
    """
    sys.stdout.flush()
    sys.stderr.flush()

    encoded = base64.b64encode(zip_bytes)
    stdout_buffer = sys.stdout.buffer
    stdout_buffer.write(SOLUTION_OUTPUT_SEPARATOR)
    stdout_buffer.write(encoded)
    stdout_buffer.write(b"\n")
    stdout_buffer.flush()


def main() -> int:
    _configure_logging()
    log = logging.getLogger("mock_solution.example")

    log.info("Starting mock example solution (signed payload generator)")

    sol = sign_mock_payload(_DEV_MOCK_ED25519_SEED_HEX)
    doc = {
        "status": sol.status,
        "signature": sol.signature,
        "payload": sol.payload,
    }
    text = json.dumps(doc, indent=2)
    if not text.endswith("\n"):
        text += "\n"

    log.info("Built signed solution payload (status=%s)", doc.get("status"))
    log.info("Emitting solution_artifacts zip on stdout after separator")

    zip_bytes = _build_solution_zip(result_text=text, output_text=text)

    # IMPORTANT: No logging after this point! The solution output separator and
    # raw zip bytes are written to stdout; any subsequent text on stdout would
    # corrupt the binary payload and cause "Bad magic number" errors on the
    # validator side.
    _write_solution_output(zip_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
