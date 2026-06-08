#!/usr/bin/env python3
# Input: the live validator mounts /challenge_input/challenge_input.json
# ({difficulty, num, num_bits}); the workbench passes (challenge_id, problem_json)
# as argv. Both are supported. Output: logs, a magic separator, then a base64 zip
# of result.json + solve_info.json (see enigma_challenges.solution_output).
#
# Env: CADO_NFS, CADO_BINDIR, CADO_THREADS, CADO_WORKDIR, CADO_EXTRA_ARGS,
#      CADO_TIMEOUT, SCRATCH_MIN_GB, RHO_BUDGET.

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import *

import gmpy2
from gmpy2 import mpz, gcd

from enigma_challenges.breaking_rsa import Problem, Solution
from enigma_challenges.solution_output import build_solution_zip, write_solution_output

CHALLENGE_INPUT_FILE = "/challenge_input/challenge_input.json"


def _printlog(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _cpu_count() -> int:
    """Cores available to this container, honoring docker --cpus (the cgroup
    quota), which os.cpu_count() ignores (it reports host cores)."""
    n = os.cpu_count() or 8
    try:
        parts = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if parts and parts[0] != "max":
            return max(1, min(n, int(parts[0]) // int(parts[1])))
    except (OSError, ValueError, IndexError):
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            return max(1, min(n, quota // period))
    except (OSError, ValueError):
        pass
    return n


# --- Stage 0: cheap exact methods --------------------------------------------

def _trial_division(n: mpz, bound: int = 1_000_000) -> Optional[int]:
    if n % 2 == 0:
        return 2
    d = 3
    while d <= bound and d * d <= n:
        if n % d == 0:
            return int(d)
        d += 2
    return None


def _pollard_rho_brent(n: mpz, budget: int) -> Optional[int]:
    if n % 2 == 0:
        return 2
    iters = 0
    for c in range(1, 12):
        y, r, q, d = mpz(2), 1, mpz(1), mpz(1)
        c_m, x, ys = mpz(c), mpz(0), mpz(0)
        while d == 1 and iters < budget:
            x = y
            for _ in range(r):
                y = (y * y + c_m) % n
            k = 0
            while k < r and d == 1 and iters < budget:
                ys = y
                m = min(128, r - k)
                for _ in range(m):
                    y = (y * y + c_m) % n
                    q = (q * abs(x - y)) % n
                d = gcd(q, n)
                k += m
                iters += m
            r *= 2
        if d == n:
            backtrack = 0
            while backtrack < budget:
                ys = (ys * ys + c_m) % n
                d = gcd(abs(x - ys), n)
                backtrack += 1
                if d > 1:
                    break
        if 1 < d < n:
            return int(d)
        if iters >= budget:
            break
    return None


def _stage0(n: mpz, log) -> Optional[Tuple[int, int]]:
    f = _trial_division(n)
    if f:
        log(f"Stage 0: trial division found factor {f}")
        return f, int(n // f)
    r = gmpy2.isqrt(n)
    if r * r == n:
        log("Stage 0: N is a perfect square")
        return int(r), int(r)
    budget = _env_int("RHO_BUDGET", 2_000_000)
    f = _pollard_rho_brent(n, budget)
    if f and 1 < f < n:
        log(f"Stage 0: Pollard's rho found factor {f}")
        return int(f), int(n // f)
    return None


# --- Stage 1: CADO-NFS (GNFS) ------------------------------------------------

def _find_cado() -> Optional[str]:
    env = os.environ.get("CADO_NFS")
    if env and os.path.isfile(env):
        return env
    for c in ["/opt/cado/bin/cado-nfs.py", "/usr/local/bin/cado-nfs.py",
              shutil.which("cado-nfs.py")]:
        if c and os.path.isfile(c):
            return c
    return None


def _find_cado_bindir() -> Optional[str]:
    """Directory of the installed CADO binaries, passed as slaves.bindir so
    clients run them directly instead of downloading + exec'ing them."""
    import glob
    env = os.environ.get("CADO_BINDIR")
    if env and os.path.isdir(env):
        return env
    for d in sorted(glob.glob("/opt/cado/lib/cado-nfs-*"), reverse=True):
        if os.path.isdir(d):
            return d
    return None


def _writable(p: str) -> bool:
    try:
        os.makedirs(p, exist_ok=True)
        t = os.path.join(p, ".w_test")
        with open(t, "wb") as fh:
            fh.write(b"x")
        os.remove(t)
        return True
    except OSError:
        return False


def _pick_scratch(log) -> str:
    """CADO working directory: CADO_WORKDIR if set, else the writable tmpfs with
    the most free space."""
    forced = os.environ.get("CADO_WORKDIR")
    if forced:
        os.makedirs(forced, exist_ok=True)
        return forced
    best, best_free = "/tmp", -1.0
    for base in (os.environ.get("TMPDIR"), "/dev/shm", "/tmp", "/var/tmp"):
        if base and _writable(base):
            try:
                st = os.statvfs(base)
                free = st.f_bavail * st.f_frsize / 1e9
            except OSError:
                continue
            if free > best_free:
                best, best_free = base, free
    wd = os.path.join(best, "cado_run")
    os.makedirs(wd, exist_ok=True)
    if 0 <= best_free < _env_int("SCRATCH_MIN_GB", 3):
        log(f"Scratch: only {best_free:.1f} GB free at {wd}; a large GNFS may "
            "ENOSPC — set CADO_WORKDIR to a bigger writable path if so.")
    return wd


def _parse_cado_factors(text: str, n: mpz, log) -> Optional[Tuple[int, int]]:
    """CADO prints the prime factors space-separated on stdout. Scan every
    integer token, keep proper divisors of N, reconstruct the prime pair."""
    facs = sorted({int(t) for t in re.findall(r"\b\d+\b", text)
                   if 1 < int(t) < n and n % int(t) == 0})
    for f in facs:
        co = int(n // f)
        if f * co == n and gmpy2.is_prime(mpz(f)) and gmpy2.is_prime(mpz(co)):
            return (f, co) if f <= co else (co, f)
    for f in facs:
        if f * (n // f) == n:
            return int(f), int(n // f)
    return None


def _run_cado_gnfs(n: mpz, log) -> Optional[Tuple[int, int]]:
    cado = _find_cado()
    if not cado:
        log("Stage 1: CADO-NFS not found (set CADO_NFS); cannot run GNFS")
        return None
    threads = os.environ.get("CADO_THREADS") or str(_cpu_count())
    workdir = _pick_scratch(log)

    # CADO requires N and all key=value overrides to be contiguous, after the
    # -flag options. The overrides make CADO run inside the validator sandbox:
    # bind the server to localhost (no DNS under --network none), disable TLS,
    # and run the INSTALLED binaries instead of downloading + exec'ing them.
    flags = ["-t", str(threads), "--workdir", workdir]
    opts = ["server.address=localhost", "server.ssl=no"]
    bindir = _find_cado_bindir()
    if bindir:
        opts.append(f"slaves.bindir={bindir}")
    opts += os.environ.get("CADO_EXTRA_ARGS", "").split()
    cmd = [sys.executable, cado] + flags + [str(int(n))] + opts

    log(f"Stage 1: CADO-NFS GNFS, threads={threads}, workdir={workdir}")
    timeout = os.environ.get("CADO_TIMEOUT")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=int(timeout) if timeout else None)
    except subprocess.TimeoutExpired:
        log(f"Stage 1: CADO-NFS hit soft timeout ({timeout}s)")
        return None
    except Exception as e:
        log(f"Stage 1: CADO-NFS failed to run ({e})")
        return None

    log(f"Stage 1: CADO-NFS exited rc={proc.returncode}")
    res = _parse_cado_factors(proc.stdout + "\n" + (proc.stderr or ""), n, log)
    if not res:
        for line in (proc.stderr or "").strip().splitlines()[-6:]:
            log(f"  cado: {line[:200]}")
        log("Stage 1: CADO-NFS did not yield a valid factorization")
    return res


# --- Pipeline ----------------------------------------------------------------

def factor_semiprime(n_int: int, num_bits: int, log) -> Tuple[Optional[int], Optional[int], str]:
    n = mpz(n_int)
    log(f"Factoring {num_bits}-bit ({len(str(n_int))}-digit) semiprime")
    res = _stage0(n, log)
    if res:
        return res[0], res[1], "stage0"
    res = _run_cado_gnfs(n, log)
    if res:
        return res[0], res[1], "cado_gnfs"
    return None, None, "failed"


def _load_problem(log) -> Tuple[str, "Problem"]:
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
        prob = Problem.from_json(sys.argv[2].strip())
        log("Loaded problem from argv")
        return sys.argv[1].strip(), prob
    raise SystemExit("No problem input: expected /challenge_input/challenge_input.json "
                     "or <challenge_id> <problem_json> argv")


def main() -> None:
    timestamp_start = datetime.now(timezone.utc).isoformat()
    start = time.time()
    try:
        challenge_id, problem = _load_problem(_printlog)
    except SystemExit as e:
        print(str(e))
        sys.exit(1)
    if problem.num < 6:
        print("Error: number must be a positive non-trivial semiprime")
        sys.exit(1)

    _printlog(f"Starting Breaking RSA challenge: {challenge_id}")
    numstr = str(problem.num)
    _printlog(f"N = {numstr[:40]}{'...' if len(numstr) > 40 else ''} ({problem.num_bits} bits)")

    p, q, method = factor_semiprime(problem.num, problem.num_bits, log=_printlog)
    solve_time = time.time() - start

    ok = (p is not None and q is not None
          and mpz(p) * mpz(q) == problem.num
          and gmpy2.is_prime(mpz(p)) and gmpy2.is_prime(mpz(q)))
    if ok:
        _printlog(f"SUCCESS via {method} in {solve_time:.2f}s")
        solution = Solution("success", int(p), int(q))
    else:
        _printlog(f"FAILED after {solve_time:.2f}s")
        solution = Solution("failed", None, None)

    result_json = json.dumps(solution.to_dict(), indent=2)
    solve_info_json = json.dumps({
        "solution_status": solution.status,
        "challenge_id": challenge_id,
        "timestamp_utc": timestamp_start,
        "solve_time_seconds": solve_time,
        "method": method,
        "num_bits": problem.num_bits,
    })

    output_dir = os.environ.get("OUTPUT_DIR")
    if output_dir:
        try:
            Path(output_dir).mkdir(exist_ok=True)
            Path(output_dir, "result.json").write_text(result_json)
            Path(output_dir, "solve_info.json").write_text(solve_info_json)
        except OSError:
            pass

    zip_bytes = build_solution_zip({
        "result.json": result_json,
        "solve_info.json": solve_info_json,
    })
    write_solution_output(zip_bytes)
    os._exit(0 if solution.status == "success" else 1)


if __name__ == "__main__":
    main()
