#!/usr/bin/env python3
# Breaking RSA solver — CADO-NFS (GNFS) with a RAM-backed working directory.
#
# Two ideas adapted from the "yasu" solution:
#   * PRECOMPILED CADO-NFS (shipped as cado-nfs.tar.gz, extracted to
#     /opt/cado-nfs) so the image build never runs the long from-source cmake
#     build that can exceed the validator's image build timeout.
#   * The `ramnfs` broker + LD_PRELOAD shim, which redirects CADO's multi-GB
#     relation scratch under /ramwork into Linux memfd_create RAM files. THIS is
#     what bypasses the validator's small (~1 GB) /tmp — not the precompile.
#
# Pipeline: Stage 0 (trial division / perfect square / bounded Pollard rho) →
# Stage 1 (ECM pretest: cheap insurance against a weak or unbalanced modulus) →
# Stage 2 (CADO-NFS GNFS through the RAM-shim — the real engine).
#
# Input: the live validator mounts /challenge_input/challenge_input.json
# ({difficulty, num, num_bits}); the workbench passes (challenge_id,
# problem_json) as argv. Output: logs, a magic separator, then a base64 zip of
# result.json + solve_info.json (see enigma_challenges.solution_output).
#
# Env: WALL_TIME, DEADLINE_MARGIN, ECM_BIN, ECM_PRETEST_CAP, CADO_NFS,
#      CADO_THREADS, RAMNFS_BROKER, RAMNFS_SHIM, RAMNFS_SOCK, RAMNFS_WORKDIR,
#      MIN_FACTOR_BITS, RHO_BUDGET.

from datetime import datetime, timezone
import glob
import json
import math
import os
from pathlib import Path
import re
import signal
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


def _min_factor_digits(num_bits: int) -> int:
    """Decimal digits of the smaller factor of a balanced semiprime: at most
    ~num_bits/2 bits. MIN_FACTOR_BITS overrides the half-of-N assumption."""
    fb = _env_int("MIN_FACTOR_BITS", 0) or (num_bits + 1) // 2
    return max(1, int(fb * math.log10(2)))


