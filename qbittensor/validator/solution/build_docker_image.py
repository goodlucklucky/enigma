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


def _inspect_image_size_bytes(image_name: str) -> int | None:
    """Return image size in bytes from ``docker image inspect``, or None on failure."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image_name, "--format", "{{.Size}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        bt.logging.error(f"\t❌ Failed to inspect image size for '{image_name}': {e}")
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
    try:
        subprocess.run(
            ["docker", "build", "-t", image_name, dockerfile_dir],
            check=True,
        )
        bt.logging.info("\t✅ Docker image built")

        size_bytes = _inspect_image_size_bytes(image_name)
        if size_bytes is None:
            _delete_image(image_name)
            return False

        if size_bytes > MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES:
            bt.logging.error(
                f"\t❌ Image '{image_name}' size {size_bytes} bytes exceeds limit "
                f"{MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES} bytes; removing image"
            )
            _delete_image(image_name)
            return False

        bt.logging.info(
            f"\t✅ Image size {size_bytes} bytes within limit "
            f"({MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES} bytes)"
        )
        return True

    except subprocess.CalledProcessError as e:
        bt.logging.error("\t❌ Build failed!")
        bt.logging.error(f"\tExit code: {e.returncode}")
        return False

    except Exception as e:
        bt.logging.error(f"\t❌ Failed to build docker image: {e}")
        return False
