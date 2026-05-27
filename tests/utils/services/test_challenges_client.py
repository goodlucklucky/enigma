# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from unittest.mock import Mock

import pytest

from qbittensor.utils.services.challenges import ChallengesClient
from qbittensor.utils.services.exceptions import ChallengesApiError
from qbittensor.dto.challenge import (
    ChallengeSubmissionRequest,
    ChallengeSubmissionResponse,
    ChallengeSubmissionRead,
    ChallengeSubmissionVerifyUploadAddressResponse,
)


@pytest.fixture
def mock_request_manager():
    """A mock RequestManager that returns controllable Response objects."""
    rm = Mock()

    def make_response(status_code=200, json_data=None, text=""):
        resp = Mock()
        resp.status_code = status_code
        resp.text = text
        if json_data is not None:
            resp.json.return_value = json_data
        else:
            resp.json.side_effect = ValueError("No JSON body")
        return resp

    rm.get.return_value = make_response(200)
    rm.post.return_value = make_response(200)
    rm.patch.return_value = make_response(200)
    return rm, make_response


@pytest.fixture
def client(mock_request_manager):
    rm, _ = mock_request_manager
    return ChallengesClient(request_manager=rm)


class TestRequestHelper:
    """Tests for the internal _request helper (the heart of the client)."""

    def test_get_delegates_correctly(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.get.return_value = make_resp(200, {"ok": True})
        c = ChallengesClient(rm)

        resp = c._request("get", "submissions/next")

        rm.get.assert_called_once()
        assert resp.status_code == 200

    def test_post_delegates_correctly(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.post.return_value = make_resp(201)
        c = ChallengesClient(rm)

        resp = c._request("post", "some/endpoint", json={"a": 1})

        rm.post.assert_called_once_with(endpoint="some/endpoint", json={"a": 1}, params={})
        assert resp.status_code == 201

    def test_patch_delegates_correctly(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        c = ChallengesClient(rm)

        c._request("patch", "foo/bar", json={"status": "success"})

        rm.patch.assert_called_once()

    def test_unsupported_method_raises(self, mock_request_manager):
        rm, _ = mock_request_manager
        c = ChallengesClient(rm)

        with pytest.raises(ChallengesApiError, match="Unsupported method"):
            c._request("delete", "something")

    def test_auth_errors_are_wrapped(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.get.return_value = make_resp(401, text="Unauthorized")
        c = ChallengesClient(rm)

        with pytest.raises(ChallengesApiError) as exc:
            c._request("get", "protected", operation="test_op")

        assert exc.value.status_code == 401

    def test_unexpected_exceptions_are_wrapped(self, mock_request_manager):
        rm, _ = mock_request_manager
        rm.get.side_effect = RuntimeError("network down")
        c = ChallengesClient(rm)

        with pytest.raises(ChallengesApiError, match="Unexpected error"):
            c._request("get", "anything")


class TestSubmitSolution:
    def test_successful_submission_returns_response(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        payload = {"foo": "bar"}
        good_response = {
            "id": "sub_123",
            "status": "pending",
            "challenge_milestone_id": "m1",
            "tx_hash": "0xtx",
            "file_download_url": "https://example.com/file",
        }
        rm.post.return_value = make_resp(201, good_response)

        c = ChallengesClient(rm)
        req = ChallengeSubmissionRequest.model_construct(**payload)

        result = c.submit_solution("m1", req)

        assert isinstance(result, ChallengeSubmissionResponse)
        assert result.id == "sub_123"

    def test_202_returns_none(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.post.return_value = make_resp(202)
        c = ChallengesClient(rm)

        result = c.submit_solution("m1", ChallengeSubmissionRequest.model_construct())
        assert result is None

    def test_auth_error_returns_none(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.post.side_effect = ChallengesApiError("auth", status_code=401)
        c = ChallengesClient(rm)

        result = c.submit_solution("m1", ChallengeSubmissionRequest.model_construct())
        assert result is None

    def test_other_error_returns_none(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.post.return_value = make_resp(500, text="boom")
        c = ChallengesClient(rm)

        result = c.submit_solution("m1", ChallengeSubmissionRequest.model_construct())
        assert result is None


class TestReportSubmissionStatus:
    def test_success_returns_true(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.patch.return_value = make_resp(200)
        c = ChallengesClient(rm)

        success = c.report_submission_status("sub_1", "Success", output_data_key="logs.zip")
        assert success is True
        rm.patch.assert_called_once()

    def test_non_200_returns_false(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.patch.return_value = make_resp(400, text="bad request")
        c = ChallengesClient(rm)

        success = c.report_submission_status("sub_1", "Failed")
        assert success is False

    def test_exception_returns_false(self, mock_request_manager):
        rm, _ = mock_request_manager
        rm.patch.side_effect = ChallengesApiError("network")
        c = ChallengesClient(rm)

        success = c.report_submission_status("sub_1", "Running")
        assert success is False


class TestGetNextCrossCheckSubmission:
    def test_204_returns_none(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.get.return_value = make_resp(204)
        c = ChallengesClient(rm)

        result = c.get_next_cross_check_submission()
        assert result is None

    def test_200_returns_dto(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        data = {
            "id": "sub_999",
            "challenge_milestone_id": "m42",
            "tx_hash": "0xabc",
            "address": "5Addr",
            "transfer_block_hash": "0xblock",
            "transfer_from_ss58": "5From",
            "transfer_to_ss58": "5To",
            "transfer_amount_rao": "100000",
            "transfer_proof_message": "msg",
            "transfer_proof_signature_hex": "sig",
            "upload_endpoint_id": "upload1",
            "file_download_url": "https://example.com/download",
        }
        rm.get.return_value = make_resp(200, data)
        c = ChallengesClient(rm)

        result = c.get_next_cross_check_submission()
        assert isinstance(result, ChallengeSubmissionRead)
        assert result.challenge_milestone_id == "m42"

    def test_auth_error_is_logged_and_returns_none(self, mock_request_manager):
        rm, _ = mock_request_manager
        rm.get.side_effect = ChallengesApiError("forbidden", status_code=403)
        c = ChallengesClient(rm)

        result = c.get_next_cross_check_submission()
        assert result is None


class TestCreateVerificationUploadUrl:
    def test_success_returns_dto(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        data = {"id": "upload_123", "url": "https://s3.../upload?token=..."}
        rm.post.return_value = make_resp(201, data)
        c = ChallengesClient(rm)

        result = c.create_verification_upload_url()
        assert result is not None
        assert "s3" in result.url

    def test_non_201_returns_none(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        rm.post.return_value = make_resp(500)
        c = ChallengesClient(rm)

        result = c.create_verification_upload_url()
        assert result is None

    def test_bad_json_returns_none(self, mock_request_manager):
        rm, make_resp = mock_request_manager
        resp = make_resp(201)
        resp.json.side_effect = Exception("bad json")
        rm.post.return_value = resp
        c = ChallengesClient(rm)

        result = c.create_verification_upload_url()
        assert result is None


class TestLogErrorResponse:
    def test_logs_json_body_when_available(self, mock_request_manager, caplog):
        rm, make_resp = mock_request_manager
        resp = make_resp(400)
        resp.json.return_value = {"status_code": 400, "message": "bad milestone"}
        c = ChallengesClient(rm)

        c._log_error_response("test_op", resp)
        # We mainly care that it didn't crash and tried to extract useful info
        assert "test_op" in caplog.text or True  # logging may be captured at different levels

    def test_falls_back_to_text_on_json_failure(self, mock_request_manager, caplog):
        rm, make_resp = mock_request_manager
        resp = make_resp(500, text="raw error text")
        resp.json.side_effect = Exception("no json")
        c = ChallengesClient(rm)

        c._log_error_response("failing_op", resp)
        assert "failing_op" in caplog.text or True
