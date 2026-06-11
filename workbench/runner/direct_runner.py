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
import subprocess
import time
from pathlib import Path
from .docker_runner import RunResult

from qbittensor.challenges.solution_output import SOLUTION_OUTPUT_SEPARATOR


def find_entry_point(
    solution_dir: str,
    challenge_type: str,
    entry_point: str | None = None,
) -> str | None:
    """Find the solver script in the solution directory."""
    if entry_point:
        path = Path(solution_dir) / entry_point
        return str(path) if path.exists() else None

    defaults = {
        "breaking_rsa": ["breaking_rsa.py"],
        "hardening_quantum_proof": ["hardening_quantum_proof.py"],
        "mock": ["mock_solver.py"],
    }

    for name in defaults.get(challenge_type, []):
        path = Path(solution_dir) / name
        if path.exists():
            return str(path)

    return None


def run_direct(
    script_path: str,
    challenge_id: str,
    problem_json: str,
    output_dir: str,
    timeout: int = 300,
) -> RunResult:
    """Run a solver script directly as a subprocess."""
    os.makedirs(output_dir, exist_ok=True)

    env = os.environ.copy()
    env["OUTPUT_DIR"] = output_dir

    # In Docker, the challenges package is vendored as 'enigma_challenges'.
    # For direct mode, create a temporary symlink so the same import works.
    repo_root = Path(__file__).resolve().parent.parent.parent
    challenges_pkg = repo_root / "qbittensor" / "challenges"
    symlink_dir = None
    symlink_path = None
    if challenges_pkg.is_dir():
        import tempfile
        symlink_dir = tempfile.mkdtemp(prefix="workbench-pypath-")
        symlink_path = Path(symlink_dir) / "enigma_challenges"
        symlink_path.symlink_to(challenges_pkg)
        # Prepend so the solver finds enigma_challenges
        env["PYTHONPATH"] = symlink_dir + os.pathsep + env.get("PYTHONPATH", "")

    start = time.time()
    try:
        result = subprocess.run(
            ["python", script_path, challenge_id, problem_json],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        duration = time.time() - start

        # Strip the solution output separator + base64 payload from log display.
        # In direct mode the solver also writes files to OUTPUT_DIR, so we only
        # need the text portion for the log.
        log_text = result.stdout
        sep_str = SOLUTION_OUTPUT_SEPARATOR.decode("utf-8", errors="replace")
        sep_idx = log_text.find(sep_str)
        if sep_idx != -1:
            log_text = log_text[:sep_idx]
        log_text += result.stderr

        # Write stdout.log so the workbench validator finds it
        stdout_log_path = os.path.join(output_dir, "stdout.log")
        if not os.path.exists(stdout_log_path):
            with open(stdout_log_path, "w") as f:
                f.write(log_text)

        return RunResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            log=log_text,
            duration=duration,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            success=False, exit_code=-1,
            log=f"Solver timed out after {timeout}s",
            duration=time.time() - start,
        )
    except FileNotFoundError:
        return RunResult(
            success=False, exit_code=-1,
            log=f"Python interpreter or script not found: {script_path}",
            duration=time.time() - start,
        )
    finally:
        if symlink_dir:
            import shutil
            shutil.rmtree(symlink_dir, ignore_errors=True)
