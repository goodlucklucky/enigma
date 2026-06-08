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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from qbittensor.validator.solution.validate_docker_image import (
    _validate_dockerfile_content,
    REJECTED_DOCKERFILE_RULES,
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str = ""


def validate_output(
    output_dir: str,
    challenge_type: str,
    check_dockerfile: bool = False,
    solution_dir: str | None = None,
) -> list[CheckResult]:
    """Run structural validation checks on solver output."""
    results = []
    output_path = Path(output_dir)

    # Check Dockerfile (Docker mode only)
    if check_dockerfile and solution_dir:
        df = _find_dockerfile(solution_dir)
        results.append(CheckResult(
            name="Dockerfile present",
            passed=df is not None,
            message="" if df is not None else f"No Dockerfile found in {solution_dir}",
        ))
        # Run the full platform-equivalent security policy (same rules as validator)
        results.append(validate_dockerfile_security(solution_dir))

    # Check required output files
    for filename in ["result.json", "stdout.log", "solve_info.json"]:
        filepath = output_path / filename
        results.append(CheckResult(
            name=f"{filename} exists",
            passed=filepath.exists(),
            message="" if filepath.exists() else f"Missing {filepath}",
        ))

    # Check result.json is valid JSON
    result_path = output_path / "result.json"
    if result_path.exists():
        try:
            with result_path.open() as f:
                json.load(f)
            results.append(CheckResult(
                name="result.json is valid JSON",
                passed=True,
            ))
        except json.JSONDecodeError as e:
            results.append(CheckResult(
                name="result.json is valid JSON",
                passed=False,
                message=f"Invalid JSON: {e}",
            ))
            return results  # Can't check schema if JSON is invalid
    else:
        return results  # Can't check JSON or schema if file missing

    # Check result.json matches Solution schema
    try:
        with result_path.open() as f:
            data = json.load(f)

        if challenge_type == "breaking_rsa":
            from qbittensor.challenges.breaking_rsa import Solution
        elif challenge_type == "mock":
            from qbittensor.challenges.mock_challenge import Solution
        else:
            results.append(CheckResult(
                name="result.json matches Solution schema",
                passed=False,
                message=f"Unknown challenge type: {challenge_type}",
            ))
            return results

        Solution.from_dict(data)
        results.append(CheckResult(
            name="result.json matches Solution schema",
            passed=True,
        ))
    except KeyError as e:
        results.append(CheckResult(
            name="result.json matches Solution schema",
            passed=False,
            message=f"Missing field: {e}",
        ))
    except TypeError as e:
        results.append(CheckResult(
            name="result.json matches Solution schema",
            passed=False,
            message=f"Type error: {e}",
        ))

    return results


def _find_dockerfile(solution_dir: str) -> Path | None:
    """Find Dockerfile in workbench solution layout (at root of solution_dir)."""
    base = Path(solution_dir)
    for name in ("Dockerfile", "dockerfile"):
        p = base / name
        if p.is_file():
            return p
    return None


def validate_dockerfile_security(solution_dir: str) -> CheckResult:
    """Apply the exact same Dockerfile security policy the validator uses.

    Rejects:
    - EXPOSE
    - VOLUME
    - COPY --from referencing external images or out-of-range stages
    - COPY/ADD with absolute paths
    - COPY/ADD with parent traversal (../)

    This ensures a solution that passes workbench docker-mode testing will
    not be rejected by the platform validator for Dockerfile policy violations.
    """
    df_path = _find_dockerfile(solution_dir)
    if df_path is None:
        return CheckResult(
            name="Dockerfile security policy",
            passed=False,
            message="No Dockerfile found in solution directory",
        )

    try:
        content = df_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return CheckResult(
            name="Dockerfile security policy",
            passed=False,
            message=f"Could not read Dockerfile: {e}",
        )

    violation = _validate_dockerfile_content(content)
    if violation is None:
        return CheckResult(
            name="Dockerfile security policy",
            passed=True,
        )

    return CheckResult(
        name="Dockerfile security policy",
        passed=False,
        message=violation,
    )
