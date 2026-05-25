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

"""Unit tests for Dockerfile security policy in validate_docker_image."""

import pytest

from qbittensor.validator.solution.validate_docker_image import (
    REJECTED_DOCKERFILE_RULES,
    _validate_dockerfile_content,
    reject_dockerfile,
)

RULE_EXPOSE = REJECTED_DOCKERFILE_RULES[0]
RULE_VOLUME = REJECTED_DOCKERFILE_RULES[1]
RULE_COPY_FROM = REJECTED_DOCKERFILE_RULES[2]
RULE_ABSOLUTE_PATH = REJECTED_DOCKERFILE_RULES[3]
RULE_PARENT_PATH = REJECTED_DOCKERFILE_RULES[4]


def _solution_dir(tmp_path, dockerfile: str, *, dockerfile_name: str = "Dockerfile") -> str:
    sol = tmp_path / "sol"
    code_dir = sol / "code"
    code_dir.mkdir(parents=True)
    (code_dir / dockerfile_name).write_text(dockerfile)
    return str(sol)


class TestRejectedDockerfileRulesDocumented:
    def test_rule_count_and_keywords(self):
        assert len(REJECTED_DOCKERFILE_RULES) == 5
        assert "EXPOSE" in REJECTED_DOCKERFILE_RULES[0]
        assert "VOLUME" in REJECTED_DOCKERFILE_RULES[1]
        assert "COPY --from" in REJECTED_DOCKERFILE_RULES[2]
        assert "absolute" in REJECTED_DOCKERFILE_RULES[3].lower()
        assert "../" in REJECTED_DOCKERFILE_RULES[4] or ".." in REJECTED_DOCKERFILE_RULES[4]


class TestRuleVolumeNotAllowed:
    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\nVOLUME /data\n",
            "FROM python:3.12\nvolume /data\n",
            "FROM python:3.12\nVOLUME [\"/data\", \"/logs\"]\n",
            "FROM python:3.12\nRUN mkdir /app\nVOLUME /data\n",
            "FROM python:3.12\nVOLUME \\\n  /data\n",
        ],
    )
    def test_rejects_volume_instruction(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) == RULE_VOLUME

    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\n",
            "FROM python:3.12\n# VOLUME /data\n",
            "FROM python:3.12\nRUN echo 'VOLUME /data' > /dev/null\n",
        ],
    )
    def test_allows_without_volume(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) is None

    def test_reject_dockerfile_via_folder(self, tmp_path):
        sol = _solution_dir(tmp_path, "FROM python:3.12\nVOLUME /data\n")
        assert reject_dockerfile(sol) is False


class TestRuleExposeNotAllowed:
    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\nEXPOSE 8080\n",
            "FROM python:3.12\nexpose 443\n",
            "FROM python:3.12\nEXPOSE 80/tcp\n",
            "FROM python:3.12\nEXPOSE 8080 8443\n",
        ],
    )
    def test_rejects_expose_instruction(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) == RULE_EXPOSE

    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\n",
            "FROM python:3.12\n# EXPOSE 8080\n",
            "FROM python:3.12\nRUN echo 'EXPOSE 8080' > /dev/null\n",
        ],
    )
    def test_allows_without_expose(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) is None

    def test_reject_dockerfile_via_folder(self, tmp_path):
        sol = _solution_dir(tmp_path, "FROM python:3.12\nEXPOSE 9000\n")
        assert reject_dockerfile(sol) is False


