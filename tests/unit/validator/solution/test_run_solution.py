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

import base64
import io
import json
import subprocess
import uuid
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from qbittensor.validator.solution.constants import (
    CHALLENGE_INPUT_DIRNAME,
    CONTAINER_CHALLENGE_INPUT_PATH,
    CONTAINER_OUTPUT_DIRNAME,
    CONTAINER_SOLUTION_DIRNAME,
    SOLUTION_LOG_FILENAME,
    SOLUTION_OUTPUT_SEPARATOR,
    SOLUTION_OUTPUT_ZIP_FILENAME,
    VALIDATOR_DOCKER_CPU_LIMIT_DEFAULT,
    VALIDATOR_MEMORY_LIMIT_DEFAULT,
    VALIDATOR_DOCKER_PIDS_LIMIT_DEFAULT,
    VALIDATOR_DOCKER_TMPFS_DEFAULT,
    VALIDATOR_DOCKER_ULIMIT_NOFILE_DEFAULT,
    VALIDATOR_DOCKER_MINER_USER_DEFAULT,
)
from qbittensor.validator.solution.run_solution import (
    docker_run_security_args,
    extract_stdout_output,
    prepare_challenge_input_mount_dir,
    run_image_detached,
)
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.run import clean_up_failed_solution, run_solution_management


