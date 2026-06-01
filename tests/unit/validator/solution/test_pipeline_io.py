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
        assert tag == folder
        assert folder == "label_sol-id_12345"
        assert (tmp_path / folder).is_dir()

    def test_cleanup_removes_folder(self, tmp_path):
        folder = tmp_path / "to_remove"
        folder.mkdir()
        manage_files.cleanup(str(folder))
        assert not folder.exists()


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


class TestDockerBuildAndValidate:
    def test_build_image_success(self):
        from qbittensor.validator.solution.constants import MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES

        max_bytes = MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES

        def run_side_effect(cmd, **kwargs):
            if cmd[:2] == ["docker", "build"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["docker", "image", "inspect"]:
                assert cmd[-1] == "{{.Size}}"
                return MagicMock(returncode=0, stdout=f"{max_bytes - 1}\n")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("subprocess.run", side_effect=run_side_effect) as mock_run:
            assert build_image("my_image", "/tmp/code") is True
        assert mock_run.call_count == 2

    def test_build_image_rejects_oversized_and_deletes(self):
        from qbittensor.validator.solution.constants import MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES

        max_bytes = MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES
        calls: list[list[str]] = []

        def run_side_effect(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["docker", "build"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["docker", "image", "inspect"]:
                return MagicMock(returncode=0, stdout=f"{max_bytes + 1}\n")
            if cmd[:2] == ["docker", "rmi"]:
                return MagicMock(returncode=0)
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("subprocess.run", side_effect=run_side_effect):
            with pytest.raises(InvalidSolutionError):
                build_image("my_image", "/tmp/code")

        assert calls[0][:2] == ["docker", "build"]
        assert calls[1][:3] == ["docker", "image", "inspect"]
        assert calls[2] == ["docker", "rmi", "-f", "my_image"]

    def test_build_image_inspect_failure_deletes_image(self):
        def run_side_effect(cmd, **kwargs):
            if cmd[:2] == ["docker", "build"]:
                return MagicMock(returncode=0)
            if cmd[:3] == ["docker", "image", "inspect"]:
                raise subprocess.CalledProcessError(1, cmd)
            if cmd[:2] == ["docker", "rmi"]:
                return MagicMock(returncode=0)
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("subprocess.run", side_effect=run_side_effect):
            with pytest.raises(InvalidSolutionError):
                build_image("my_image", "/tmp/code")

    def test_build_image_failure(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "docker", stderr="build error details")):
            with pytest.raises(InvalidSolutionError) as exc:
                build_image("my_image", "/tmp/code")
            msg = str(exc.value)
            assert "failed with exit code 1" in msg
            assert "build error details" in msg
            assert "Command:" in msg
            assert "Exit code: 1" in msg

    def test_build_image_raises_rich_error_on_docker_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("docker missing")):
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
