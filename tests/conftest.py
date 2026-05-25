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
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MOCK_SOLUTION_DOCKER_CONTEXT = (
    ROOT / "workbench" / "challenges" / "mock_challenge" / "example_solution"
)
MOCK_SOLUTION_TEST_IMAGE = "enigma-pytest-mock-solution:latest"
root = str(ROOT)
if root not in sys.path:
    sys.path.insert(0, root)


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Isolated ENIGMA_DATA_DIR for SQLite tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ENIGMA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("ENIGMA_REPO_ROOT", raising=False)
    return data_dir


@pytest.fixture
def validator_db(tmp_data_dir):
    from qbittensor.database.db_connection import DBConnection

    return DBConnection("challenge_solutions", "5TestHotkeyForDb")


@pytest.fixture
def miner_db(tmp_data_dir):
    from qbittensor.database.db_connection import DBConnection

    return DBConnection("miner_submissions", "5TestHotkeyForDb")


def _docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            check=True,
            timeout=15,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(scope="session")
def docker_daemon():
    if not _docker_available():
        pytest.skip("Docker daemon not available")
    if not MOCK_SOLUTION_DOCKER_CONTEXT.is_dir():
        pytest.skip(f"Mock solution context missing: {MOCK_SOLUTION_DOCKER_CONTEXT}")
    return True


@pytest.fixture(scope="session")
def mock_solution_image(docker_daemon):
    build = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            MOCK_SOLUTION_TEST_IMAGE,
            str(MOCK_SOLUTION_DOCKER_CONTEXT),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if build.returncode != 0:
        pytest.fail(
            "Failed to build mock_solution image:\n"
            f"{(build.stderr or build.stdout).strip()}"
        )
    yield MOCK_SOLUTION_TEST_IMAGE
    subprocess.run(
        ["docker", "rmi", "-f", MOCK_SOLUTION_TEST_IMAGE],
        capture_output=True,
        check=False,
    )
