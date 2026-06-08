# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from qbittensor.challenges.solution_output import extract_artifacts, split_on_separator

# Import platform constants and helpers for parity with the validator.
# These ensure the workbench applies the same limits and hardening that the
# real validator will enforce on submitted solutions.
from qbittensor.validator.solution.constants import (
    MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES,
    SOLUTION_STDOUT_MAX_BYTES_DEFAULT,
    SOLUTION_STDOUT_MAX_BYTES_ENV,
    VALIDATOR_DOCKER_CPU_LIMIT_ENV,
    VALIDATOR_DOCKER_GPUS_ENV,
    VALIDATOR_MEMORY_LIMIT_ENV,
)
from qbittensor.validator.solution.run_solution import docker_run_security_args

DEFAULT_WALL_TIME = 4 * 3600  # 4 hours


@dataclass
class RunResult:
    success: bool
    exit_code: int
    log: str
    duration: float


def check_docker() -> bool:
    """Check if Docker is available."""
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True, check=True, timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _find_challenges_package() -> str | None:
    """Locate the challenges package (vendored as enigma_challenges for solution containers) relative to this file."""
    # Walk up from workbench/runner/ to repo root
    repo_root = Path(__file__).resolve().parent.parent.parent
    pkg = repo_root / "qbittensor" / "challenges"
    return str(pkg) if pkg.exists() else None


def _inspect_image_size_bytes(image_name: str) -> int | None:
    """Return image size in bytes (same approach as the validator)."""
    cmd = ["docker", "image", "inspect", image_name, "--format", "{{.Size}}"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return None


def _get_stdout_max_bytes() -> int:
    """Return the max stdout bytes the validator will accept (env overridable)."""
    raw = os.getenv(
        SOLUTION_STDOUT_MAX_BYTES_ENV,
        str(SOLUTION_STDOUT_MAX_BYTES_DEFAULT),
    ).strip()
    try:
        return int(raw)
    except ValueError:
        return SOLUTION_STDOUT_MAX_BYTES_DEFAULT


def _get_workbench_docker_args() -> list[str]:
    """
    Return docker run hardening arguments, using the exact same logic and
    constants as the validator for security parity.

    We deliberately relax some capacity-oriented limits on the workbench
    (cpus, memory, gpus, pids-limit, nofile ulimit) by default, because typical
    developer machines are much smaller than validator hosts. These limits are
    only applied if you explicitly set the corresponding VALIDATOR_* env var.
    """
    resource_envs = (
        VALIDATOR_DOCKER_CPU_LIMIT_ENV,
        VALIDATOR_MEMORY_LIMIT_ENV,
        VALIDATOR_DOCKER_GPUS_ENV,
    )

    saved: dict[str, str | None] = {}
    for env_name in resource_envs:
        current = os.environ.get(env_name)
        saved[env_name] = current
        if not current or not current.strip():
            # Force empty so the shared function skips emitting the resource flag.
            os.environ[env_name] = ""

    try:
        return docker_run_security_args()
    finally:
        for env_name, original in saved.items():
            if original is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = original


def build_image(solution_dir: str, challenge_type: str) -> RunResult:
    """Build a Docker image from the solution directory.

    Creates a temporary build context that includes both the solution
    directory contents and the challenges package (laid out as 'enigma_challenges'
    so that solution Dockerfiles and their imports continue to work).
    """
    image_name = f"workbench-test-{challenge_type}"
    start = time.time()

    challenges_pkg = _find_challenges_package()

    try:
        # Build in a temp context that merges solution + challenges package
        with tempfile.TemporaryDirectory(prefix="workbench-build-") as ctx:
            # Copy solution contents into the build context
            for item in Path(solution_dir).iterdir():
                if item.is_symlink():
                    continue
                dest = Path(ctx) / item.name
                if item.is_dir():
                    def _no_symlinks(path: str, names: list[str]) -> list[str]:
                        return [n for n in names if not os.path.islink(os.path.join(path, n))]
                    shutil.copytree(item, dest, ignore=_no_symlinks)
                else:
                    shutil.copy2(item, dest)

            # Copy challenges package if found; lay it out under the name expected by
            # solution Dockerfiles (enigma_challenges top-level for their COPY + imports).
            if challenges_pkg:
                shutil.copytree(
                    challenges_pkg,
                    Path(ctx) / "enigma_challenges",
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.egg-info", ".venv", "node_modules",
                    ),
                )

            result = subprocess.run(
                ["docker", "build", "--platform", "linux/amd64",
                 "-t", image_name, ctx],
                capture_output=True, text=True, timeout=600,
            )
            duration = time.time() - start

            if result.returncode == 0:
                # Enforce the same post-build image size limit the validator uses.
                size_bytes = _inspect_image_size_bytes(image_name)
                if size_bytes is not None and size_bytes > MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES:
                    # Remove the oversized image (matches validator behavior in build_docker_image.py).
                    subprocess.run(
                        ["docker", "rmi", "-f", image_name],
                        capture_output=True, check=False
                    )
                    return RunResult(
                        success=False,
                        exit_code=result.returncode,
                        log=(
                            result.stdout + result.stderr +
                            f"\n\n❌ Image size {size_bytes} bytes exceeds limit "
                            f"{MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES} bytes. "
                            "Image removed. This solution would be rejected by the validator."
                        ),
                        duration=duration,
                    )

            return RunResult(
                success=result.returncode == 0,
                exit_code=result.returncode,
                log=result.stdout + result.stderr,
                duration=duration,
            )
    except subprocess.TimeoutExpired:
        return RunResult(
            success=False, exit_code=-1,
            log="Docker build timed out after 600s",
            duration=time.time() - start,
        )


