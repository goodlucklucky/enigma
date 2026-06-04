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
                dest = Path(ctx) / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
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

        # Extract solution artifacts from stdout (same protocol as validator)
        extract_artifacts(raw_stdout, output_dir)

        # For the log display, only include the text portion (before separator)
        logs_bytes, _, _ = split_on_separator(raw_stdout)
        log_text = logs_bytes.decode("utf-8", errors="replace")
        if result.stderr:
            log_text += "\n" + result.stderr.decode("utf-8", errors="replace")

        return RunResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            log=log_text,
            duration=duration,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            success=False, exit_code=-1,
            log=f"Container timed out after {timeout}s",
            duration=time.time() - start,
        )
