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

import qbittensor.validator.solution.exceptions.validation_errors as validation_errors

class InvalidSolutionError(Exception):
    """Custom exception for invalid solution errors."""
    container_id: str | None = None
    image_name: str | None = None
    challenge_id: str | None = None
    transaction_id: str | None = None
    error_msg: str | None = None

    def __init__(self, message: str, container_id: str | None = None, image_name: str | None = None, challenge_id: str | None = None, transaction_id: str | None = None):
        super().__init__(message)
        self.error_msg = message
        self.container_id = container_id
        self.image_name = image_name
        self.challenge_id = challenge_id
        self.transaction_id = transaction_id

