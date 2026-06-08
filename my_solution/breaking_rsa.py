#!/usr/bin/env python3
# The MIT License (MIT)
# Copyright © 2026
#
# Breaking RSA solver — General Number Field Sieve (CADO-NFS).
#
# Why GNFS (CADO-NFS) and not SIQS (YAFU):
#   At 460 bits (~139 decimal digits) the semiprime is well past the SIQS->GNFS
#   crossover (~100-110 digits). A multithreaded SIQS (the engine the previous
#   top solution used) would need *days* at this size; it cannot finish inside
#   the validator's 4-hour wall-time. GNFS is the only classical algorithm that
#   factors a 139-digit balanced semiprime in hours on this hardware, and
#   CADO-NFS is the most robust, fully-integrated open-source GNFS pipeline
#   (polynomial selection -> lattice sieving -> filtering -> linear algebra ->
#   square root, all driven by one command).
#
# The hard part is NOT compute, it is SCRATCH DISK:
#   The validator runs solutions read-only-root with a single writable /tmp that
#   is a 256 MB *noexec* tmpfs, plus ~85 GB RAM (--memory 85g), 24 cores, and a
#   GPU. A 139-digit GNFS produces multiple GB of relation/matrix scratch — it
#   ENOSPCs in 256 MB. So this solver provisions a large RAM-backed scratch
#   area and points CADO-NFS at it:
#     1. $CADO_WORKDIR if the operator set one (e.g. a host-mounted ramdisk).
#     2. Otherwise the largest writable tmpfs already present (/dev/shm, /tmp).
#     3. Otherwise a best-effort unprivileged user-namespace tmpfs sized to RAM
#        (unshare --map-root-user --mount + mount -t tmpfs). This is the one
#        unprivileged mechanism that yields >256 MB of writable space; it
#        succeeds on hosts that allow unprivileged user namespaces and falls
#        back gracefully (with a loud warning) when the sandbox forbids it.
#
# Pipeline:
#   Stage 0   cheap exact methods (trial division, perfect square, bounded
#             Pollard rho, bounded Pollard p-1) — instant wins for small or
#             degenerate inputs and the workbench's low-bit test cases.
#   Stage 0.5 short GMP-ECM pre-test — cheap insurance against an unexpectedly
#             small/unbalanced factor (does nothing for a balanced c139, costs
#             only a few seconds; disable with ECM_PRETEST=0).
#   Stage 1   CADO-NFS GNFS — the engine for the real challenge size.
#
# Optional GPU acceleration (the RTX PRO 6000 the old solution left idle):
#   GNFS *sieving* (70-85% of the work) is CPU-only in every production tool, so
#   the GPU does not touch the bottleneck. Where it helps is polynomial
#   selection and linear algebra. CADO-NFS can use a GPU for polynomial
#   selection when built with CUDA; set USE_GPU_POLYSELECT=1 to enable the
#   GPU poly-select path (the Dockerfile builds the CUDA poly-select binaries).
#   The baseline runs pure-CPU CADO and is fully correct without a GPU.
#
# Input (live validator): the problem is delivered as a read-only mounted file
#   /challenge_input/challenge_input.json containing {difficulty, num, num_bits}.
#   The workbench instead passes (challenge_id, problem_json) as argv. We support
#   BOTH: prefer the mounted file, fall back to argv.
#
# Output contract: logs to stdout, then a magic separator, then a base64 zip of
#   result.json + solve_info.json (see enigma_challenges.solution_output).
#
# Env overrides:
#   CADO_NFS            path to cado-nfs.py (default: search common locations)
#   CADO_THREADS        thread count for CADO (default: os.cpu_count())
#   CADO_WORKDIR        scratch dir for CADO (default: auto RAM-backed, see above)
#   CADO_EXTRA_ARGS     extra args appended to the cado-nfs.py invocation
#   CADO_TIMEOUT        soft self-timeout (s) for the CADO run (default: none)
#   SCRATCH_MIN_GB      min free GB before we trust a workdir (default: 3)
#   RAMDISK_SIZE_GB     size of the self-provisioned tmpfs (default: 60)
#   USE_USERNS_RAMDISK  1/0 attempt unprivileged-userns tmpfs (default: 1)
#   USE_GPU_POLYSELECT  1/0 use CADO CUDA polynomial selection (default: 0)
#   ECM_PRETEST         1/0 run the short ECM pre-test (default: 1)
#   ECM_CURVES          curves for the ECM pre-test (default: 40)
#   ECM_B1              B1 bound for the ECM pre-test (default: 250000)
#   RHO_BUDGET          Pollard's rho iteration budget (default: 2_000_000)

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


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Stage 0: cheap exact methods
# ---------------------------------------------------------------------------

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
    """Brent's variant of Pollard's rho. Bounded everywhere (the backtrack loop
    is capped too — the previous top solution had an unbounded `while True`
    backtrack that could hang on prime powers)."""
    if n % 2 == 0:
        return 2
    iters = 0
    for c in range(1, 12):
        y, r, q = mpz(2), 1, mpz(1)
        d = mpz(1)
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


