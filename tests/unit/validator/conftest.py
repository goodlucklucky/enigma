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

from unittest.mock import Mock

import pytest

from qbittensor.utils.services.challenges import ChallengesClient


@pytest.fixture
def platform_client():
    """Shared mock for ChallengesClient to be used across validator tests."""
    client = Mock(spec=ChallengesClient)
    # Provide sensible defaults for all public methods on ChallengesClient
    client.submit_solution.return_value = None
    client.get_next_cross_check_submission.return_value = None
    client.report_submission_status.return_value = True
    client.get_milestone_price_tao.return_value = 0.1
    client.get_milestone_transfer_amount_rao.return_value = "100000"
    client.create_verification_upload_url.return_value = Mock(
        id="upload-123", url="https://example.com/upload"
    )
    client.list_challenges.return_value = {"challenges": []}
    client.get_challenge.return_value = {}
    client.get_submission_upload_slot.return_value = {
        "upload_url": "https://example.com/upload",
        "fields": {},
    }
    return client
