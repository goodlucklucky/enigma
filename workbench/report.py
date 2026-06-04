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

import sys
from typing import Optional
from workbench.validator import CheckResult
from workbench.verifier import VerifyResult


def _truncate(s: str, max_len: int = 60) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."


def print_report(
    challenge_type: str,
    mode: str,
    seed: int,
    problem_summary: dict,
    build_result=None,
    run_result=None,
    validation_results: list[CheckResult] | None = None,
    verify_result: VerifyResult | None = None,
    total_time: float = 0.0,
):
    """Print a structured test report to stdout."""
    lines = []
    lines.append("")
    lines.append("Workbench Test Report")
    lines.append("=====================")
    lines.append(f"Challenge:  {challenge_type}")
    lines.append(f"Mode:       {mode}")
    lines.append(f"Seed:       {seed}")

    for key, value in problem_summary.items():
        if key not in ("challenge_type", "mode", "seed"):
            display_val = _truncate(str(value))
            lines.append(f"{key:12s}{display_val}")

    lines.append("")

    # Build & Run section
    if build_result is not None or run_result is not None:
        lines.append("--- Build & Run ---")
        if build_result is not None:
            status = "OK" if build_result.success else "FAILED"
            lines.append(f"Docker build:   {status} ({build_result.duration:.2f}s)")
            if not build_result.success:
                for log_line in build_result.log.strip().splitlines()[-10:]:
                    lines.append(f"  {log_line}")
        if run_result is not None:
            if run_result.exit_code == -1:
                status = "TIMEOUT"
            elif run_result.success:
                status = f"OK (exit 0, {run_result.duration:.2f}s)"
            else:
                status = f"FAILED (exit {run_result.exit_code}, {run_result.duration:.2f}s)"
            lines.append(f"Container run:  {status}")
            if not run_result.success and run_result.log.strip():
                for log_line in run_result.log.strip().splitlines()[-10:]:
                    lines.append(f"  {log_line}")
        lines.append("")

    # Structural validation
    if validation_results is not None:
        lines.append("--- Structural Validation ---")
        all_passed = True
        for check in validation_results:
            tag = "PASS" if check.passed else "FAIL"
            lines.append(f"[{tag}] {check.name}")
            if not check.passed and check.message:
                lines.append(f"       {check.message}")
                all_passed = False
        lines.append("")

    # Verification
    if verify_result is not None:
        lines.append("--- Solution Verification ---")
        if verify_result.passed:
            lines.append(f"[PASS] {verify_result.message}")
        else:
            lines.append(f"[FAIL] {verify_result.message}")
            if verify_result.expected:
                lines.append(f"       Expected: {_truncate(verify_result.expected, 120)}")
            if verify_result.actual:
                lines.append(f"       Got:      {_truncate(verify_result.actual, 120)}")
        lines.append("")
    elif validation_results is not None:
        has_failures = any(not c.passed for c in validation_results)
        if has_failures:
            lines.append("--- Solution Verification ---")
            lines.append("[SKIP] Skipped due to validation failures")
            lines.append("")

    # Summary
    lines.append(f"Total time: {total_time:.2f}s")

    all_ok = True
    if validation_results and any(not c.passed for c in validation_results):
        all_ok = False
    if verify_result and not verify_result.passed:
        all_ok = False
    if build_result and not build_result.success:
        all_ok = False

    lines.append(f"Result: {'ALL CHECKS PASSED' if all_ok else 'CHECKS FAILED'}")
    lines.append("")

    print("\n".join(lines))
    return all_ok