def _build_stdout_with_payload(logs: bytes, payload: bytes, separator_count: int = 1, *, encode: bool = True) -> bytes:
    """Assemble a fake container stdout stream: logs + N separators + base64(payload).

    Set *encode=False* to inject a raw (non-base64) payload for negative tests.
    """
    encoded = base64.b64encode(payload) + b"\n" if encode else payload
    return logs + (SOLUTION_OUTPUT_SEPARATOR * separator_count) + encoded


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestMountHelpers:
    def test_prepare_challenge_input_mount_dir_is_fresh(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        stale = workspace / CHALLENGE_INPUT_DIRNAME
        stale.mkdir()
        (stale / "old.txt").write_text("stale")

        mount_dir = prepare_challenge_input_mount_dir(str(workspace))
        assert mount_dir == str(stale)
        assert stale.is_dir()
        assert not (stale / "old.txt").exists()


class TestExtractStdoutOutput:
    def test_splits_logs_and_solution_zip(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        zip_bytes = _make_zip({"result.json": '{"hello": "world"}', "output.txt": "Hello"})
        stdout = _build_stdout_with_payload(b"line1\nline2\n", zip_bytes)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=stdout, returncode=0)
            assert extract_stdout_output("ctr", str(workspace)) is True

        cmd = mock_run.call_args.args[0]
        assert cmd == ["docker", "logs", "ctr"]

        log_path = workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_LOG_FILENAME
        assert log_path.is_file()
        assert log_path.read_bytes() == b"line1\nline2\n"

        zip_path = workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_OUTPUT_ZIP_FILENAME
        assert zip_path.is_file()
        assert zip_path.read_bytes() == zip_bytes

        artifacts_dir = workspace / CONTAINER_OUTPUT_DIRNAME / CONTAINER_SOLUTION_DIRNAME
        assert (artifacts_dir / "result.json").is_file()
        assert (artifacts_dir / "output.txt").read_text() == "Hello"

    def test_only_first_separator_delimits(self, tmp_path):
        """A second SOLUTION_OUTPUT_SEPARATOR inside the payload must be preserved."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        zip_bytes = _make_zip({"output.txt": "Hello"})
        b64_payload = base64.b64encode(zip_bytes) + b"\n"
        stdout = b"logs\n" + SOLUTION_OUTPUT_SEPARATOR + b64_payload

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=stdout, returncode=0)
            assert extract_stdout_output("ctr", str(workspace)) is True
        zip_path = workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_OUTPUT_ZIP_FILENAME
        assert zip_path.read_bytes() == zip_bytes

        # Case 2: an accidental separator earlier in logs — split at the first.
        ws2 = tmp_path / "workspace2"
        ws2.mkdir()
        stdout_logs_have_sep = SOLUTION_OUTPUT_SEPARATOR + b"logs\n" + SOLUTION_OUTPUT_SEPARATOR + b64_payload
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=stdout_logs_have_sep, returncode=0)
            # First separator splits at offset 0 → logs are empty,
            # payload starts with "logs\n<separator><b64>" which is not valid base64.
            assert extract_stdout_output("ctr", str(ws2)) is False
        log_path = ws2 / CONTAINER_OUTPUT_DIRNAME / SOLUTION_LOG_FILENAME
        assert log_path.read_bytes() == b""

    def test_missing_separator_writes_logs_only(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"oops no marker\n", returncode=0)
            assert extract_stdout_output("ctr", str(workspace)) is True

        log_path = workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_LOG_FILENAME
        assert log_path.is_file()
        assert log_path.read_bytes() == b"oops no marker\n"
        assert not (workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_OUTPUT_ZIP_FILENAME).exists()
        # Artifacts directory is now created early so diagnostics can be written into it
        artifacts_dir = workspace / CONTAINER_OUTPUT_DIRNAME / CONTAINER_SOLUTION_DIRNAME
        assert artifacts_dir.is_dir()
        assert (artifacts_dir / "extraction_diagnostics.txt").is_file()

    def test_invalid_zip_payload_returns_false_but_logs_kept(self, tmp_path):
        """Payload is valid base64 but decoded bytes are not a valid zip."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        not_a_zip = b"this-is-not-a-zip"
        stdout = _build_stdout_with_payload(b"log\n", not_a_zip)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=stdout, returncode=0)
            assert extract_stdout_output("ctr", str(workspace)) is False

        log_path = workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_LOG_FILENAME
        assert log_path.read_bytes() == b"log\n"
        zip_path = workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_OUTPUT_ZIP_FILENAME
        assert zip_path.read_bytes() == not_a_zip
        artifacts_dir = workspace / CONTAINER_OUTPUT_DIRNAME / CONTAINER_SOLUTION_DIRNAME
        assert artifacts_dir.is_dir()
        # Directory now contains the extraction diagnostics file (new behavior)
        files = list(artifacts_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "extraction_diagnostics.txt"

    def test_invalid_base64_payload_returns_false(self, tmp_path):
        """Payload after separator is not valid base64 at all."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        stdout = _build_stdout_with_payload(b"log\n", b"!!!not-base64!!!", encode=False)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=stdout, returncode=0)
            assert extract_stdout_output("ctr", str(workspace)) is False

        log_path = workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_LOG_FILENAME
        assert log_path.read_bytes() == b"log\n"
        assert not (workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_OUTPUT_ZIP_FILENAME).exists()

    def test_truncates_when_stdout_exceeds_cap(self, tmp_path, monkeypatch):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        monkeypatch.setenv("VALIDATOR_SOLUTION_STDOUT_MAX_BYTES", "16")
        zip_bytes = _make_zip({"output.txt": "Hello"})
        stdout = _build_stdout_with_payload(b"x" * 32, zip_bytes)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=stdout, returncode=0)
            assert extract_stdout_output("ctr", str(workspace)) is False

        log_path = workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_LOG_FILENAME
        # Logs file holds only the truncated prefix; no artifact extraction at all.
        assert len(log_path.read_bytes()) == 16
        assert not (workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_OUTPUT_ZIP_FILENAME).exists()

    def test_docker_logs_failure_returns_false(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "docker logs", stderr=b"no such container"),
        ):
            assert extract_stdout_output("ctr", str(workspace)) is False
        assert not (workspace / CONTAINER_OUTPUT_DIRNAME / SOLUTION_LOG_FILENAME).exists()

    def test_rejects_zip_with_path_traversal(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # Build a zip that tries to escape the destination directory.
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../escape.txt", "pwn")
        stdout = _build_stdout_with_payload(b"log\n", evil.getvalue())

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=stdout, returncode=0)
            assert extract_stdout_output("ctr", str(workspace)) is False

        assert not (workspace.parent / "escape.txt").exists()


class TestRunImageDetached:
    def test_starts_container_and_returns_id(self, tmp_path):
        host_folder = tmp_path / "solution"
        host_folder.mkdir()
        mount_dir = prepare_challenge_input_mount_dir(str(host_folder))
        (Path(mount_dir) / "input.txt").write_text("input")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="container-id-123\n", returncode=0)
            cid = run_image_detached(
                image_name="img",
                container_name="ctr",
                validator_label="val_label",
                challenge_input_mount_dir=mount_dir,
            )

        assert cid == "container-id-123"
        args = mock_run.call_args.args[0]
        assert "docker" in args
        assert "--network" in args
        assert "none" in args
        assert "--pids-limit" in args
        assert "128" in args
        assert "--ulimit" in args
        assert "nofile=1024:1024" in args
        assert "--cap-drop" in args
        assert "ALL" in args
        assert "--security-opt" in args
        assert "no-new-privileges:true" in args
        assert "--read-only" in args
        assert VALIDATOR_DOCKER_TMPFS_DEFAULT in args
        assert "--user" in args
        assert VALIDATOR_DOCKER_MINER_USER_DEFAULT in args
        assert "--cpus" in args
        assert VALIDATOR_DOCKER_CPU_LIMIT_DEFAULT in args
        assert "--memory" in args
        assert VALIDATOR_MEMORY_LIMIT_DEFAULT in args
        assert "--memory-swap" in args
        mount_spec = f"{mount_dir}:{CONTAINER_CHALLENGE_INPUT_PATH}:ro"
        assert mount_spec in args
        # /output must NOT appear in any volume/bind/tmpfs spec — output is delivered
        # entirely via stdout now, so the container gets no shared writable mount.
        v_indices = [i for i, a in enumerate(args) if a == "-v"]
        for i in v_indices:
            assert "/output" not in args[i + 1], (
                f"unexpected /output volume mount: {args[i + 1]}"
            )
        tmpfs_indices = [i for i, a in enumerate(args) if a == "--tmpfs"]
        for i in tmpfs_indices:
            assert "/output" not in args[i + 1], (
                f"unexpected /output tmpfs mount: {args[i + 1]}"
            )
        # Host ``output/`` should not be pre-created by run_image_detached.
        assert not (host_folder / "output").exists()


class TestDockerRunSecurityArgs:
    def test_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            args = docker_run_security_args()
        assert ["--stop-timeout", "30"] == args[:2]
        assert ["--pids-limit", "128"] == args[2:4]
        assert "--ulimit" in args
        assert "nofile=1024:1024" in args
        assert ["--cap-drop", "ALL"] == args[args.index("--cap-drop"): args.index("--cap-drop") + 2]
        assert "--security-opt" in args
        assert "--read-only" in args
        assert "--tmpfs" in args
        # No /output tmpfs / volume is configurable anymore.
        for i, token in enumerate(args):
            if token == "--tmpfs":
                assert "/output" not in args[i + 1]
        assert ["--user", VALIDATOR_DOCKER_MINER_USER_DEFAULT] == args[
            args.index("--user"): args.index("--user") + 2
        ]
        assert ["--cpus", VALIDATOR_DOCKER_CPU_LIMIT_DEFAULT] == args[
            args.index("--cpus"): args.index("--cpus") + 2
        ]
        mem_idx = args.index("--memory")
        assert ["--memory", VALIDATOR_MEMORY_LIMIT_DEFAULT] == args[mem_idx: mem_idx + 2]
        swap_idx = args.index("--memory-swap")
        assert ["--memory-swap", VALIDATOR_MEMORY_LIMIT_DEFAULT] == args[swap_idx: swap_idx + 2]

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("VALIDATOR_DOCKER_PIDS_LIMIT", "64")
        monkeypatch.setenv("VALIDATOR_DOCKER_ULIMIT_NOFILE", "512:512")
        monkeypatch.setenv("VALIDATOR_DOCKER_CAP_DROP", "NET_RAW")
        monkeypatch.setenv("VALIDATOR_DOCKER_NO_NEW_PRIVILEGES", "false")
        monkeypatch.setenv("VALIDATOR_DOCKER_READ_ONLY", "0")
        monkeypatch.setenv("VALIDATOR_DOCKER_TMPFS", "/tmp:size=128m")
        args = docker_run_security_args()
        assert "--pids-limit" in args and "64" in args
        assert "nofile=512:512" in args
        assert "--cap-drop" in args and "NET_RAW" in args
        assert "--security-opt" not in args
        assert "--read-only" not in args
        assert "/tmp:size=128m" in args

    def test_non_root_user_env_override(self, monkeypatch):
        monkeypatch.setenv("VALIDATOR_DOCKER_MINER_USER", "10001:10001")
        args = docker_run_security_args()
        assert ["--user", "10001:10001"] == args[args.index("--user"): args.index("--user") + 2]

    def test_empty_non_root_user_disables_user_flag(self, monkeypatch):
        monkeypatch.setenv("VALIDATOR_DOCKER_MINER_USER", "")
        args = docker_run_security_args()
        assert "--user" not in args

    def test_cpu_limit_env_override(self, monkeypatch):
        monkeypatch.setenv("VALIDATOR_DOCKER_CPU_LIMIT", "1.5")
        args = docker_run_security_args()
        assert ["--cpus", "1.5"] == args[args.index("--cpus"): args.index("--cpus") + 2]

    def test_empty_cpu_limit_disables_cpus_flag(self, monkeypatch):
        monkeypatch.setenv("VALIDATOR_DOCKER_CPU_LIMIT", "")
        args = docker_run_security_args()
        assert "--cpus" not in args

    def test_memory_limit_env_override(self, monkeypatch):
        monkeypatch.setenv("VALIDATOR_MEMORY_LIMIT", "512m")
        args = docker_run_security_args()
        mem_idx = args.index("--memory")
        assert args[mem_idx: mem_idx + 4] == ["--memory", "512m", "--memory-swap", "512m"]

    def test_empty_memory_limit_disables_memory_flags(self, monkeypatch):
        monkeypatch.setenv("VALIDATOR_MEMORY_LIMIT", "")
        args = docker_run_security_args()
        assert "--memory" not in args
        assert "--memory-swap" not in args

    def test_docker_failure_raises_invalid_solution(self, tmp_path):
        host_folder = tmp_path / "solution"
        host_folder.mkdir()
        mount_dir = prepare_challenge_input_mount_dir(str(host_folder))

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "docker", stderr="fail"),
        ):
            with pytest.raises(InvalidSolutionError):
                run_image_detached(
                    "img", "ctr", "label", mount_dir
                )


@pytest.mark.integration
class TestMockSolutionDockerIntegration:
    """Build and run workbench mock_solution with validator docker hardening."""

    def test_mock_solution_runs_with_hardened_container(
        self, mock_solution_image, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("VALIDATOR_DOCKER_PIDS_LIMIT", raising=False)
        monkeypatch.delenv("VALIDATOR_DOCKER_ULIMIT_NOFILE", raising=False)
        monkeypatch.delenv("VALIDATOR_DOCKER_CAP_DROP", raising=False)
        monkeypatch.delenv("VALIDATOR_DOCKER_NO_NEW_PRIVILEGES", raising=False)
        monkeypatch.delenv("VALIDATOR_DOCKER_READ_ONLY", raising=False)
        monkeypatch.delenv("VALIDATOR_DOCKER_TMPFS", raising=False)
        monkeypatch.delenv("VALIDATOR_DOCKER_MINER_USER", raising=False)
        monkeypatch.delenv("VALIDATOR_DOCKER_CPU_LIMIT", raising=False)
        monkeypatch.delenv("VALIDATOR_MEMORY_LIMIT", raising=False)
        monkeypatch.delenv("VALIDATOR_SOLUTION_STDOUT_MAX_BYTES", raising=False)

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        mount_dir = prepare_challenge_input_mount_dir(str(workspace))
        (Path(mount_dir) / "unused.txt").write_text("mock challenge input\n")

        container_name = f"enigma-test-mock-{uuid.uuid4().hex[:12]}"
        container_id = None
        try:
            container_id = run_image_detached(
                image_name=mock_solution_image,
                container_name=container_name,
                validator_label="pytest_validator",
                challenge_input_mount_dir=mount_dir,
            )

            wait = subprocess.run(
                ["docker", "wait", container_id],
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            assert wait.stdout.strip() == "0", (
                f"mock_solution exited non-zero; logs:\n"
                f"{subprocess.run(['docker', 'logs', container_id], capture_output=True, text=True).stderr}"
            )

            assert extract_stdout_output(container_id, str(workspace)) is True

            artifacts_dir = (
                workspace / CONTAINER_OUTPUT_DIRNAME / CONTAINER_SOLUTION_DIRNAME
            )
            result_json = artifacts_dir / "result.json"
            assert result_json.is_file(), (
                "mock_solution did not emit result.json in its stdout-delivered zip"
            )
            payload = json.loads(result_json.read_text(encoding="utf-8"))
            assert payload.get("status") == "success"
            assert payload.get("signature")
            assert payload.get("payload")

            inspect = subprocess.run(
                ["docker", "inspect", container_id, "--format", "{{json .HostConfig}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            host_config = json.loads(inspect.stdout)
            assert host_config["PidsLimit"] == int(VALIDATOR_DOCKER_PIDS_LIMIT_DEFAULT)
            assert host_config["NanoCpus"] == int(
                float(VALIDATOR_DOCKER_CPU_LIMIT_DEFAULT) * 1_000_000_000
            )
            assert host_config["Memory"] == int(VALIDATOR_MEMORY_LIMIT_DEFAULT)
            assert host_config["MemorySwap"] == int(VALIDATOR_MEMORY_LIMIT_DEFAULT)
            assert host_config["ReadonlyRootfs"] is True
            assert "ALL" in host_config.get("CapDrop", [])
            assert any(
                "no-new-privileges" in opt
                for opt in host_config.get("SecurityOpt", [])
            )
            tmpfs = host_config.get("Tmpfs") or {}
            assert "/tmp" in tmpfs
            assert "noexec" in tmpfs["/tmp"]
            assert "nosuid" in tmpfs["/tmp"]
            assert "/output" not in tmpfs, "stdout-only contract forbids tmpfs /output"
            binds = host_config.get("Binds") or []
            assert any(":ro" in bind and "/challenge_input" in bind for bind in binds)
            # /output must not be present as a bind mount under the stdout-only contract.
            assert not any("/output" in bind for bind in binds)

            mounts_inspect = subprocess.run(
                ["docker", "inspect", container_id, "--format", "{{json .Mounts}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            container_mounts = json.loads(mounts_inspect.stdout)
            output_mount = next(
                (m for m in container_mounts if m.get("Destination") == "/output"),
                None,
            )
            assert output_mount is None, (
                f"stdout-only contract forbids any /output mount: {output_mount}"
            )

            soft, hard = VALIDATOR_DOCKER_ULIMIT_NOFILE_DEFAULT.split(":")
            nofile = next(
                (u for u in host_config.get("Ulimits", []) if u.get("Name") == "nofile"),
                None,
            )
            assert nofile is not None
            assert nofile["Soft"] == int(soft)
            assert nofile["Hard"] == int(hard)

            user = subprocess.run(
                [
                    "docker",
                    "inspect",
                    container_id,
                    "--format",
                    "{{.Config.User}}",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert user.stdout.strip() == VALIDATOR_DOCKER_MINER_USER_DEFAULT
        finally:
            if container_id:
                subprocess.run(
                    ["docker", "rm", "-fv", container_id],
                    capture_output=True,
                    check=False,
                )


class TestCleanUpFailedSolution:
    def test_invokes_docker_and_rm(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            clean_up_failed_solution("img_name", "cid123", "/tmp/folder")
        assert mock_run.call_count == 3


class TestRunSolutionManagement:
    @patch("qbittensor.validator.solution.run.run_image_detached")
    @patch("qbittensor.validator.solution.run.validate_image", return_value=True)
    @patch("qbittensor.validator.solution.run.build_image", return_value=True)
    @patch("qbittensor.validator.solution.run.reject_dockerfile", return_value=True)
    @patch("qbittensor.validator.solution.run.validate_code", return_value=True)
    @patch("qbittensor.validator.solution.run.unzip")
    @patch("qbittensor.validator.solution.run.validate_zip", return_value=True)
    @patch("qbittensor.validator.solution.run.download_zip")
    @patch("qbittensor.validator.solution.run.setup")
    @patch("qbittensor.validator.solution.run.run_challenge_setup")
    def test_happy_path(
        self,
        mock_setup_challenge,
        mock_setup,
        mock_download,
        *_mocks,
    ):
        mock_setup.return_value = ("tag", "/tmp/tag_folder")
        mock_download.return_value = "/tmp/tag_folder/solution.zip"
        mock_setup_challenge.return_value = "/tmp/tag_folder/challenge_input.txt"

        db = Mock()
        db.db_query.has_seen_tx_hash = Mock(return_value=False)
        db.db_query.create_challenge_solution = Mock(return_value=True)
        db.db_query.update_challenge_solution = Mock(return_value=True)

        with patch(
            "qbittensor.validator.solution.run.run_image_detached",
            return_value="container-abc",
        ):
            image, container, folder = run_solution_management(
                db_conn=db,
                validator_label="val_label",
                download_url="https://example.com/z.zip",
                challenge_milestone_id="012b3e8e-b1e9-401e-ab70-f1598b34746f",
                challenge_validation_solution_id="sol-id",
                tx_hash="0xtx",
                miner_hotkey="miner_hk",
                submission_id="sub-id",
                challenge_id="challenge-id",
            )

        assert image == "tag_image"
        assert container == "container-abc"
        assert folder == "/tmp/tag_folder"
        db.db_query.create_challenge_solution.assert_called_once()
        db.db_query.update_challenge_solution.assert_called_once()

    @patch("qbittensor.validator.solution.run.clean_up_failed_solution")
    @patch("qbittensor.validator.solution.run.download_zip", return_value=None)
    @patch("qbittensor.validator.solution.run.setup", return_value=("tag", "/tmp/f"))
    def test_download_failure_returns_none_triple(self, _setup, _download, mock_cleanup):
        db = Mock()
        db.db_query.has_seen_tx_hash = Mock(return_value=False)
        db.db_query.create_challenge_solution = Mock(return_value=True)
        db.db_query.update_challenge_solution_status = Mock(return_value=True)

        result = run_solution_management(
            db_conn=db,
            validator_label="lbl",
            download_url="url",
            challenge_milestone_id="m",
            challenge_validation_solution_id="sol",
            tx_hash="tx",
            miner_hotkey="hk",
            submission_id="sub",
            challenge_id="challenge-id",
        )
        assert result == (None, None, None)
        mock_cleanup.assert_called_once()
