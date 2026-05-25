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

from unittest.mock import ANY, Mock, patch
import os

import pytest

from qbittensor.dto.challenge import ChallengeSubmissionVerifyUploadAddressResponse
from qbittensor.utils.solution_status import SolutionStatus
from qbittensor.validator.solution.validate_solution_output import (
    establish_upload_locations_for_solution_data,
    perform_solution_output_validation,
    upload_zip_to_platform,
    validate_solution,
    verify_upload_locations,
)
from qbittensor.utils.services.challenges import ChallengesClient


class TestEstablishUploadLocations:
    def test_success(self, platform_client):
        platform_client.create_verification_upload_url.return_value = ChallengeSubmissionVerifyUploadAddressResponse(
            id="log-id", url="https://upload.example/log"
        )
        result = establish_upload_locations_for_solution_data("/tmp/sol", "logs", platform_client)
        assert isinstance(result, ChallengeSubmissionVerifyUploadAddressResponse)
        assert result.id == "log-id"

    def test_failure(self, platform_client):
        platform_client.create_verification_upload_url.return_value = None
        assert establish_upload_locations_for_solution_data("/tmp", "logs", platform_client) is None


class TestVerifyUploadLocations:
    def test_success(self, platform_client):
        db = Mock()
        db.db_query.get_submission_id_by_solution_location.return_value = "sub-1"
        logs = ChallengeSubmissionVerifyUploadAddressResponse(id="log-id", url="https://u/log")
        output = ChallengeSubmissionVerifyUploadAddressResponse(id="out-id", url="https://u/out")
        platform_client.report_submission_status.return_value = True
        assert verify_upload_locations("/tmp/sol", logs, output, db) is True

    def test_failure(self):
        db = Mock()
        db.db_query.get_submission_id_by_solution_location.return_value = None  # simulate missing submission
        logs = ChallengeSubmissionVerifyUploadAddressResponse(id="log-id", url="https://u/log")
        output = ChallengeSubmissionVerifyUploadAddressResponse(id="out-id", url="https://u/out")
        assert verify_upload_locations("/tmp/sol", logs, output, db) is False


class TestPerformSolutionOutputValidation:
    @patch("qbittensor.validator.solution.validate_solution_output.upload_zip_to_platform")
    @patch("qbittensor.validator.solution.validate_solution_output.validate_output", return_value=True)
    def test_success_path(self, _validate, _upload, platform_client):
        db = Mock()
        db.db_query.get_challenge_milestone_id_by_file_path.return_value = (
            "012b3e8e-b1e9-401e-ab70-f1598b34746f"
        )
        db.db_query.get_submission_id_by_solution_location.return_value = "sub-1"
        request_manager = Mock()
        request_manager.patch.return_value = Mock(status_code=200)
        platform_data = ChallengeSubmissionVerifyUploadAddressResponse(
            id="out-id", url="https://upload.example/out"
        )

        status = perform_solution_output_validation(
            "/tmp/workspace",
            "/tmp/workspace/output",
            platform_data,  # logs_data
            platform_data,  # solution_output_data (same mock is fine for test)
            platform_client,
            db,
        )
        assert status == SolutionStatus.SUCCESS.value

    def test_missing_milestone_returns_failed(self, platform_client):
        db = Mock()
        db.db_query.get_challenge_milestone_id_by_file_path.return_value = None
        db.db_query.get_submission_id_by_solution_location.return_value = "sub-1"
        platform_data = ChallengeSubmissionVerifyUploadAddressResponse(
            id="out-id", url="https://upload.example/out"
        )
        status = perform_solution_output_validation(
            "/tmp/workspace",
            "/tmp/workspace/output",
            platform_data,  # logs_data
            platform_data,  # solution_output_data
            platform_client,
            db,
        )
        assert status == SolutionStatus.FAILED.value


class TestUploadZipToPlatform:
    def test_uploads_single_file(self, tmp_path):
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (out_dir / "stdout.log").write_text("log line")
        platform_data = ChallengeSubmissionVerifyUploadAddressResponse(
            id="id", url="https://upload.example/put"
        )
        with patch("requests.put") as mock_put:
            mock_put.return_value = Mock(status_code=200)
            upload_zip_to_platform(str(out_dir), platform_data, "stdout.log")
        mock_put.assert_called_once()

    def test_skips_missing_file(self, tmp_path):
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        platform_data = ChallengeSubmissionVerifyUploadAddressResponse(
            id="id", url="https://upload.example/put"
        )
        with patch("requests.put") as mock_put:
            upload_zip_to_platform(str(out_dir), platform_data, "missing.log")
        mock_put.assert_not_called()


class TestValidateSolutionCorePaths:
    """High-value tests for the main validate_solution entry point and key decision branches."""

    def test_returns_failed_upload_when_cannot_get_upload_locations(self, tmp_path, platform_client):
        ws = str(tmp_path / "ws")
        os.makedirs(os.path.join(ws, "output"))

        platform_client.create_verification_upload_url.return_value = None
        db = Mock()

        status = validate_solution(ws, platform_client, db)
        assert status.upper() == "FAILED_UPLOAD"

    def test_perform_validation_success_reports_success_with_keys(self, tmp_path, platform_client):
        ws = str(tmp_path / "ws")
        output_path = os.path.join(ws, "output")
        os.makedirs(output_path)

        platform_client.create_verification_upload_url.return_value = Mock(id="log_id", url="log_url")
        platform_client.report_submission_status.return_value = True

        db = Mock()
        db.db_query.get_challenge_milestone_id_by_file_path.return_value = "m1"
        db.db_query.get_submission_id_by_solution_location.return_value = "sub1"

        with patch("qbittensor.validator.solution.validate_solution_output.verify_upload_locations", return_value=True), \
             patch("qbittensor.validator.solution.validate_solution_output.upload_zip_to_platform"), \
             patch("qbittensor.validator.solution.validate_solution_output.validate_output", return_value=True):

            status = validate_solution(ws, platform_client, db)
            assert status.upper() == "SUCCESS"
            platform_client.report_submission_status.assert_called_with(
                "sub1", "Success", log_data_key="log_id", output_data_key=ANY
            )

    def test_perform_validation_failure_reports_failure_with_keys(self, tmp_path, platform_client):
        ws = str(tmp_path / "ws")
        output_path = os.path.join(ws, "output")
        os.makedirs(output_path)

        platform_client.create_verification_upload_url.return_value = Mock(id="log_id", url="log_url")
        platform_client.report_submission_status.return_value = True

        db = Mock()
        db.db_query.get_challenge_milestone_id_by_file_path.return_value = "m1"
        db.db_query.get_submission_id_by_solution_location.return_value = "sub1"

        with patch("qbittensor.validator.solution.validate_solution_output.verify_upload_locations", return_value=True), \
             patch("qbittensor.validator.solution.validate_solution_output.upload_zip_to_platform"), \
             patch("qbittensor.validator.solution.validate_solution_output.validate_output", return_value=False):

            status = validate_solution(ws, platform_client, db)
            assert status.upper() == "FAILED"
            platform_client.report_submission_status.assert_called_with(
                "sub1", "Failure", log_data_key="log_id", output_data_key=ANY
            )
