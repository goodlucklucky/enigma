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

import base64
import json
from typing import Dict, Optional
import bittensor as bt
from time import time
from pydantic import BaseModel
import requests
from datetime import datetime, timedelta

from qbittensor.utils.request.utils import make_session
from qbittensor.utils.time import timestamp


JWT_ENDPOINT: str = "token"


class KeycloakJWT(BaseModel):
    access_token: str
    expires_in: int


class JWT(KeycloakJWT):
    expiration_date: datetime


class JWTManager:

    def __init__(
        self,
        keypair: bt.Keypair,
        netuid: int,
        tensorauth_url: Optional[str] = None,
    ) -> None:
        self._keypair: bt.Keypair = keypair
        self._netuid: int = netuid
        self._timeout: float = 7.0
        self._session: requests.Session = make_session(allowed_methods=["GET"])
        self._tensorauth_url: str = tensorauth_url or ""

    def get_jwt(self) -> JWT:
        """Fetch JWT from tensorauth service using signed header"""
        if not self._tensorauth_url:
            bt.logging.warning(
                "TENSORAUTH_URL not provided to JWTManager and not set in environment. "
                "get_jwt() calls will fail until it is configured."
            )
            raise ValueError(
                "TENSORAUTH_URL is not configured. "
                "Provide it via the tensorauth_url constructor argument or the TENSORAUTH_URL environment variable."
            )
        bt.logging.info(" ☎️  Contacting tensorauth service for a JWT!")
        bt.logging.info(f"Tensoruath URL: {self._tensorauth_url}/{JWT_ENDPOINT}")
        now: datetime = timestamp()
        response = self._session.get(
            f"{self._tensorauth_url}/{JWT_ENDPOINT}",
            headers=self._get_signed_header(),
            timeout=self._timeout,
        )
        response.raise_for_status()
        token_data = response.json()
        if not isinstance(token_data, dict):
            bt.logging.error(f"❌ ERROR: JWT response is not a dictionary: {token_data}")
            raise ValueError("JWT response is not a dictionary")
        try:
            token: KeycloakJWT = KeycloakJWT(**{str(k): v for k, v in token_data.items()})
        except Exception as e:
            bt.logging.error(f"❌ ERROR: Failed to parse JWT response: {e}")
            raise e
        bt.logging.info(f"✅ Received JWT from {self._tensorauth_url}/{JWT_ENDPOINT}")
        expiration_date: datetime = now + timedelta(seconds=token.expires_in)
        bt.logging.info(f"    - Token expires at {expiration_date.isoformat()} (in {token.expires_in} seconds)")
        return JWT(**token.model_dump(by_alias=True), expiration_date=expiration_date)

    def _get_signed_header(self) -> Dict[str, str]:
        """Create request header with signature, timestamp, hotkey, and netuid"""
        timestamp = str(int(time()))
        signature_bytes: bytes = self._keypair.sign(self._keypair.ss58_address.encode("utf-8"))
        signature_b64: str = base64.b64encode(signature_bytes).decode("utf-8")
        token_json: dict = {
            "hotkey": self._keypair.ss58_address,
            "timestamp": timestamp,
            "signature": signature_b64,
            "subnet": self._netuid
        }
        token: str = base64.b64encode(json.dumps(token_json).encode("utf-8")).decode('utf-8')
        return {
            "Authorization": f"Bearer {token}",
        }