class TestRuleCopyFromBuildContextOnly:
    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\nCOPY --from=nginx:latest /etc/nginx/nginx.conf /app/\n",
            "FROM python:3.12\nCOPY --from=my.registry.io/app:1.0 /bin/app /app/\n",
            "FROM python:3.12\nCOPY --from=nginx /etc/nginx/nginx.conf /app/\n",
            "FROM golang:1.22 AS builder\nFROM alpine:3.19\nCOPY --from=missing /out/app /app/\n",
            "FROM python:3.12\nCOPY --from=1 /out/app /app/\n",
        ],
    )
    def test_rejects_external_or_unknown_copy_from(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) == RULE_COPY_FROM

    @pytest.mark.parametrize(
        "dockerfile",
        [
            (
                "FROM golang:1.22 AS builder\n"
                "RUN go build -o /out/app .\n"
                "FROM alpine:3.19\n"
                "COPY --from=builder /out/app /usr/local/bin/app\n"
            ),
            (
                "FROM golang:1.22 AS builder\n"
                "FROM alpine:3.19 AS runtime\n"
                "COPY --from=0 /out/app /usr/local/bin/app\n"
            ),
            "FROM python:3.12\nCOPY app.py /app/\n",
        ],
    )
    def test_allows_same_dockerfile_stage_copy_from(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) is None

    def test_reject_dockerfile_via_folder(self, tmp_path):
        sol = _solution_dir(
            tmp_path,
            "FROM python:3.12\nCOPY --from=nginx:latest /etc/nginx/nginx.conf /app/\n",
        )
        assert reject_dockerfile(sol) is False


class TestRuleNoAbsoluteHostPaths:
    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\nCOPY /etc/passwd /app/passwd\n",
            "FROM python:3.12\nADD /tmp/artifact.tgz /app/\n",
            'FROM python:3.12\nCOPY ["/etc/passwd", "/app/passwd"]\n',
            "FROM python:3.12\nCOPY /var/log/syslog /app/syslog\n",
        ],
    )
    def test_rejects_absolute_copy_add_sources(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) == RULE_ABSOLUTE_PATH

    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\nCOPY app.py /app/\n",
            "FROM python:3.12\nCOPY ./src /app/src\n",
            "FROM python:3.12\nADD local.tgz /app/\n",
            (
                "FROM golang:1.22 AS builder\n"
                "FROM alpine:3.19\n"
                "COPY --from=builder /out/app /usr/local/bin/app\n"
            ),
        ],
    )
    def test_allows_relative_or_stage_paths(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) is None

    def test_reject_dockerfile_via_folder(self, tmp_path):
        sol = _solution_dir(tmp_path, "FROM python:3.12\nCOPY /etc/passwd /app/passwd\n")
        assert reject_dockerfile(sol) is False


class TestRuleNoParentPathTraversal:
    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\nADD ../outside.txt /app/\n",
            "FROM python:3.12\nCOPY ../secrets/key /app/key\n",
            "FROM python:3.12\nCOPY foo/../../../etc/passwd /app/passwd\n",
            'FROM python:3.12\nCOPY ["../outside.txt", "/app/outside.txt"]\n',
        ],
    )
    def test_rejects_parent_traversal_in_copy_add(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) == RULE_PARENT_PATH

    @pytest.mark.parametrize(
        "dockerfile",
        [
            "FROM python:3.12\nCOPY app.py /app/\n",
            "FROM python:3.12\nCOPY src/pkg/module.py /app/\n",
            "FROM python:3.12\nADD assets/data.json /app/\n",
        ],
    )
    def test_allows_paths_within_context(self, dockerfile):
        assert _validate_dockerfile_content(dockerfile) is None

    def test_reject_dockerfile_via_folder(self, tmp_path):
        sol = _solution_dir(tmp_path, "FROM python:3.12\nADD ../outside.txt /app/\n")
        assert reject_dockerfile(sol) is False


class TestDockerfileParserEdgeCases:
    def test_line_continuation_allowed_copy(self):
        dockerfile = "FROM python:3.12\nCOPY app.py \\\n  /app/\n"
        assert _validate_dockerfile_content(dockerfile) is None

    def test_reject_dockerfile_passes_clean_policy(self, tmp_path):
        sol = _solution_dir(
            tmp_path,
            "FROM python:3.12\nCOPY app.py /app/\n",
        )
        assert reject_dockerfile(sol) is True