def _verify_pair(f: int, n: mpz) -> Optional[Tuple[int, int]]:
    """Ordered prime pair if f is a valid prime factor of N, else None."""
    f = int(f)
    if f <= 1 or f >= n or n % f != 0:
        return None
    co = int(n // f)
    if gmpy2.is_prime(mpz(f)) and gmpy2.is_prime(mpz(co)):
        return (f, co) if f <= co else (co, f)
    return None


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
    """Brent's improvement on Pollard's rho. The budget counts ACTUAL modmuls
    (not outer iterations) so it terminates promptly on a hard semiprime instead
    of looping while r doubles unbounded."""
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


def _stage0(n: mpz, min_factor_digits: int, log) -> Optional[Tuple[int, int]]:
    log("Stage 0: trial division + perfect square + bounded Pollard rho")
    f = _trial_division(n)
    if f:
        log(f"Stage 0: trial division found factor {f}")
        return _verify_pair(f, n) or (f, int(n // f))
    r = gmpy2.isqrt(n)
    if r * r == n:
        log("Stage 0: N is a perfect square")
        return int(r), int(r)
    # Pollard rho only reaches small factors; skip it when the smaller factor is
    # known to be far larger than rho's practical range.
    if min_factor_digits <= 20:
        f = _pollard_rho_brent(n, _env_int("RHO_BUDGET", 2_000_000))
        if f:
            log(f"Stage 0: Pollard's rho found factor {f}")
            return _verify_pair(f, n) or (int(f), int(n // f))
    return None


# --- Stage 1: ECM pretest ----------------------------------------------------

def _find_bin(env_key: str, candidates: List[str]) -> Optional[str]:
    env = os.environ.get(env_key, "").strip()
    if env and os.path.isfile(env):
        return env
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _run_ecm_pretest(n: mpz, deadline: float, log) -> Optional[Tuple[int, int]]:
    ecm = _find_bin("ECM_BIN", ["/usr/local/bin/ecm", "/usr/bin/ecm"])
    if not ecm:
        log("Stage 1: ECM binary not found; skipping")
        return None
    cap = _env_int("ECM_PRETEST_CAP", 60)
    budget = max(5, min(cap, int((deadline - time.time()) * 0.05)))
    log(f"Stage 1: ECM pretest, budget={budget}s")
    try:
        proc = subprocess.run([ecm, "-q", "11e6"], input=f"{int(n)}\n",
                              capture_output=True, text=True, timeout=budget)
    except subprocess.TimeoutExpired:
        log("Stage 1: ECM pretest found nothing (timed out)")
        return None
    except Exception as e:  # noqa: BLE001
        log(f"Stage 1: ECM error: {e}")
        return None
    for tok in re.findall(r"\b\d{6,}\b", proc.stdout or ""):
        pair = _verify_pair(int(tok), n)
        if pair:
            log(f"Stage 1: ECM found factor {pair[0]}")
            return pair
    return None


# --- Stage 2: CADO-NFS (GNFS) through the ramnfs RAM-shim ---------------------

def _find_cado_script() -> Optional[str]:
    env = os.environ.get("CADO_NFS", "").strip()
    if env and os.path.isfile(env):
        return env
    for c in ["/opt/cado-nfs/build/release/cado-nfs.py",
              "/usr/local/bin/cado-nfs.py", "/usr/bin/cado-nfs.py"]:
        if os.path.isfile(c):
            return c
    for pat in ["/opt/cado-nfs/build/*/cado-nfs.py", "/usr/local/lib/cado-nfs-*/cado-nfs.py"]:
        m = sorted(glob.glob(pat))
        if m:
            return m[-1]
    return None


def _start_broker(sock_path: str, log) -> Optional[subprocess.Popen]:
    broker = _find_bin("RAMNFS_BROKER", ["/opt/ramnfs/broker", "/app/ramnfs/broker"])
    if not broker:
        log("ramnfs: broker binary not found")
        return None
    try:
        os.unlink(sock_path)
    except OSError:
        pass
    try:
        proc = subprocess.Popen([broker, sock_path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        log(f"ramnfs: failed to start broker: {e}")
        return None
    for _ in range(50):
        if os.path.exists(sock_path):
            log(f"ramnfs: broker started (pid={proc.pid})")
            return proc
        time.sleep(0.1)
    log("ramnfs: broker socket did not appear after 5s")
    proc.kill()
    return None


def _factors_from_line(line: str, n: mpz) -> Optional[Tuple[int, int]]:
    """CADO prints the prime factors space-separated on one line ("p q")."""
    parts = line.split()
    if len(parts) < 2 or not all(re.fullmatch(r"\d+", p) for p in parts):
        return None
    prod = 1
    for p in parts:
        prod *= int(p)
    if prod == n and all(gmpy2.is_prime(mpz(int(p))) for p in parts):
        a, b = int(parts[0]), int(parts[1])
        return (a, b) if a <= b else (b, a)
    return None


def _run_cado(n: mpz, deadline: float, log) -> Optional[Tuple[int, int]]:
    cado = _find_cado_script()
    if not cado:
        log("Stage 2: CADO-NFS script not found; skipping")
        return None
    shim = _find_bin("RAMNFS_SHIM", ["/opt/ramnfs/shim.so", "/app/ramnfs/shim.so"])
    sock = os.environ.get("RAMNFS_SOCK", "/tmp/ramnfs.sock")
    workdir = os.environ.get("RAMNFS_WORKDIR", "/ramwork/factor.work")
    threads = _env_int("CADO_THREADS", 0) or _cpu_count()

    broker = None
    if shim:
        broker = _start_broker(sock, log)
        if not broker:
            log("ramnfs: broker unavailable; falling back to /tmp (may ENOSPC)")
            shim = None
    if not shim:
        workdir = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cado_run")
        os.makedirs(workdir, exist_ok=True)

    env = dict(os.environ)
    env["HOME"] = env["TMPDIR"] = "/tmp"
    if shim:
        env["LD_PRELOAD"] = shim
        env["RAMNFS_SOCK"] = sock
        env["RAMNFS_PREFIX"] = "/ramwork"

    remaining = max(60, int(deadline - time.time()))
    cado_build = str(Path(cado).parent)
    cmd = [
        sys.executable, cado, str(int(n)),
        f"tasks.workdir={workdir}",
        f"tasks.threads={threads}",
        "server.address=localhost", "server.port=0", "server.threaded=1",
        f"slaves.nrclients={threads}",
        f"slaves.cado_nfs_client.bindir={cado_build}",
        f"tasks.linalg.bwc.threads={threads}",
        "tasks.sieve.las.threads=1",
    ]
    # By default let CADO choose size-appropriate parameters (degree, admax) from
    # its parameter files — a hardcoded degree=5 breaks small inputs that want
    # degree 4. Override only when explicitly asked.
    if os.environ.get("CADO_ADMAX"):
        cmd.append(f"tasks.polyselect.admax={os.environ['CADO_ADMAX']}")
    if os.environ.get("CADO_DEGREE"):
        cmd.append(f"tasks.polyselect.degree={os.environ['CADO_DEGREE']}")
    ndigits = len(str(int(n)))
    log(f"Stage 2: CADO c{ndigits} ({int(n).bit_length()}-bit) threads={threads} "
        f"workdir={workdir} ram_shim={'on' if shim else 'off'} budget={remaining}s")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, start_new_session=True)
    factors: Optional[Tuple[int, int]] = None
    tail: List[str] = []
    last_log = 0.0
    t0 = time.time()
    try:
        assert proc.stdout
        for raw in proc.stdout:
            for line in raw.replace("\r", "\n").splitlines():
                s = line.strip()
                if not s:
                    continue
                tail.append(s)
                del tail[:-40]
                factors = _factors_from_line(s, n)
                if factors:
                    log("Stage 2: CADO found factors")
                    break
                now = time.time()
                if now - last_log > 120:
                    log(f"Stage 2: CADO running ... {int(now - t0)}s elapsed")
                    last_log = now
                if now >= deadline:
                    log(f"Stage 2: CADO hit deadline after {int(now - t0)}s")
                    break
            if factors or time.time() >= deadline:
                break
    except Exception as e:  # noqa: BLE001
        log(f"Stage 2: CADO read error: {e}")
    finally:
        _kill(proc)
        if broker:
            _kill(broker)

    if not factors:
        for ln in tail[-12:]:
            log(f"  cado: {ln[:200]}")
    return factors


def _kill(proc: subprocess.Popen) -> None:
    """Terminate a process and its session group (CADO spawns child workers)."""
    if proc is None or proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.wait(timeout=8)
            return
        except Exception:  # noqa: BLE001
            continue


# --- Pipeline ----------------------------------------------------------------

def factor_semiprime(n_int: int, num_bits: int, deadline: float,
                     log) -> Tuple[Optional[int], Optional[int], str]:
    n = mpz(n_int)
    min_digits = _min_factor_digits(num_bits)
    log(f"Factoring {num_bits}-bit ({len(str(n_int))}-digit) semiprime; "
        f"smaller factor ~{min_digits} digits")
    res = _stage0(n, min_digits, log)
    if res:
        return res[0], res[1], "stage0"
    res = _run_ecm_pretest(n, deadline, log)
    if res:
        return res[0], res[1], "ecm"
    res = _run_cado(n, deadline, log)
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
        except Exception as e:  # noqa: BLE001
            log(f"Failed to parse {CHALLENGE_INPUT_FILE}: {e}")
    if len(sys.argv) == 3:
        prob = Problem.from_json(sys.argv[2].strip())
        log("Loaded problem from argv")
        return sys.argv[1].strip(), prob
    raise SystemExit("No problem input: expected /challenge_input/challenge_input.json "
                     "or <challenge_id> <problem_json> argv")


def main() -> None:
    wall = _env_int("WALL_TIME", 14400)
    margin = _env_int("DEADLINE_MARGIN", 120)
    timestamp_start = datetime.now(timezone.utc).isoformat()
    start = time.time()
    deadline = start + wall - margin
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

    p, q, method = factor_semiprime(problem.num, problem.num_bits, deadline, log=_printlog)
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
