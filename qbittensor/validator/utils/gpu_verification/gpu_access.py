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

"""Build, run, and verify the validator GPU smoke-test container."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import bittensor as bt

from qbittensor.validator.solution.constants import (
    VALIDATOR_DOCKER_GPUS_DEFAULT,
    VALIDATOR_DOCKER_GPUS_ENV,
)

_GPU_VERIFICATION_DIR = Path(__file__).resolve().parent
_DEFAULT_IMAGE_NAME = "enigma-validator-gpu-verification"
_SUCCESS_MARKER = "SUCCESS: GPU is accessible from this container."


def _gpu_passthrough() -> str:
    """GPU selector for ``docker run --gpus`` (defaults to ``all`` for this smoke test)."""
    raw = os.getenv(VALIDATOR_DOCKER_GPUS_ENV, "").strip()
    if raw:
        return raw
    return VALIDATOR_DOCKER_GPUS_DEFAULT.strip() or "all"


def _remove_gpu_verification_image(image_name: str) -> None:
    subprocess.run(
        ["docker", "rmi", "-f", image_name],
        capture_output=True,
        check=False,
        text=True,
    )


def test_gpu_container(
    image_name: str = _DEFAULT_IMAGE_NAME,
    gpus: str | None = None,
) -> bool:
    """
    Build the GPU verification image, run it with GPU passthrough, and check stdout.

    Returns ``True`` when the container exits 0 and prints the expected success line
    from ``main.py``. Requires Docker, NVIDIA drivers, and the NVIDIA Container Toolkit.
    """

    build_dir = str(_GPU_VERIFICATION_DIR)
    gpus_arg = (gpus or _gpu_passthrough()).strip()
    if not gpus_arg:
        bt.logging.error(
            "GPU container test requires --gpus; set VALIDATOR_DOCKER_GPUS or pass gpus=..."
        )
        return False

    image_built = False
    try:
        bt.logging.info(f"Building GPU verification image '{image_name}' from {build_dir}")
        try:
            subprocess.run(
                ["docker", "build", "-t", image_name, build_dir],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            bt.logging.error(f"GPU image build failed (exit {exc.returncode})")
            if exc.stderr:
                bt.logging.error(exc.stderr.strip())
            return False

        image_built = True

        bt.logging.info(f"Running GPU verification container (gpus={gpus_arg!r})")
        try:
            run_result = subprocess.run(
                ["docker", "run", "--rm", "--gpus", gpus_arg, image_name],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            bt.logging.error(f"GPU container run failed (exit {exc.returncode})")
            combined = (exc.stdout or "") + (exc.stderr or "")
            if combined.strip():
                bt.logging.error(combined.strip())
            return False

        output = (run_result.stdout or "") + (run_result.stderr or "")
        if _SUCCESS_MARKER not in output:
            bt.logging.error(
                "GPU container exited 0 but did not print the expected success marker"
            )
            if output.strip():
                bt.logging.error(output.strip())
            return False

        bt.logging.info("GPU verification container test passed")
        return True
    finally:
        if image_built:
            bt.logging.info(f"Removing GPU verification image '{image_name}'")
            _remove_gpu_verification_image(image_name)
