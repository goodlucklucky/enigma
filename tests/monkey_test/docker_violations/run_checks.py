#!/usr/bin/env python3
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

"""Run reject_dockerfile against every scenario under docker_violations/."""

from pathlib import Path

from qbittensor.validator.solution.validate_docker_image import reject_dockerfile

ROOT = Path(__file__).resolve().parent

EXPECTED = {
    "allowed_relative_copy": True,
    "allowed_multistage_copy": True,
    "allowed_lowercase_dockerfile": True,
    "violation_volume": False,
    "violation_expose": False,
    "violation_copy_from_external_image": False,
    "violation_copy_from_unknown_stage": False,
    "violation_copy_from_stage_index": False,
    "violation_copy_absolute_path": False,
    "violation_add_absolute_path": False,
    "violation_copy_json_absolute": False,
    "violation_add_parent_traversal": False,
    "violation_copy_nested_parent_traversal": False,
}


def main() -> int:
    failures = 0
    for name in sorted(EXPECTED):
        path = ROOT / name
        got = reject_dockerfile(str(path))
        expect = EXPECTED[name]
        status = "PASS" if got == expect else "MISMATCH"
        if got != expect:
            failures += 1
        label = "allow" if expect else "reject"
        print(f"{status:8} {name:42} expected={label} got={'allow' if got else 'reject'}")
    if failures:
        print(f"\n{failures} scenario(s) did not match expected policy outcome")
        return 1
    print(f"\nAll {len(EXPECTED)} scenarios matched expected policy outcome")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