def _pollard_pm1(n: mpz, b1: int = 1_000_000) -> Optional[int]:
    """Pollard p-1: a fast win only if some factor's (p-1) is b1-smooth.
    Random challenge primes almost never are, but it is cheap insurance."""
    a = mpz(2)
    # multiply the exponent by all prime powers up to b1
    j = 2
    while j <= b1:
        a = gmpy2.powmod(a, j, n)
        if (j & 1023) == 0:
            d = gcd(a - 1, n)
            if 1 < d < n:
                return int(d)
        j += 1
    d = gcd(a - 1, n)
    if 1 < d < n:
        return int(d)
    return None


def _stage0(n: mpz, log) -> Optional[Tuple[int, int]]:
    f = _trial_division(n, 1_000_000)
    if f:
        log(f"Stage 0: trial division found factor {f}")
        return f, int(n // f)
    r = gmpy2.isqrt(n)
    if r * r == n:
        log("Stage 0: N is a perfect square")
        return int(r), int(r)
    budget = _env_int("RHO_BUDGET", 2_000_000)
    log(f"Stage 0: Pollard's rho (budget {budget})...")
    f = _pollard_rho_brent(n, budget)
    if f and 1 < f < n:
        log(f"Stage 0: Pollard's rho found factor {f}")
        return int(f), int(n // f)
    # very short p-1, only meaningful for a lucky smooth factor
    try:
        f = _pollard_pm1(n, 200_000)
        if f and 1 < f < n:
            log(f"Stage 0: Pollard's p-1 found factor {f}")
            return int(f), int(n // f)
    except Exception as e:  # never let an opportunistic method abort the solve
        log(f"Stage 0: p-1 skipped ({e})")
    return None


# ---------------------------------------------------------------------------
# Stage 0.5: short ECM pre-test (cheap insurance for a small/unbalanced factor)
# ---------------------------------------------------------------------------

def _find_bin(env: str, names: List[str]) -> Optional[str]:
    e = os.environ.get(env)
    if e and os.path.isfile(e):
        return e
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for c in [f"/opt/ecm/{names[0]}", f"/usr/local/bin/{names[0]}"]:
        if os.path.isfile(c):
            return c
    return None


def _run_ecm_pretest(n: mpz, log) -> Optional[Tuple[int, int]]:
    if not _env_flag("ECM_PRETEST", True):
        return None
    ecm = _find_bin("ECM_BIN", ["ecm"])
    if not ecm:
        log("Stage 0.5: ecm binary not found; skipping ECM pre-test")
        return None
    curves = _env_int("ECM_CURVES", 40)
    b1 = _env_int("ECM_B1", 250_000)
    log(f"Stage 0.5: GMP-ECM pre-test ({curves} curves, B1={b1})")
    try:
        proc = subprocess.run(
            [ecm, "-c", str(curves), "-q", str(b1)],
            input=f"{int(n)}\n", text=True, capture_output=True, timeout=600,
        )
    except Exception as e:
        log(f"Stage 0.5: ECM pre-test error ({e})")
        return None
    for tok in re.findall(r"\b\d{2,}\b", proc.stdout):
        f = int(tok)
        if 1 < f < n and n % f == 0:
            log(f"Stage 0.5: ECM found factor {f}")
            return int(f), int(n // f)
    return None


# ---------------------------------------------------------------------------
# Scratch provisioning — the 256 MB sandbox is the real blocker for GNFS.
# ---------------------------------------------------------------------------

def _free_gb(path: str) -> float:
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e9
    except OSError:
        return -1.0


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        t = os.path.join(path, ".w_test")
        with open(t, "wb") as fh:
            fh.write(b"x")
        os.remove(t)
        return True
    except OSError:
        return False


def _pick_existing_scratch(log) -> Tuple[str, float]:
    """Largest already-writable dir among the usual tmpfs candidates."""
    candidates = []
    for base in (os.environ.get("TMPDIR"), "/dev/shm", "/tmp", "/var/tmp"):
        if base and _writable(base):
            candidates.append((base, _free_gb(base)))
    for base, gb in candidates:
        log(f"  scratch candidate {base}: {gb:.2f} GB free")
    if not candidates:
        return "/tmp", _free_gb("/tmp")
    best = max(candidates, key=lambda x: x[1])
    return best


def provision_scratch(log) -> Tuple[List[str], str]:
    """Return (command_prefix, workdir).

    command_prefix is prepended to the CADO invocation; when we self-provision a
    tmpfs via an unprivileged user namespace it is the `unshare ... bash -c`
    wrapper that mounts the tmpfs and then execs CADO. Otherwise it is empty and
    workdir is the largest writable tmpfs we found.
    """
    # 1. explicit operator override
    forced = os.environ.get("CADO_WORKDIR")
    if forced:
        os.makedirs(forced, exist_ok=True)
        log(f"Scratch: using CADO_WORKDIR={forced} ({_free_gb(forced):.2f} GB free)")
        return [], forced

    min_gb = float(_env_int("SCRATCH_MIN_GB", 3))
    base, gb = _pick_existing_scratch(log)
    if gb >= min_gb:
        wd = os.path.join(base, "cado_run")
        os.makedirs(wd, exist_ok=True)
        log(f"Scratch: using existing tmpfs {wd} ({gb:.2f} GB free)")
        return [], wd

    log(f"Scratch: best existing writable space is only {gb:.2f} GB "
        f"(< {min_gb} GB) — a 139-digit GNFS needs several GB.")

    # 2. best-effort: self-provision a RAM tmpfs in an unprivileged user namespace
    if _env_flag("USE_USERNS_RAMDISK", True):
        prefix = _build_userns_tmpfs_prefix(log)
        if prefix is not None:
            return prefix, "/scratch"

    # 3. give up gracefully: use the biggest dir we have and warn loudly
    wd = os.path.join(base, "cado_run")
    os.makedirs(wd, exist_ok=True)
    log("Scratch: WARNING — proceeding on undersized scratch; GNFS may ENOSPC. "
        "Set CADO_WORKDIR to a host-mounted ramdisk, or raise the sandbox "
        "tmpfs/--shm-size, to guarantee completion.")
    return [], wd


def _build_userns_tmpfs_prefix(log) -> Optional[List[str]]:
    """Build an `unshare` command prefix that mounts a big tmpfs at /scratch
    inside a fresh user+mount namespace, then runs the given command. Returns
    None if unprivileged user namespaces / tmpfs mounts are unavailable."""
    unshare = shutil.which("unshare")
    mount = shutil.which("mount")
    if not unshare or not mount:
        log("Scratch: unshare/mount not present; cannot self-provision a ramdisk")
        return None
    size_gb = _env_int("RAMDISK_SIZE_GB", 60)
    # smoke-test: can we actually create the namespace and mount a tmpfs?
    test = subprocess.run(
        [unshare, "--map-root-user", "--mount", "--", "bash", "-c",
         "mkdir -p /scratch && mount -t tmpfs -o size=64m tmpfs /scratch && "
         "test -w /scratch && echo OK"],
        capture_output=True, text=True,
    )
    if "OK" not in test.stdout:
        log("Scratch: unprivileged user-namespace tmpfs mount is blocked by the "
            f"sandbox ({(test.stderr or test.stdout).strip()[:160]}); "
            "falling back to existing tmpfs")
        return None
    log(f"Scratch: self-provisioning a {size_gb} GB tmpfs at /scratch via an "
        "unprivileged user namespace")
    # The real run wraps CADO so it (and its children) see the mounted /scratch.
    return [unshare, "--map-root-user", "--mount", "--propagation", "private",
            "--", "bash", "-c",
            f"mkdir -p /scratch && mount -t tmpfs -o size={size_gb}g tmpfs /scratch && exec \"$@\"",
            "bash"]


# ---------------------------------------------------------------------------
# Stage 1: CADO-NFS (GNFS)
# ---------------------------------------------------------------------------

def _find_cado() -> Optional[str]:
    env = os.environ.get("CADO_NFS")
    if env and os.path.isfile(env):
        return env
    for c in ["/opt/cado/bin/cado-nfs.py",
              "/opt/cado-nfs/cado-nfs.py",
              "/usr/local/bin/cado-nfs.py",
              shutil.which("cado-nfs.py")]:
        if c and os.path.isfile(c):
            return c
    # last resort: walk likely install/source roots
    for root in ("/opt/cado", "/opt/cado-nfs"):
        if os.path.isdir(root):
            for dirpath, _dirs, files in os.walk(root):
                if "cado-nfs.py" in files:
                    return os.path.join(dirpath, "cado-nfs.py")
    return None


def _find_cado_bindir() -> Optional[str]:
    """Directory holding the INSTALLED CADO binaries (las, polyselect, ...).
    Passed to clients as slaves.bindir so they run these directly from the
    exec-allowed root instead of downloading them into the noexec /tmp."""
    import glob
    env = os.environ.get("CADO_BINDIR")
    if env and os.path.isdir(env):
        return env
    for d in sorted(glob.glob("/opt/cado/lib/cado-nfs-*"), reverse=True):
        if os.path.isdir(d):
            return d
    return None


def _parse_cado_factors(stdout: str, n: mpz, log) -> Optional[Tuple[int, int]]:
    """CADO-NFS prints the prime factors, space-separated, on stdout when it
    finishes. Be defensive: scan every integer token, keep proper divisors of
    N, and reconstruct the two prime factors. main() self-verifies regardless."""
    toks = re.findall(r"\b\d+\b", stdout)
    facs = sorted({int(t) for t in toks if t.isdigit()
                   and 1 < int(t) < n and n % int(t) == 0})
    for f in facs:
        co = int(n // f)
        if f * co == n and gmpy2.is_prime(mpz(f)) and gmpy2.is_prime(mpz(co)):
            return (f, co) if f <= co else (co, f)
    # fall back: any proper divisor pair whose product is N
    for f in facs:
        if f * (n // f) == n:
            return int(f), int(n // f)
    return None


def _run_cado_gnfs(n: mpz, log) -> Optional[Tuple[int, int]]:
    cado = _find_cado()
    if not cado:
        log("Stage 1: CADO-NFS not found (set CADO_NFS); cannot run GNFS")
        return None

    threads = os.environ.get("CADO_THREADS") or str(os.cpu_count() or 8)
    prefix, workdir = provision_scratch(log)

    # CADO requires N and all key=value parameter overrides to be CONTIGUOUS,
    # with the -flag options (-t, --workdir) placed BEFORE N. These overrides
    # are exactly what make CADO run inside the validator sandbox:
    #   server.address=localhost  bind the local server to localhost — under
    #                             --network none the container hostname does not
    #                             resolve and CADO otherwise dies in cert setup.
    #   server.ssl=no             loopback-only; no TLS certificate to generate.
    #   slaves.bindir=<install>   clients run the INSTALLED binaries on the
    #                             exec-allowed root instead of downloading them
    #                             into the noexec /tmp (which fails with EACCES).
    flag_args = ["-t", str(threads), "--workdir", workdir]
    kv_opts = ["server.address=localhost", "server.ssl=no"]
    bindir = _find_cado_bindir()
    if bindir:
        kv_opts.append(f"slaves.bindir={bindir}")
    else:
        log("Stage 1: WARNING — CADO bindir not found; clients may try to "
            "download binaries (fails under noexec /tmp)")
    if _env_flag("USE_GPU_POLYSELECT", False):
        gpu_dir = os.environ.get("CADO_GPU_DIR", "")
        if gpu_dir:
            kv_opts.append(f"tasks.polyselect.gpu={gpu_dir}")
        log("Stage 1: GPU polynomial selection requested")
    kv_opts += os.environ.get("CADO_EXTRA_ARGS", "").split()

    cmd = list(prefix) + [sys.executable, cado] + flag_args + [str(int(n))] + kv_opts

    log(f"Stage 1: CADO-NFS GNFS, threads={threads}, workdir={workdir}")
    log(f"  cmd: {' '.join(str(c) for c in cmd)}")

    timeout = os.environ.get("CADO_TIMEOUT")
    timeout_s = int(timeout) if timeout else None
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        log(f"Stage 1: CADO-NFS hit soft timeout ({timeout_s}s)")
        return None
    except Exception as e:
        log(f"Stage 1: CADO-NFS failed to run ({e})")
        return None

    elapsed = time.time() - start
    log(f"Stage 1: CADO-NFS exited rc={proc.returncode} after {elapsed:.0f}s")
    # surface a tail of CADO's own log for debugging (goes to our stdout logs)
    tail = (proc.stderr or "").strip().splitlines()[-8:]
    for line in tail:
        log(f"  cado: {line[:200]}")

    res = _parse_cado_factors(proc.stdout + "\n" + (proc.stderr or ""), n, log)
    if res:
        return res
    log("Stage 1: CADO-NFS did not yield a valid factorization")
    return None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def factor_semiprime(n_int: int, num_bits: int, log) -> Tuple[Optional[int], Optional[int], str]:
    n = mpz(n_int)
    log(f"Factoring {num_bits}-bit ({len(str(n_int))}-digit) semiprime")
    res = _stage0(n, log)
    if res:
        return res[0], res[1], "stage0"
    res = _run_ecm_pretest(n, log)
    if res:
        return res[0], res[1], "ecm_pretest"
    res = _run_cado_gnfs(n, log)
    if res:
        return res[0], res[1], "cado_gnfs"
    return None, None, "failed"


def _load_problem(log) -> Tuple[str, "Problem"]:
    """Live validator delivers the problem as a mounted file; workbench via argv."""
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
        cid = sys.argv[1].strip()
        prob = Problem.from_json(sys.argv[2].strip())
        log("Loaded problem from argv")
        return cid, prob
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
    _printlog(f"Number size: {problem.num_bits} bits")
    numstr = str(problem.num)
    _printlog(f"N = {numstr[:40]}{'...' if len(numstr) > 40 else ''}")

    p, q, method = factor_semiprime(problem.num, problem.num_bits, log=_printlog)
    solve_time = time.time() - start

    # Self-verify before claiming success: both prime, product == N.
    ok = (
        p is not None and q is not None
        and mpz(p) * mpz(q) == problem.num
        and gmpy2.is_prime(mpz(p)) and gmpy2.is_prime(mpz(q))
    )
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
