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

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from qbittensor.validator.solution.constants import (
    CHALLENGE_SOLTION_PREFIX,
    CONTAINER_OUTPUT_DIRNAME,
    CONTAINER_SOLUTION_DIRNAME,
)
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.exceptions.validation_errors import ValidationErrors


class TestConstants:
    def test_constant_values(self):
        assert CHALLENGE_SOLTION_PREFIX == "sn63solution"
        assert CONTAINER_OUTPUT_DIRNAME == "output"
        assert CONTAINER_SOLUTION_DIRNAME == "solution_artifacts"


class TestValidationErrors:
    def test_enum_members_exist(self):
        assert ValidationErrors.INVALID_TARBALL.value
        assert ValidationErrors.DOCKER_RUN_FAILED.value


class TestInvalidSolutionError:
    def test_stores_optional_fields(self):
        err = InvalidSolutionError(
            "failed",
            container_id="cid",
            image_name="img",
            challenge_id="ch",
            transaction_id="tx",
        )
        assert str(err) == "failed"
        assert err.error_msg == "failed"
        assert err.container_id == "cid"
        assert err.image_name == "img"
        assert err.challenge_id == "ch"
        assert err.transaction_id == "tx"
