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

"""Unit tests for code layout validation in validate_code."""

from qbittensor.validator.solution.validate_code import validate_code


def _solution_dir(tmp_path, dockerfile: str, *, dockerfile_name: str = "Dockerfile") -> str:
    sol = tmp_path / "sol"
    code_dir = sol / "code"
    code_dir.mkdir(parents=True)
    (code_dir / dockerfile_name).write_text(dockerfile)
    return str(sol)


class TestDockerfilePresence:
    def test_validate_code_finds_dockerfile(self, tmp_path):
        sol = _solution_dir(tmp_path, "FROM python:3.12\n")
        assert validate_code(sol) is True

    def test_validate_code_finds_lowercase_dockerfile(self, tmp_path):
        sol = _solution_dir(tmp_path, "FROM python:3.12\n", dockerfile_name="dockerfile")
        assert validate_code(sol) is True

    def test_validate_code_missing_dockerfile(self, tmp_path):
        sol = tmp_path / "sol"
        (sol / "code").mkdir(parents=True)
        assert validate_code(str(sol)) is False

    def test_validate_code_allows_volume_instruction(self, tmp_path):
        sol = _solution_dir(tmp_path, "FROM python:3.12\nVOLUME /data\n")
        assert validate_code(sol) is True
