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

"""
Centralized environment loading + API URL configuration.

This module is the single source of truth for:
- When and how .env is loaded (via python-dotenv)
- The three core external API base URLs used by the system
- The standard production defaults for those URLs

All entrypoints (neurons and CLIs) should use the helpers here instead of
calling load_dotenv() and applying defaults themselves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


# -----------------------------------------------------------------------------
# Production defaults for the three external services we talk to.
# -----------------------------------------------------------------------------
DEFAULT_CHALLENGES_API_URL = "https://challenges.qbittensorlabs.com"
DEFAULT_TENSORAUTH_URL = "https://tensorauth.qbittensorlabs.com"
DEFAULT_TELEMETRY_API_URL = "https://telemetry.qbittensorlabs.com"


@dataclass(frozen=True)
class ApiConfig:
    """Resolved API base URLs (no version prefix — callers add /v1/ where appropriate)."""

    challenges_api_url: str
    tensorauth_url: str
    telemetry_api_url: str


def get_api_config() -> ApiConfig:
    """
    Load the environment (if not already loaded) and return the three main
    API base URLs with the standard production defaults applied.

    This is the recommended way for entrypoints (miner, validator, CLIs) to
    obtain their configuration for the external services.
    """
    load_dotenv()

    return ApiConfig(
        challenges_api_url=os.getenv("CHALLENGES_API_URL") or DEFAULT_CHALLENGES_API_URL,
        tensorauth_url=os.getenv("TENSORAUTH_URL") or DEFAULT_TENSORAUTH_URL,
        telemetry_api_url=os.getenv("TELEMETRY_API_URL") or DEFAULT_TELEMETRY_API_URL,
    )
