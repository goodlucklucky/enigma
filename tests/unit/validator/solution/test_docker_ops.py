# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including but not limited to
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

"""Unit tests for DockerOps.

These are pure unit tests that mock subprocess.run (and related) so we never call
real Docker unless in dedicated integration tests (which we are not adding here).
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from qbittensor.validator.solution.docker_ops import DockerOps
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError


class TestDockerOps:
    def test_ps_builds_correct_command(self):
        ops = DockerOps(validator_label="val_label")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ctr1\nctr2\n", returncode=0)
            ids = ops.ps(all=True, status="running", format="{{.ID}}")
            assert ids == ["ctr1", "ctr2"]
            cmd = mock_run.call_args[0][0]
            assert "docker" in cmd and "ps" in cmd and "-a" in cmd
            assert any("label=val_label" in str(c) for c in cmd)

    def test_stop_and_rm_return_success(self):
        ops = DockerOps()
        with patch.object(ops, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert ops.stop("ctr1") is True
            assert ops.rm("ctr1", volumes=True) is True

    def test_check_available_delegates(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="Docker version 24", returncode=0)
            assert DockerOps.check_available() is True


class TestDockerOpsRun:
    """Tests for DockerOps low-level run (replacement for the old centralized docker helper).

    All tests patch subprocess so this is pure unit coverage.
    """

    def test_success_returns_completed_process(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok\n", stderr="", returncode=0)
            ops = DockerOps()
            result = ops._run(["docker", "--version"])
            assert result.stdout == "ok\n"
            mock_run.assert_called_once()

    def test_file_not_found_raises_rich_invalid_solution_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("no docker")):
            with pytest.raises(InvalidSolutionError) as exc:
                ops = DockerOps()
                ops._run(["docker", "run", "foo"], description="docker run test")
        msg = str(exc.value)
        assert "Docker CLI not found" in msg
        assert "docker run test" in msg
        assert "Is Docker installed" in msg

    def test_called_process_error_127_includes_full_diagnostics(self):
        err = subprocess.CalledProcessError(127, ["docker", "run", "img"])
        err.stderr = "docker: command not found\n"
        err.stdout = ""
        with patch("subprocess.run", side_effect=err):
            with pytest.raises(InvalidSolutionError) as exc:
                ops = DockerOps()
                ops._run(
                    ["docker", "run", "img"],
                    description="docker run for container ctr",
                )
        msg = str(exc.value)
        assert "exit status 127" in msg
        assert "docker run for container ctr" in msg
        assert "Command: docker run img" in msg
        assert "Exit code: 127" in msg
        assert "docker: command not found" in msg

    def test_called_process_error_other_code_includes_stdout_and_stderr(self):
        err = subprocess.CalledProcessError(1, ["docker", "build", "."])
        err.stderr = "build failed: no space left on device"
        err.stdout = "Step 1/3 : FROM python"
        with patch("subprocess.run", side_effect=err):
            with pytest.raises(InvalidSolutionError) as exc:
                ops = DockerOps()
                ops._run(["docker", "build", "."], description="docker build")
        msg = str(exc.value)
        assert "failed with exit code 1" in msg
        assert "docker build" in msg
        assert "build failed: no space left on device" in msg
        assert "Step 1/3 : FROM python" in msg

    def test_success_path_does_not_raise(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")
            ops = DockerOps()
            result = ops._run(["docker", "ps"], check=True)
            assert result.returncode == 0
