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

import io
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from qbittensor.validator.solution import manage_files
from qbittensor.validator.solution.download_solution import download_zip
from qbittensor.validator.solution.validate_zipfile import validate_zip
from qbittensor.validator.solution.extract_solution_code import unzip, _flatten_single_top_level_dir
from qbittensor.validator.solution.build_docker_image import build_image
from qbittensor.validator.solution.validate_docker_image import validate_image
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError


class TestManageFiles:
    def test_setup_creates_folder(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("qbittensor.validator.solution.manage_files.time.time", return_value=12345):
            tag, folder = manage_files.setup("label", "sol-id")
        assert tag == "label_sol-id_12345"
        assert folder == "data/solutions/label_sol-id_12345"
        assert (tmp_path / folder).is_dir()

    def test_cleanup_removes_folder(self, tmp_path):
        folder = tmp_path / "to_remove"
        folder.mkdir()
        manage_files.cleanup(str(folder))
        assert not folder.exists()

    def test_setup_respects_enigma_data_dir_env(self, tmp_path, monkeypatch):
        """When ENIGMA_DATA_DIR is set (from --neuron.data_dir), workspaces go under <data>/solutions/."""
        custom_base = tmp_path / "custom_data"
        monkeypatch.setenv("ENIGMA_DATA_DIR", str(custom_base))
        # No chdir needed; setup will compute absolute under the env value + /solutions
        with patch("qbittensor.validator.solution.manage_files.time.time", return_value=12345):
            tag, folder = manage_files.setup("label", "sol-id")
        assert tag == "label_sol-id_12345"
        # folder will be absolute when env provides an absolute base
        expected = custom_base / "solutions" / "label_sol-id_12345"
        assert Path(folder).resolve() == expected.resolve()
        assert Path(folder).is_dir()
        # Also ensure it did not pollute a "data/" relative to cwd
        assert not (tmp_path / "data").exists()


class TestDownloadZip:
    def test_download_success(self, tmp_path):
        folder = tmp_path / "dl"
        folder.mkdir()
        with patch("urllib.request.urlretrieve") as mock_retrieve:
            mock_retrieve.return_value = (str(folder / "solution.zip"), {})
            result = download_zip("https://example.com/z.zip", str(folder))
        assert result == str(folder / "solution.zip")

    def test_download_failure_returns_none(self, tmp_path):
        folder = tmp_path / "dl"
        folder.mkdir()
        with patch("urllib.request.urlretrieve", side_effect=OSError("network")):
            assert download_zip("https://example.com/z.zip", str(folder)) is None


class TestValidateZip:
    def test_valid_zip(self, tmp_path):
        zpath = tmp_path / "test.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("a.txt", "data")
        assert validate_zip(str(zpath)) is True

    def test_invalid_zip(self, tmp_path):
        bad = tmp_path / "not.zip"
        bad.write_text("not a zip")
        assert validate_zip(str(bad)) is False

    def test_rejects_oversized_uncompressed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES", "10")
        zpath = tmp_path / "big.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("a.txt", "x" * 20)
        assert validate_zip(str(zpath)) is False

    def test_accepts_within_uncompressed_limit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES", "100")
        zpath = tmp_path / "small.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("a.txt", "x" * 20)
        assert validate_zip(str(zpath)) is True


class TestExtractSolutionCode:
    def _make_zip(self, path: Path, entries: dict[str, str]):
        with zipfile.ZipFile(path, "w") as zf:
            for name, content in entries.items():
                zf.writestr(name, content)

    def test_unzip_flat_files(self, tmp_path):
        folder = tmp_path / "work"
        folder.mkdir()
        zpath = tmp_path / "src.zip"
        self._make_zip(zpath, {"main.py": "print('hi')"})
        unzip(str(folder), str(zpath))
        assert (folder / "code" / "main.py").is_file()

    def test_unzip_flattens_single_top_level_dir(self, tmp_path):
        dest = tmp_path / "code"
        dest.mkdir()
        (dest / "wrapper").mkdir()
        (dest / "wrapper" / "Dockerfile").write_text("FROM scratch")
        _flatten_single_top_level_dir(str(dest))
        assert (dest / "Dockerfile").is_file()
        assert not (dest / "wrapper").exists()

    def test_unzip_corrupt_raises_invalid_solution(self, tmp_path):
        folder = tmp_path / "work"
        folder.mkdir()
        bad = tmp_path / "bad.zip"
        bad.write_text("corrupt")
        with pytest.raises(InvalidSolutionError):
            unzip(str(folder), str(bad))

    def test_unzip_rejects_path_traversal(self, tmp_path):
        """Miner upload zip with lexical traversal must be rejected before any Docker phase."""
        from qbittensor.validator.solution.extract_solution_code import unzip as _unzip

        work = tmp_path / "work"
        work.mkdir()
        zpath = tmp_path / "evil.zip"
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../outside.txt", "pwned")
            zf.writestr("code/main.py", "print(1)")
        zpath.write_bytes(evil.getvalue())

        with pytest.raises(InvalidSolutionError):
            _unzip(str(work), str(zpath))

        # Nothing should have escaped
        assert not (tmp_path / "outside.txt").exists()

    def test_unzip_rejects_symlink_members(self, tmp_path):
        """Zip containing a symlink entry (via ZipInfo mode) must be refused."""
        work = tmp_path / "work"
        work.mkdir()
        zpath = tmp_path / "link.zip"

        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as zf:
            # Construct a symlink entry (S_IFLNK | 0777)
            info = zipfile.ZipInfo("data_link")
            info.external_attr = (0o120777 << 16)
            info.file_size = 0
            zf.writestr(info, "/etc/passwd")  # target hint; mode triggers symlink creation on extract

            # A regular file too
            zf.writestr("main.py", "print('hi')")

        zpath.write_bytes(evil.getvalue())

        with pytest.raises(InvalidSolutionError):
            unzip(str(work), str(zpath))

    def test_unzip_enforces_actual_byte_budget(self, tmp_path, monkeypatch):
        """Even if declared sizes were small, actual decompressed bytes are counted."""
        monkeypatch.setenv("VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES", "10")
        work = tmp_path / "work"
        work.mkdir()
        zpath = tmp_path / "bigish.zip"
        # Truthful large content; declared will also be large so precheck catches,
        # but this exercises the streaming max_bytes path inside safe extraction.
        self._make_zip(zpath, {"big.txt": "X" * 100})
        with pytest.raises(InvalidSolutionError):
            unzip(str(work), str(zpath))


class TestDockerBuildAndValidate:
    def _make_popen_mock(self, stdout_text: str, returncode: int = 0) -> MagicMock:
        """Create a Popen mock that yields lines from stdout_text (for --progress=plain streaming)."""
        mock_proc = MagicMock()
        # Iteration over .stdout yields lines (as the real Popen does in text mode)
        mock_proc.stdout = io.StringIO(stdout_text)
        mock_proc.returncode = returncode
        # communicate is called in finally for any trailing data
        mock_proc.communicate.return_value = (stdout_text, "")
        return mock_proc

    def test_build_image_success(self):
        from qbittensor.validator.solution.constants import MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES

        max_bytes = MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES

        build_stdout = (
            "Step 1/2 : FROM python:3.11-slim\n"
            " ---> Using cache\n"
            "Step 2/2 : COPY . /app\n"
            " ---> Using cache\n"
            "Successfully built abc123\n"
        )

        def run_side_effect(cmd, **kwargs):
            # Only inspect still goes through subprocess.run (via _run_docker_command)
            if cmd[:3] == ["docker", "image", "inspect"]:
                assert cmd[-1] == "{{.Size}}"
                return MagicMock(returncode=0, stdout=f"{max_bytes - 1}\n")
            raise AssertionError(f"unexpected run command (build now uses Popen): {cmd}")

        with patch("subprocess.Popen", return_value=self._make_popen_mock(build_stdout, 0)) as mock_popen, \
             patch("subprocess.run", side_effect=run_side_effect) as mock_run:
            assert build_image("my_image", "/tmp/code") is True

        # Popen for build + run for inspect
        assert mock_popen.call_count == 1
        assert mock_run.call_count == 1
        # Verify we passed --progress=plain
        called_cmd = mock_popen.call_args[0][0]
        assert called_cmd[0:3] == ["docker", "build", "--progress=plain"]

    def test_build_image_rejects_oversized_and_deletes(self):
        from qbittensor.validator.solution.constants import MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES

        max_bytes = MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES
        calls: list[list[str]] = []
        build_stdout = "Step 1/1 : FROM python\nSuccessfully built def456\n"

        def run_side_effect(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:3] == ["docker", "image", "inspect"]:
                return MagicMock(returncode=0, stdout=f"{max_bytes + 1}\n")
            if cmd[:2] == ["docker", "rmi"]:
                return MagicMock(returncode=0)
            raise AssertionError(f"unexpected run command: {cmd}")

        with patch("subprocess.Popen", return_value=self._make_popen_mock(build_stdout, 0)) as mock_popen, \
             patch("subprocess.run", side_effect=run_side_effect):
            with pytest.raises(InvalidSolutionError):
                build_image("my_image", "/tmp/code")

        # First recorded call should be the build Popen cmd
        popen_cmd = mock_popen.call_args[0][0]
        assert popen_cmd[:2] == ["docker", "build"]
        assert "--progress=plain" in popen_cmd
        # Subsequent run calls: inspect then rmi
        assert calls[0][:3] == ["docker", "image", "inspect"]
        assert calls[1] == ["docker", "rmi", "-f", "my_image"]

    def test_build_image_inspect_failure_deletes_image(self):
        build_stdout = "Step 1/1 : FROM python\nSuccessfully built 999\n"

        def run_side_effect(cmd, **kwargs):
            if cmd[:2] == ["docker", "build"]:
                # Should not be reached; build uses Popen now
                raise AssertionError("build should use Popen")
            if cmd[:3] == ["docker", "image", "inspect"]:
                raise subprocess.CalledProcessError(1, cmd)
            if cmd[:2] == ["docker", "rmi"]:
                return MagicMock(returncode=0)
            raise AssertionError(f"unexpected run command: {cmd}")

        with patch("subprocess.Popen", return_value=self._make_popen_mock(build_stdout, 0)), \
             patch("subprocess.run", side_effect=run_side_effect):
            with pytest.raises(InvalidSolutionError):
                build_image("my_image", "/tmp/code")

    def test_build_image_failure(self):
        build_err = (
            "Step 1/2 : FROM python\n"
            "ERROR: failed to solve: failed to compute cache key: failed to calculate checksum..."
        )
        with patch("subprocess.Popen", return_value=self._make_popen_mock(build_err, 1)):
            with pytest.raises(InvalidSolutionError) as exc:
                build_image("my_image", "/tmp/code")
            msg = str(exc.value)
            assert "failed with exit code 1" in msg
            assert "docker build" in msg.lower()
            assert "Command:" in msg
            assert "Exit code: 1" in msg
            # The rich message now points users at the uploaded build log file
            assert "docker_build.log" in msg or "build output is captured" in msg.lower()

    def test_build_image_raises_rich_error_on_docker_not_found(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError("docker missing")):
            with pytest.raises(InvalidSolutionError) as exc:
                build_image("my_image", "/tmp/code")
            msg = str(exc.value)
            assert "Docker CLI not found" in msg
            assert "docker build" in msg.lower() or "build" in msg.lower()

    def test_validate_image_exists(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert validate_image("my_image") is True

    def test_validate_image_missing(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            with pytest.raises(InvalidSolutionError):
                validate_image("missing_image")
