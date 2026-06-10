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

import os
import subprocess
from typing import Optional

import bittensor as bt

from qbittensor.validator.solution.constants import (
    DOCKER_BUILD_LOG_FILENAME,
    MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES,
)
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.exceptions.validation_errors import ValidationErrors
from .docker_ops import DockerOps


def _inspect_image_size_bytes(image_name: str) -> int | None:
    """Return image size in bytes from ``docker image inspect``, or None on failure."""
    cmd = ["docker", "image", "inspect", image_name, "--format", "{{.Size}}"]
    try:
        ops = DockerOps()
        size_str = ops.image_inspect(image_name, "{{.Size}}")
        if size_str is None:
            return None
        return int(size_str)
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
    DockerOps().rmi(image_name, force=True)


def build_image(image_name: str, dockerfile_dir: str = ".", build_log_path: Optional[str] = None) -> bool:
    """
    Build the miner solution Docker image.

    Uses ``--progress=plain`` so the output is line-oriented and suitable for
    persistent logs (no TTY progress bars). When ``build_log_path`` is provided,
    the full build transcript (stdout+stderr combined) is written to that file
    **regardless of success or failure**. This allows the build logs to be
    included in the platform log package (via ``log_data_key``) for diagnostics.

    The build log is written to e.g. ``<workspace>/output/docker_build.log``.
    """
    bt.logging.info("🧱 Building docker image")
    bt.logging.info(f"\tImage name: {image_name}")
    if build_log_path:
        bt.logging.info(f"\tBuild log: {build_log_path}")

    build_cmd = ["build", "--progress=plain", "-t", image_name, dockerfile_dir]

    build_output_lines: list[str] = []
    ops = DockerOps()

    try:
        # Stream the build so we get live progress in validator logs and can
        # persist the complete transcript to the build log file on disk.
        proc = ops.popen(
            build_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line buffered
        )

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                # Keep raw line (without forcing strip so we preserve structure)
                stripped = line.rstrip("\n\r")
                build_output_lines.append(line)  # keep original newlines for file
                # Log at debug to avoid flooding main log for very long builds;
                # operators can still see key steps, and the full log is uploaded.
                bt.logging.debug(f"   [docker build] {stripped}")
        finally:
            # Ensure we wait for process and capture any final output
            remaining, _ = proc.communicate()
            if remaining:
                for line in remaining.splitlines(keepends=True):
                    build_output_lines.append(line)
                    bt.logging.debug(f"   [docker build] {line.rstrip('\n\r')}")

        if proc.returncode != 0:
            # Build failed — write what we have (important for diagnostics) then raise rich error
            _write_build_log(build_log_path, build_output_lines, build_cmd)
            # Re-raise via the shared error path so callers get consistent rich diagnostics
            # (the exception message will be sent as the Failure "reason" text).
            # We synthesize a CalledProcessError-like failure for _run_docker_command style messaging.
            stdout_text = "".join(build_output_lines)
            raise subprocess.CalledProcessError(
                returncode=proc.returncode or 1,
                cmd=build_cmd,
                output=stdout_text,
                stderr="",
            )

        # Success path — persist the log
        _write_build_log(build_log_path, build_output_lines, build_cmd)
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

    except subprocess.CalledProcessError as e:
        # Convert to our rich InvalidSolutionError (mirrors what _run_docker_command does)
        returncode = e.returncode
        stdout = (e.output or "").strip() or "(no output captured)"
        # The build log file (if provided) already contains the full transcript.
        detail = f"docker build for image {image_name} failed with exit code {returncode}"
        bt.logging.error(
            f"❌ {detail}\n"
            f"   Command: {' '.join(build_cmd)}\n"
            f"   stdout:\n{stdout[:8000]}"  # bound the main log; full content is in the uploaded log file
        )

        platform_msg = (
            f"{detail}\n\n"
            f"Command: {' '.join(build_cmd)}\n"
            f"Exit code: {returncode}\n\n"
            f"Full build output is captured in {DOCKER_BUILD_LOG_FILENAME} (uploaded with this submission for diagnostics).\n\n"
            f"Last output:\n{stdout[-4000:] if len(stdout) > 4000 else stdout}"
        )
        raise InvalidSolutionError(message=platform_msg) from e

    except FileNotFoundError as e:
        msg = (
            f"Docker CLI not found while building image {image_name}. "
            "The 'docker' executable is not available in the PATH of the validator process. "
            "Is Docker installed and properly integrated (especially on WSL)?"
        )
        bt.logging.error(f"❌ {msg} | command={' '.join(build_cmd)} | {e}")
        raise InvalidSolutionError(message=msg) from e

    except InvalidSolutionError:
        # Size / inspect errors etc. — already proper
        raise

    except Exception as e:
        msg = f"Unexpected error while building Docker image: {e}"
        bt.logging.error(f"\t❌ {msg}")
        raise InvalidSolutionError(message=msg) from e


def _write_build_log(
    build_log_path: Optional[str],
    output_lines: list[str],
    build_cmd: list[str],
) -> None:
    """Persist the captured build output to the provided path (best effort)."""
    if not build_log_path:
        return
    try:
        parent = os.path.dirname(build_log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(build_log_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(f"# Docker build log\n")
            f.write(f"# Command: {' '.join(build_cmd)}\n")
            f.write(f"# Captured with --progress=plain\n\n")
            f.writelines(output_lines)
        bt.logging.info(f"\t📝 Wrote build log to {build_log_path}")
    except Exception as e:
        bt.logging.warning(f"⚠️ Failed to write docker build log to {build_log_path}: {e}")
