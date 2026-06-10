# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# ... (standard header omitted for brevity)

"""Small unit tests for gpu_access that mock DockerOps (no real docker)."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from qbittensor.validator.utils.gpu_verification import gpu_access


def test_gpu_container_requires_gpus(monkeypatch):
    monkeypatch.setenv("VALIDATOR_DOCKER_GPUS", "")
    assert gpu_access.test_gpu_container() is False


def test_gpu_container_success_path(monkeypatch):
    """Happy path: build and run succeed, marker present."""
    monkeypatch.setenv("VALIDATOR_DOCKER_GPUS", "all")

    mock_ops = MagicMock()
    # build succeeds
    mock_ops.build_image.return_value = MagicMock(returncode=0)
    # run succeeds with marker in output
    run_res = MagicMock(stdout="some output\nSUCCESS: GPU is accessible from this container.\n", stderr="")
    mock_ops.run_container.return_value = run_res

    with patch("qbittensor.validator.utils.gpu_verification.gpu_access.DockerOps", return_value=mock_ops):
        result = gpu_access.test_gpu_container()
        assert result is True

    mock_ops.build_image.assert_called_once()
    mock_ops.run_container.assert_called_once()


def test_gpu_container_build_fails(monkeypatch):
    monkeypatch.setenv("VALIDATOR_DOCKER_GPUS", "all")

    mock_ops = MagicMock()
    err = subprocess.CalledProcessError(1, ["build"])
    err.stderr = b"build error"
    mock_ops.build_image.side_effect = err

    with patch("qbittensor.validator.utils.gpu_verification.gpu_access.DockerOps", return_value=mock_ops):
        result = gpu_access.test_gpu_container()
        assert result is False


def test_remove_image_uses_rmi():
    mock_ops = MagicMock()
    with patch("qbittensor.validator.utils.gpu_verification.gpu_access.DockerOps", return_value=mock_ops):
        gpu_access._remove_gpu_verification_image("test-img")
        mock_ops.rmi.assert_called_once_with("test-img", force=True)
