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

import json
import re

import bittensor as bt
from pathlib import Path

from .run_solution import _run_docker_command
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.exceptions.validation_errors import ValidationErrors

REJECTED_DOCKERFILE_RULES: tuple[str, ...] = (
    "EXPOSE instructions are not allowed",
    "VOLUME instructions are not allowed",
    "COPY --from must refer to a build stage in the same Dockerfile, not an external image",
    "COPY and ADD source paths must not use absolute host file paths",
    "COPY and ADD source paths must not navigate outside the Dockerfile directory (e.g. ../)",
)

_INSTRUCTION = re.compile(
    r"^\s*(?P<cmd>[A-Z]+)(?:\s+(?P<args>.+))?$",
    re.IGNORECASE,
)
_FROM_AS = re.compile(r"^\s*FROM\s+.+\s+AS\s+(\S+)", re.IGNORECASE)
_COPY_ADD_FLAG = re.compile(
    r"--(?:from|chown|chmod|link|exclude|parents|platform)=[^\s]+",
    re.IGNORECASE,
)


def _code_dir(folder_name: str) -> Path:
    return Path(folder_name) / "code"


def _dockerfile_path(folder_name: str) -> Path | None:
    code_dir = _code_dir(folder_name)
    for name in ("Dockerfile", "dockerfile"):
        path = code_dir / name
        if path.is_file():
            return path
    return None


def _strip_comment(line: str) -> str:
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _logical_lines(content: str) -> list[str]:
    physical = [_strip_comment(line).rstrip() for line in content.splitlines()]
    merged: list[str] = []
    buf = ""
    for line in physical:
        stripped = line.strip()
        if not stripped:
            if buf:
                merged.append(buf)
                buf = ""
            continue
        piece = stripped if not buf else f"{buf} {stripped}"
        if line.rstrip().endswith("\\"):
            buf = piece[:-1].rstrip()
            continue
        merged.append(piece)
        buf = ""
    if buf:
        merged.append(buf)
    return merged


def _parse_stages(lines: list[str]) -> set[str]:
    stages: set[str] = set()
    for line in lines:
        match = _FROM_AS.match(line)
        if match:
            stages.add(match.group(1).lower())
    return stages


def _copy_from_ref(args: str) -> str | None:
    match = re.search(r"--from=([^\s]+)", args, re.IGNORECASE)
    return match.group(1) if match else None


def _parse_copy_add_paths(args: str) -> list[str]:
    remaining = _COPY_ADD_FLAG.sub("", args).strip()
    if remaining.startswith("["):
        try:
            parsed = json.loads(remaining)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list) or len(parsed) < 2:
            return []
        return [str(p) for p in parsed[:-1]]

    parts = remaining.split()
    if len(parts) < 2:
        return []
    return parts[:-1]


def _copy_from_is_external(ref: str, stages: set[str]) -> bool:
    if ref.isdigit():
        return False
    if ":" in ref or "/" in ref:
        return True
    return ref.lower() not in stages


def _path_escapes_context(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    for part in normalized.split("/"):
        if part == "..":
            return True
    return False


def _validate_dockerfile_content(content: str) -> str | None:
    """Return the first violated rule message, or None if the Dockerfile is allowed."""
    lines = _logical_lines(content)
    stages = _parse_stages(lines)
    stage_count = sum(
        1
        for line in lines
        if (m := _INSTRUCTION.match(line)) and m.group("cmd").upper() == "FROM"
    )

    for line in lines:
        match = _INSTRUCTION.match(line)
        if not match:
            continue
        cmd = match.group("cmd").upper()
        args = match.group("args") or ""

        if cmd == "EXPOSE":
            return REJECTED_DOCKERFILE_RULES[0]
        if cmd == "VOLUME":
            return REJECTED_DOCKERFILE_RULES[1]
        if cmd not in ("COPY", "ADD"):
            continue

        from_ref = _copy_from_ref(args)
        if from_ref is not None:
            if _copy_from_is_external(from_ref, stages):
                return REJECTED_DOCKERFILE_RULES[2]
            if from_ref.isdigit():
                idx = int(from_ref)
                if idx < 0 or idx >= stage_count:
                    return REJECTED_DOCKERFILE_RULES[2]
            continue

        for src in _parse_copy_add_paths(args):
            if _path_escapes_context(src):
                if src.replace("\\", "/").startswith("/"):
                    return REJECTED_DOCKERFILE_RULES[3]
                return REJECTED_DOCKERFILE_RULES[4]

    return None


def reject_dockerfile(folder_name: str) -> bool:
    """Return True if the Dockerfile passes security policy, False if rejected."""
    path = _dockerfile_path(folder_name)
    if path is None:
        return False

    violation = _validate_dockerfile_content(path.read_text(encoding="utf-8", errors="replace"))
    if violation is None:
        bt.logging.info("\t✅ Dockerfile passed security policy")
        return True

    bt.logging.error(f"\t❌ Dockerfile rejected: {violation}")
    return False


def _image_exists(image_name: str) -> bool:
    """Checks whether or not the image exists.

    Uses the shared docker runner so we get good diagnostics logged on failure.
    """
    cmd = ["docker", "image", "inspect", image_name]
    try:
        result = _run_docker_command(cmd, description="docker image inspect (existence check)", check=False)
        exists = result.returncode == 0
        if not exists:
            bt.logging.error(f"\t❌ Image '{image_name}' does not exist")
            if result.stderr.strip():
                bt.logging.error(f"\t   stderr: {result.stderr.strip()}")
        return exists
    except Exception as e:
        bt.logging.error(f"\t❌ Error while checking if image '{image_name}' exists: {e}")
        return False


def validate_image(image_name: str) -> bool:
    """Validate the docker image.

    Raises InvalidSolutionError with rich diagnostics on critical failures
    (e.g. Docker CLI unavailable).
    """
    bt.logging.info("🕵 Validating docker image")
    if not _image_exists(image_name=image_name):
        # _image_exists already logged the reason
        raise InvalidSolutionError(
            message=ValidationErrors.DOCKER_IMAGE_VALIDATION_FAILED.value
        )
    bt.logging.info(f"\t✅ Validation for image '{image_name}' successful")
    return True
