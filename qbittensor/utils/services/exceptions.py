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

"""Exceptions for the Challenges API client."""

from typing import Any, Optional
import json


def _parse_platform_error_body(body: Optional[str]) -> dict[str, Any]:
    """Attempt to parse the platform's ErrorResponseDto from a response body.

    Returns a dict with any of: 'status_code', 'message', 'error_code' when present.
    Safe on any input (returns empty dict on failure).
    """
    if not body or not isinstance(body, str):
        return {}
    text = body.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, Any] = {}
    if "status_code" in data:
        try:
            result["status_code"] = int(data["status_code"])
        except Exception:
            pass
    if "message" in data:
        result["message"] = data["message"]
    if "error_code" in data and data["error_code"]:
        result["error_code"] = str(data["error_code"])
    return result


class ChallengesApiError(Exception):
    """Base exception for errors when communicating with the Challenges API."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_text: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.error_code = error_code

    def __str__(self) -> str:
        parts: list[str] = []
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.error_code:
            parts.append(f"error_code={self.error_code}")
        suffix = f" ({', '.join(parts)})" if parts else ""
        return f"{super().__str__()}{suffix}"


class ChallengesAuthError(ChallengesApiError):
    """Raised when the request was unauthorized (401/403)."""
    pass


class ChallengesConflictError(ChallengesApiError):
    """Raised when the operation conflicts with existing state (e.g. 409 or 202 for duplicate submission)."""
    pass
