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

import bittensor as bt
from typing import Dict, List, Tuple
import requests
from datetime import datetime, timedelta

from qbittensor.utils.request.jwt_manager import JWT, JWTManager
from qbittensor.utils.request.utils import make_session
from qbittensor.utils.time import timestamp


JWT_EXPIRATION_BUFFER: timedelta = timedelta(minutes=1)


class RequestManager:
    """Generic signed HTTP client for platform APIs.

    Base URLs (including any version prefix like /v1) are the responsibility
    of the caller (ChallengesClient, TelemetryService, etc.). This class only
    handles JWT signing and making HTTP requests.
    """

    def __init__(
        self,
        keypair: bt.Keypair,
        base_url: str | None = None,
        network: str = "",
        netuid: int | None = None,
        tensorauth_url: str | None = None,
    ) -> None:
        self._keypair: bt.Keypair = keypair
        self._base_url = base_url.rstrip("/") if base_url else None
        self._network = network
        self._netuid: int | None = netuid
        self._timeout: float = 7.0

        self._jwt_manager: JWTManager = JWTManager(
            keypair, netuid=netuid or 0, tensorauth_url=tensorauth_url
        )
        self._jwt: JWT | None = None

        self._session: requests.Session = make_session(
            allowed_methods=["GET", "POST", "PATCH"],
        )

    def get(
        self,
        endpoint: str,
        params: Dict = {},
        additional_headers: List[Tuple[str, str]] = [],
        ignore_codes: List[int] = [],
    ) -> requests.Response:
        """Make a GET request to the job server with signed header"""
        headers = self._get_header()
        for key, value in additional_headers:
            headers[key] = value
        full_url: str = self._build_url(endpoint)
        try:
            response: requests.Response = self._session.get(
                full_url, headers=headers, params=params, timeout=self._timeout
            )
            self.check_error_code(response, full_url, "GET", ignore_codes=ignore_codes)
            return response
        except requests.exceptions.ConnectionError as e:
            bt.logging.error(f"({type(e)}) GET request to {full_url} failed: {e}")
            raise

    def post(
        self,
        endpoint: str,
        json: Dict = {},
        params: Dict = {},
        additional_headers: List[Tuple[str, str]] = [],
        ignore_codes: List[int] = [],
    ) -> requests.Response:
        """Make a POST request to the job server with signed header"""
        headers = self._get_header()
        for key, value in additional_headers:
            headers[key] = value
        full_url: str = self._build_url(endpoint)
        response: requests.Response = self._session.post(
            full_url, json=json, headers=headers, params=params, timeout=self._timeout
        )
        self.check_error_code(response, full_url, "POST", ignore_codes=ignore_codes)
        return response



    def patch(self, endpoint: str, json: Dict, params: Dict = {}, ignore_codes: List[int] = []) -> requests.Response:
        """Make a PATCH request to the job server with signed header"""
        headers = self._get_header()
        full_url: str = self._build_url(endpoint)
        response: requests.Response = self._session.patch(
            full_url, json=json, headers=headers, params=params, timeout=self._timeout
        )
        self.check_error_code(response, full_url, "PATCH", ignore_codes=ignore_codes)
        return response

    def check_error_code(self, response: requests.Response, url: str, method: str, ignore_codes: List[int] = []) -> bool:
        """Return true if status code is non-200"""
        status_code = response.status_code
        is_error_code = status_code < 200 or status_code > 299
        if is_error_code:
            if status_code not in ignore_codes:
                bt.logging.error(
                    "❗ Received error from server for '{method} {url}' code: {status_code} - {text}'.".format(
                        method=method, url=url, status_code=status_code, text=response.text
                    )
                )
        else:
            if status_code not in ignore_codes:
                bt.logging.info(
                    "✅ {method} request to '{url}' successful with status code {status_code}".format(
                        method=method, url=url, status_code=status_code
                    )
                )
        return is_error_code

    def _build_url(self, endpoint: str) -> str:
        """Build full endpoint url.

        If a base_url was provided at construction, it will be prepended.
        The caller is responsible for including any version prefix (e.g. 'v1/').
        """
        if self._base_url:
            return f"{self._base_url}/{endpoint.lstrip('/')}"
        return endpoint.lstrip('/')

    def _get_header(self) -> Dict[str, str]:
        """Create request header with signature, timestamp, hotkey"""
        if self._token_is_expired():
            bt.logging.info("🔑 JWT expired, fetching a new one")
            self._jwt: JWT = self._jwt_manager.get_jwt()
        return {
            "Authorization": f"Bearer {self._jwt.access_token}",
        }

    def _token_is_expired(self) -> bool:
        """Check if the current JWT is expired"""
        if self._jwt is None:
            return True
        now: datetime = timestamp()
        return now >= (self._jwt.expiration_date - JWT_EXPIRATION_BUFFER)
