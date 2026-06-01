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

import subprocess

import bittensor as bt

from qbittensor.validator.solution.constants import MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.exceptions.validation_errors import ValidationErrors
from .run_solution import _run_docker_command


def _inspect_image_size_bytes(image_name: str) -> int | None:
    """Return image size in bytes from ``docker image inspect``, or None on failure."""
    cmd = ["docker", "image", "inspect", image_name, "--format", "{{.Size}}"]
    try:
        result = _run_docker_command(cmd, description="docker image inspect for size", check=True)
        return int(result.stdout.strip())
    except InvalidSolutionError:
        # Docker not available or command failed — treat as uninspectable
        return None
    except ValueError as e:
        bt.logging.error(f"\t❌ Failed to parse image size for '{image_name}': {e}")
        return None
    except Exception as e:
        bt.logging.error(f"\t❌ Unexpected error inspecting image size for '{image_name}': {e}")
        return None


def _delete_image(image_name: str) -> None:
    subprocess.run(
        ["docker", "rmi", "-f", image_name],
        capture_output=True,
        check=False,
    )


def build_image(image_name: str, dockerfile_dir: str = ".") -> bool:
    bt.logging.info("🧱 Building docker image")
    bt.logging.info(f"\tImage name: {image_name}")

    build_cmd = ["docker", "build", "-t", image_name, dockerfile_dir]

    try:
        _run_docker_command(build_cmd, description=f"docker build for image {image_name}")
        bt.logging.info("\t✅ Docker image built")

        size_bytes = _inspect_image_size_bytes(image_name)
        if size_bytes is None:
            _delete_image(image_name)
            raise InvalidSolutionError(
                message="Docker build appeared to succeed, but we could not inspect the resulting image size. "
                        "This usually means the Docker CLI became unavailable or the image was immediately removed."
            )

        if size_bytes > MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES:
            bt.logging.error(
                f"\t❌ Image '{image_name}' size {size_bytes} bytes exceeds limit "
                f"{MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES} bytes; removing image"
            )
            _delete_image(image_name)
            raise InvalidSolutionError(
                message=f"Built image exceeds maximum allowed size ({size_bytes} > {MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES} bytes)."
            )

        bt.logging.info(
            f"\t✅ Image size {size_bytes} bytes within limit "
            f"({MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES} bytes)"
        )
        return True

    except InvalidSolutionError:
        # The helper already raised with rich diagnostics — just re-raise
        raise

    except Exception as e:
        msg = f"Unexpected error while building Docker image: {e}"
        bt.logging.error(f"\t❌ {msg}")
        raise InvalidSolutionError(message=msg) from e
