#!/usr/bin/env python3
# Breaking RSA solver — YAFU factor() (automated SOTA factoring).
#
# YAFU auto-selects the method (trial division, rho, p-1, ECM, then SIQS). It
# wins smaller milestones quickly (fast AVX SIQS + ECM). NOTE: YAFU's NFS path
# would need the external GGNFS lattice sievers (ggnfs_dir in yafu.ini); without
# them, large inputs fall back to SIQS, which is too slow past ~110 digits — so
# this engine does not clear a 460-bit number in 4 h (no factoring engine does
# on one machine; see README).
#
# Input: live validator mounts /challenge_input/challenge_input.json; workbench
# passes (challenge_id, problem_json) as argv. Output: logs + separator +
# base64 zip of result.json + solve_info.json.
#
# Env: YAFU_BIN, YAFU_THREADS, YAFU_WORKDIR, YAFU_TIMEOUT.

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import gmpy2
from gmpy2 import mpz

from enigma_challenges.breaking_rsa import Problem, Solution
from enigma_challenges.solution_output import build_solution_zip, write_solution_output

CHALLENGE_INPUT_FILE = "/challenge_input/challenge_input.json"


def _printlog(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def _find_yafu():
    env = os.environ.get("YAFU_BIN")
    if env and os.path.isfile(env):
        return env
    for c in ["/opt/yafu/yafu", "/usr/local/bin/yafu", shutil.which("yafu")]:
        if c and os.path.isfile(c):
            return c
    return None


def _pick_workdir():
    base = os.environ.get("YAFU_WORKDIR") or os.environ.get("TMPDIR") or "/tmp"
    wd = os.path.join(base, "yafu_run")
    os.makedirs(wd, exist_ok=True)
    return wd


def _run_yafu(n, log):
    yafu = _find_yafu()
    if not yafu:
        log("YAFU not found (set YAFU_BIN)")
        return None
    threads = os.environ.get("YAFU_THREADS") or str(os.cpu_count() or 8)
    wd = _pick_workdir()
    ini = os.path.join(os.path.dirname(yafu), "yafu.ini")
    if os.path.isfile(ini):  # carry ggnfs_dir etc. into the run dir
        try:
            shutil.copy(ini, os.path.join(wd, "yafu.ini"))
        except OSError:
            pass
    log(f"Running YAFU factor(), threads={threads}, workdir={wd}")
    timeout = os.environ.get("YAFU_TIMEOUT")
    try:
        proc = subprocess.run(
            [yafu, "-threads", str(threads)],
            input=f"factor({int(n)})\n",
            cwd=wd, capture_output=True, text=True,
            timeout=int(timeout) if timeout else None,
        )
    except subprocess.TimeoutExpired:
        log(f"YAFU hit soft timeout ({timeout}s)")
        return None
    except Exception as e:
        log(f"YAFU failed to run ({e})")
        return None
    text = proc.stdout + "\n" + (proc.stderr or "")
    facs = sorted({int(t) for t in re.findall(r"\bP\d+\s*=\s*(\d+)", text)
                   if 1 < int(t) < n and n % int(t) == 0})
    for f in facs:
        co = int(n // f)
        if f * co == n and gmpy2.is_prime(mpz(f)) and gmpy2.is_prime(mpz(co)):
            return (f, co) if f <= co else (co, f)
    log("YAFU did not yield a valid factorization")
    return None


def _load_problem(log):
    if os.path.isfile(CHALLENGE_INPUT_FILE):
        try:
            data = json.loads(Path(CHALLENGE_INPUT_FILE).read_text())
            prob = Problem(int(data["difficulty"]), int(data["num"]), int(data["num_bits"]))
            cid = (sys.argv[1].strip() if len(sys.argv) > 1 else "") or "challenge"
            log(f"Loaded problem from {CHALLENGE_INPUT_FILE}")
            return cid, prob
        except Exception as e:
            log(f"Failed to parse {CHALLENGE_INPUT_FILE}: {e}")
    if len(sys.argv) == 3:
        return sys.argv[1].strip(), Problem.from_json(sys.argv[2].strip())
    raise SystemExit("No problem input")


def main():
    ts = datetime.now(timezone.utc).isoformat()
    start = time.time()
    try:
        cid, problem = _load_problem(_printlog)
    except SystemExit as e:
        print(str(e))
        sys.exit(1)
    if problem.num < 6:
        print("Error: number must be a positive non-trivial semiprime")
        sys.exit(1)
    _printlog(f"Starting Breaking RSA challenge: {cid} ({problem.num_bits} bits)")

    res = _run_yafu(mpz(problem.num), _printlog)
    solve_time = time.time() - start
    p, q = (res if res else (None, None))
    ok = (p is not None and mpz(p) * mpz(q) == problem.num
          and gmpy2.is_prime(mpz(p)) and gmpy2.is_prime(mpz(q)))
    if ok:
        _printlog(f"SUCCESS via yafu in {solve_time:.2f}s")
        solution = Solution("success", int(p), int(q))
    else:
        _printlog(f"FAILED after {solve_time:.2f}s")
        solution = Solution("failed", None, None)

    result_json = json.dumps(solution.to_dict(), indent=2)
    solve_info_json = json.dumps({
        "solution_status": solution.status, "challenge_id": cid,
        "timestamp_utc": ts, "solve_time_seconds": solve_time,
        "method": "yafu", "num_bits": problem.num_bits,
    })
    out = os.environ.get("OUTPUT_DIR")
    if out:
        try:
            Path(out).mkdir(exist_ok=True)
            Path(out, "result.json").write_text(result_json)
            Path(out, "solve_info.json").write_text(solve_info_json)
        except OSError:
            pass
    write_solution_output(build_solution_zip({
        "result.json": result_json, "solve_info.json": solve_info_json}))
    os._exit(0 if solution.status == "success" else 1)


if __name__ == "__main__":
    main()
