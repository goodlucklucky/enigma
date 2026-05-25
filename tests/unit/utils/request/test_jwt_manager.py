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
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from qbittensor.utils.request.jwt_manager import JWTManager, KeycloakJWT


class TestJWTManager:
    def test_get_signed_header_contains_hotkey_and_subnet(self):
        keypair = Mock()
        keypair.ss58_address = "5HotkeyAddress"
        keypair.sign.return_value = b"signed-bytes"

        manager = JWTManager(keypair, netuid=63)  # tensorauth_url not needed for _get_signed_header
        headers = manager._get_signed_header()

        assert "Authorization" in headers
        token_b64 = headers["Authorization"].removeprefix("Bearer ")
        token_json = json.loads(base64.b64decode(token_b64))
        assert token_json["hotkey"] == "5HotkeyAddress"
        assert token_json["subnet"] == 63
        assert "timestamp" in token_json
        assert "signature" in token_json

    def test_get_jwt_parses_response_and_sets_expiration(self):
        keypair = Mock()
        keypair.ss58_address = "5HotkeyAddress"
        keypair.sign.return_value = b"signed-bytes"

        mock_response = Mock()
        mock_response.json.return_value = {"access_token": "tok123", "expires_in": 3600}
        mock_response.raise_for_status = Mock()

        manager = JWTManager(keypair, netuid=2, tensorauth_url="http://dummy-auth")
        manager._session = Mock()
        manager._session.get.return_value = mock_response

        before = datetime.now(timezone.utc)
        jwt = manager.get_jwt()
        after = datetime.now(timezone.utc)

        assert jwt.access_token == "tok123"
        assert jwt.expires_in == 3600
        assert before <= jwt.expiration_date <= after.replace(second=after.second) or jwt.expiration_date >= before

    def test_get_jwt_raises_on_non_dict_response(self):
        keypair = Mock()
        keypair.ss58_address = "5HotkeyAddress"
        keypair.sign.return_value = b"signed-bytes"

        mock_response = Mock()
        mock_response.json.return_value = ["not", "a", "dict"]
        mock_response.raise_for_status = Mock()

        manager = JWTManager(keypair, netuid=2, tensorauth_url="http://dummy-auth")
        manager._session = Mock()
        manager._session.get.return_value = mock_response

        with pytest.raises(ValueError, match="not a dictionary"):
            manager.get_jwt()

    def test_oqjwt_model_validation(self):
        token = KeycloakJWT(access_token="abc", expires_in=60)
        assert token.access_token == "abc"
        assert token.expires_in == 60
