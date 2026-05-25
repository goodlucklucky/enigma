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

from typing import Optional
import requests

import bittensor as bt

from qbittensor.dto.challenge import (
    ChallengeSubmissionRead,
    ChallengeSubmissionRequest,
    ChallengeSubmissionResponse,
    ChallengeSubmissionVerifyUploadAddressResponse,
)
from qbittensor.utils.request.request_manager import RequestManager
from qbittensor.utils.services.exceptions import ChallengesApiError


class ChallengesClient:
    """
    Client for communication with the Challenges platform API.

    Recommended construction (hides RequestManager details):
        ChallengesClient(
            keypair=wallet.hotkey,
            base_url=challenges_api_url,
            tensorauth_url=...,
            netuid=netuid,
        )

    Alternative / advanced modes:
    - Pass a pre-built RequestManager (for tests, custom sessions, etc.).
    - Pass only base_url for unauthenticated/public reads (no auth/JWT).

    A fresh RequestManager is created per client instance when using the
    keypair-based constructor.
    """

    def __init__(
        self,
        request_manager: Optional[RequestManager] = None,
        base_url: Optional[str] = None,
        *,
        keypair: Optional[bt.Keypair] = None,
        tensorauth_url: Optional[str] = None,
        netuid: Optional[int] = None,
    ):
        if request_manager is not None:
            # Advanced / test path: caller supplies a fully configured RM
            self.request_manager = request_manager
            self._base_url = base_url
        elif keypair is not None and base_url is not None:
            # Preferred authenticated path: client owns its RequestManager
            self.request_manager = RequestManager(
                keypair,
                base_url=base_url,
                tensorauth_url=tensorauth_url,
                netuid=netuid,
            )
            self._base_url = base_url
        elif base_url is not None:
            # Public / unauthenticated path
            self.request_manager = None
            self._base_url = base_url
        else:
            raise ValueError(
                "ChallengesClient requires one of: request_manager, (keypair + base_url), or base_url"
            )

    @property
    def base_url(self) -> str:
        if self._base_url:
            return self._base_url
        # Fallback: try to get from request_manager if available (less ideal)
        # For now we expect callers to provide it when using public mode.
        raise RuntimeError("base_url was not provided to ChallengesClient")

    # ------------------------------------------------------------------
    # Internal request helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        operation: str = "request",
    ):
        """Centralized request helper."""
        if self.request_manager:
            # Authenticated path
            try:
                if method == "get":
                    resp = self.request_manager.get(endpoint=endpoint, params=params or {})
                elif method == "post":
                    resp = self.request_manager.post(endpoint=endpoint, json=json or {}, params=params or {})
                elif method == "patch":
                    resp = self.request_manager.patch(endpoint=endpoint, json=json or {}, params=params or {})
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if resp.status_code in (401, 403):
                    raise ChallengesApiError(
                        f"Authentication error during {operation}",
                        status_code=resp.status_code,
                        response_text=resp.text,
                    )
                return resp

            except ChallengesApiError:
                raise
            except Exception as e:
                raise ChallengesApiError(f"Unexpected error during {operation}: {e}") from e

        else:
            # Public / unauthenticated path
            url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
            try:
                resp = requests.request(
                    method.lower(),
                    url,
                    json=json,
                    params=params,
                    timeout=30.0,
                )
                resp.raise_for_status()
                return resp
            except requests.HTTPError as e:
                raise ChallengesApiError(
                    f"HTTP error during {operation}",
                    status_code=e.response.status_code if e.response else None,
                    response_text=e.response.text if e.response else str(e),
                ) from e
            except Exception as e:
                raise ChallengesApiError(f"Unexpected error during {operation}: {e}") from e

    # ------------------------------------------------------------------
    # Public endpoints (no auth required)
    # ------------------------------------------------------------------

    def list_challenges(self) -> dict:
        """Fetch the list of challenges (public)."""
        resp = self._request("get", "v1/challenges", operation="list_challenges")
        return resp.json()

    def get_challenge(self, challenge_id: str) -> dict:
        """Fetch details for a specific challenge (public)."""
        resp = self._request("get", f"v1/challenges/{challenge_id}", operation="get_challenge")
        return resp.json()

    def get_milestone_price_tao(self, challenge_id: str, milestone_id: str) -> float:
        """
        Fetch the current priceTao for a milestone from the platform.

        This performs:
            GET /v1/challenges/{challenge_id}
            then searches the milestones array for the matching id and returns priceTao.

        Both challenge_id and milestone_id are required.
        """
        challenge = self.get_challenge(challenge_id)
        for ms in challenge.get("milestones", []):
            if str(ms.get("id")) == str(milestone_id):
                price = ms.get("priceTao")
                if price is not None:
                    return float(price)
        raise RuntimeError(
            f"milestone_id {milestone_id} not found under challenge {challenge_id}"
        )

    def get_milestone_transfer_amount_rao(
        self, challenge_id: str, milestone_id: str
    ) -> str:
        """
        Convenience helper that returns the expected on-chain transfer amount
        as a string (in RAO) for a given milestone.

        This is the value that should be used when verifying transfer proofs.
        """
        price_tao = self.get_milestone_price_tao(challenge_id, milestone_id)
        return str(int(bt.Balance.from_tao(price_tao).rao))

    # ------------------------------------------------------------------
    # Authenticated endpoints (require RequestManager)
    # ------------------------------------------------------------------

    def submit_solution(
        self,
        milestone_id: str,
        payload: ChallengeSubmissionRequest,
    ) -> Optional[ChallengeSubmissionResponse]:
        if not self.request_manager:
            raise RuntimeError("submit_solution requires an authenticated ChallengesClient")

        endpoint = f"v1/challenges/milestones/{milestone_id}/submissions"
        try:
            resp = self._request(
                "post",
                endpoint,
                json=payload.model_dump(exclude_none=True),
                operation="submit_solution",
            )
        except ChallengesApiError as e:
            if e.status_code in (401, 403):
                bt.logging.error(f"❌ Auth error submitting solution: {e}")
                return None
            bt.logging.error(f"❌ Error during submit_solution: {e}")
            return None

        if resp.status_code == 201:
            return ChallengeSubmissionResponse(**resp.json())
        elif resp.status_code == 202:
            bt.logging.info("⭐ Submission already exists on platform (202).")
            return None
        else:
            self._log_error_response("submit_solution", resp)
            return None

    def report_submission_status(
        self,
        submission_id: str,
        status: str,
        reason: Optional[str] = None,
        log_data_key: Optional[str] = None,
        output_data_key: Optional[str] = None,
    ) -> bool:
        if not self.request_manager:
            raise RuntimeError("report_submission_status requires an authenticated ChallengesClient")

        payload: dict = {"status": status}
        if reason:
            payload["message"] = reason
        if log_data_key:
            payload["log_data_key"] = log_data_key
        if output_data_key:
            payload["output_data_key"] = output_data_key

        try:
            resp = self._request(
                "patch",
                f"v1/challenges/submissions/{submission_id}/verify",
                json=payload,
                operation="report_submission_status",
            )
        except ChallengesApiError as e:
            bt.logging.error(f"❌ Error reporting submission status: {e}")
            return False

        if resp.status_code == 200:
            bt.logging.info(f"✅ Reported submission {submission_id} as {status}")
            return True
        else:
            self._log_error_response("report_submission_status", resp)
            return False

    def get_next_cross_check_submission(self) -> Optional[ChallengeSubmissionRead]:
        if not self.request_manager:
            raise RuntimeError("get_next_cross_check_submission requires an authenticated ChallengesClient")

        try:
            resp = self._request(
                "get",
                "v1/submissions/next",
                operation="get_next_cross_check_submission",
            )
        except ChallengesApiError as e:
            if e.status_code in (401, 403):
                bt.logging.error(f"❌ Auth error from /submissions/next: {e}")
            else:
                bt.logging.error(f"❌ Error calling /submissions/next: {e}")
            return None

        if resp.status_code == 204:
            return None
        elif resp.status_code == 200:
            return ChallengeSubmissionRead(**resp.json())
        else:
            self._log_error_response("get_next_cross_check_submission", resp)
            return None

    def create_verification_upload_url(self) -> Optional[ChallengeSubmissionVerifyUploadAddressResponse]:
        if not self.request_manager:
            raise RuntimeError("create_verification_upload_url requires an authenticated ChallengesClient")

        endpoint = "v1/challenges/submissions/verify/upload"
        try:
            resp = self._request("post", endpoint, operation="create_verification_upload_url")
        except ChallengesApiError as e:
            bt.logging.error(f"❌ Error creating verification upload URL: {e}")
            return None

        if resp.status_code == 201:
            try:
                return ChallengeSubmissionVerifyUploadAddressResponse(**resp.json())
            except Exception as e:
                bt.logging.error(f"❌ Failed to parse verification upload response: {e}")
                return None
        else:
            self._log_error_response("create_verification_upload_url", resp)
            return None

    # ------------------------------------------------------------------
    # Miner submission flow (special authenticated endpoint)
    # The returned URL is a presigned storage URL — do NOT send auth headers when uploading.
    # ------------------------------------------------------------------
    def get_submission_upload_slot(self, filename: str, size: int) -> dict:
        """
        Miner CLI uses this to request a slot for uploading the actual solution package.
        Corresponds to POST /v1/submissions/upload (returns upload_url + fields).
        """
        if not self.request_manager:
            raise RuntimeError("get_submission_upload_slot requires an authenticated ChallengesClient")

        try:
            resp = self._request(
                "post",
                "v1/submissions/upload",
                json={"filename": filename, "size": size},
                operation="get_submission_upload_slot",
            )
        except ChallengesApiError as e:
            bt.logging.error(f"❌ Failed to get submission upload slot: {e}")
            raise

        return resp.json()

    def _log_error_response(self, operation: str, resp):
        try:
            body = resp.json()
            status = body.get("status_code", resp.status_code)
            message = body.get("message", resp.text)
        except Exception:
            status = resp.status_code
            message = resp.text
        bt.logging.error(f"❌ Platform error during {operation} (status={status}): {message}")
