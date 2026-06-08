#!/usr/bin/env python3
"""Local smoke tests for the GNFS solver wrapper.

Exercises the full submission contract WITHOUT needing Docker, CADO, or a GPU:
  - argv input parsing
  - Stage 0 factoring of small semiprimes
  - the stdout `logs -> separator -> base64(zip)` output protocol
  - result.json / solve_info.json correctness + self-verify
  - graceful failure path when the heavy engine isn't available

Run:  python local_test.py
"""
import base64
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import gmpy2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from enigma_challenges.solution_output import SOLUTION_OUTPUT_SEPARATOR  # noqa: E402


def _prime_near(bits: int, offset: int) -> int:
    base = (1 << (bits - 1)) | 1 | (offset << 4)
    return int(gmpy2.next_prime(base))


def run_solver(num: int, num_bits: int, difficulty: int = 1):
    problem = json.dumps({"difficulty": difficulty, "num": num, "num_bits": num_bits})
    proc = subprocess.run(
        [sys.executable, str(HERE / "breaking_rsa.py"), "cid-123", problem],
        capture_output=True, timeout=300,
    )
    return proc.returncode, proc.stdout


def parse_output(raw: bytes):
    idx = raw.find(SOLUTION_OUTPUT_SEPARATOR)
    assert idx != -1, "separator not found in solver stdout"
    logs = raw[:idx]
    payload_b64 = raw[idx + len(SOLUTION_OUTPUT_SEPARATOR):].strip()
    zip_bytes = base64.b64decode(payload_b64, validate=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        result = json.loads(zf.read("result.json"))
        info = json.loads(zf.read("solve_info.json"))
    return logs, names, result, info


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main() -> int:
    ok = True

    # --- Test A: small semiprime, expect Stage 0 success -------------------
    print("Test A: 60-bit semiprime (expect Stage 0 success, exit 0)")
    p, q = _prime_near(30, 7), _prime_near(30, 99)
    n = p * q
    rc, raw = run_solver(n, n.bit_length())
    logs, names, result, info = parse_output(raw)
    ok &= check("exit code 0", rc == 0)
    ok &= check("separator + base64 zip present", True)
    ok &= check("zip has result.json + solve_info.json",
                {"result.json", "solve_info.json"} <= names)
    ok &= check("status == success", result.get("status") == "success")
    ok &= check("p*q == N", result.get("p", 0) * result.get("q", 0) == n)
    ok &= check("both factors prime",
                bool(gmpy2.is_prime(result["p"])) and bool(gmpy2.is_prime(result["q"])))
    ok &= check("method == stage0", info.get("method") == "stage0")
    ok &= check("challenge_id echoed", info.get("challenge_id") == "cid-123")
    print(f"    factored {n} = {result.get('p')} * {result.get('q')}")

    # --- Test B: hard semiprime, no CADO here -> graceful failure ----------
    print("Test B: 170-bit semiprime, no engine installed (expect clean FAIL, exit 1)")
    p2, q2 = _prime_near(85, 12345), _prime_near(85, 67890)
    n2 = p2 * q2
    rc2, raw2 = run_solver(n2, n2.bit_length())
    logs2, names2, result2, info2 = parse_output(raw2)
    ok &= check("exit code 1", rc2 == 1)
    ok &= check("output protocol still valid (zip decoded)",
                {"result.json", "solve_info.json"} <= names2)
    ok &= check("status == failed", result2.get("status") == "failed")
    ok &= check("p/q are null on failure",
                result2.get("p") is None and result2.get("q") is None)
    ok &= check("reached Stage 1 (CADO-not-found logged)",
                b"CADO-NFS not found" in raw2 or b"cado" in raw2.lower())

    # --- Test C: perfect square degenerate case ---------------------------
    print("Test C: perfect square N = r^2 (expect Stage 0 success)")
    r = _prime_near(40, 5)
    n3 = r * r
    rc3, raw3 = run_solver(n3, n3.bit_length())
    _, _, result3, info3 = parse_output(raw3)
    ok &= check("exit code 0", rc3 == 0)
    ok &= check("p == q == r", result3.get("p") == r and result3.get("q") == r)

    print()
    print("ALL TESTS PASSED" if ok else "SOME TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