def run_container(
    challenge_type: str,
    challenge_id: str,
    problem_json: str,
    output_dir: str,
    timeout: int = DEFAULT_WALL_TIME,
    qasm_file: str | None = None,
    env_vars: dict[str, str] | None = None,
    network: bool = False,
) -> RunResult:
    """Run a Docker container with the solver.

    Matches the validator contract: no output volume mount. The solver writes
    its results to stdout using the solution output protocol (logs, separator,
    base64 zip). After the container exits, stdout is captured and artifacts
    are extracted into output_dir.

    Security hardening (read-only root, restricted tmpfs, dropped caps,
    no-new-privileges, pids limit, forced non-root user, etc.) is applied using
    the exact same constants and logic as the validator for parity.

    Resource limits (--cpus, --memory, --gpus) use the validator's values only
    if you explicitly export the corresponding VALIDATOR_* environment variables.
    This avoids failures on normal developer machines that don't have 24+ cores,
    85 GiB RAM, or NVIDIA GPU passthrough.

    Args:
        network: If True, allow network access. Default False (matches validator).
    """
    image_name = f"workbench-test-{challenge_type}"

    cmd = [
        "docker", "run", "--rm",
        "--platform", "linux/amd64",
    ]

    if not network:
        cmd.extend(["--network", "none"])

    # Apply platform hardening for parity.
    # Security/isolation flags come from the same source the validator uses.
    # Resource limits (cpus/memory/gpus) are relaxed by default for local dev
    # machines unless the user explicitly sets the validator env vars.
    cmd.extend(_get_workbench_docker_args())

    # No -v for output — we capture stdout instead (matches validator contract).

    if qasm_file:
        qasm_abs = os.path.abspath(qasm_file)
        cmd.extend(["-v", f"{qasm_abs}:/app/peaked-circuit.qasm:ro"])

    if env_vars:
        for key, value in env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])

    cmd.extend([image_name, challenge_id, problem_json])

    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
        )
        duration = time.time() - start

        raw_stdout = result.stdout or b""
        container_exit = result.returncode

        # Enforce the same stdout cap the validator uses when reading container output.
        # Oversized stdout means the solution is misbehaving (matches validator
        # behavior in run_solution.extract_stdout_output).
        max_stdout = _get_stdout_max_bytes()
        stdout_exceeded = len(raw_stdout) > max_stdout
        if stdout_exceeded:
            raw_stdout = raw_stdout[:max_stdout]

        # Extract solution artifacts from stdout (same protocol as validator).
        # If we truncated, artifacts will likely be missing/incomplete (parity).
        extract_artifacts(raw_stdout, output_dir)

        # For the log display, only include the text portion (before separator)
        logs_bytes, _, _ = split_on_separator(raw_stdout)
        log_text = logs_bytes.decode("utf-8", errors="replace")
        if result.stderr:
            log_text += "\n" + result.stderr.decode("utf-8", errors="replace")

        if stdout_exceeded:
            log_text += (
                f"\n[Workbench] stdout exceeded validator limit "
                f"({max_stdout} bytes from {SOLUTION_STDOUT_MAX_BYTES_ENV} / default). "
                "Artifacts may be incomplete or missing — this matches platform rejection behavior."
            )

        # Treat stdout overrun as a failure for the run result (even if container exit was 0),
        # so the report clearly shows the parity violation (similar to how validator refuses artifacts).
        run_success = (container_exit == 0) and not stdout_exceeded

        return RunResult(
            success=run_success,
            exit_code=container_exit,
            log=log_text,
            duration=duration,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            success=False, exit_code=-1,
            log=f"Container timed out after {timeout}s",
            duration=time.time() - start,
        )
