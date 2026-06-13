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
# Pipeline: Stage 2 only — CADO-NFS GNFS through the RAM-shim.
#
# Input: the live validator mounts /challenge_input/challenge_input.json
# ({difficulty, num, num_bits}); the workbench passes (challenge_id,
# problem_json) as argv. Output: logs, a magic separator, then a base64 zip of
# result.json + solve_info.json (see enigma_challenges.solution_output).
#
# Env: WALL_TIME, DEADLINE_MARGIN, CADO_NFS, CADO_THREADS,
#      RAMNFS_BROKER, RAMNFS_SHIM, RAMNFS_SOCK, RAMNFS_WORKDIR.

from datetime import datetime, timezone
import glob
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import *

import gmpy2
from gmpy2 import mpz

from enigma_challenges.breaking_rsa import Problem, Solution
from enigma_challenges.solution_output import build_solution_zip, write_solution_output
import gpu_la

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


def _find_bin(env_key: str, candidates: List[str]) -> Optional[str]:
    env = os.environ.get(env_key, "").strip()
    if env and os.path.isfile(env):
        return env
    for c in candidates:
        if os.path.isfile(c):
            return c
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


def _default_param(ndigits: int, key: str = "tasks.polyselect.ropteffort") -> Optional[float]:
    """CADO's calibrated value of `key` for the params.cNN file nearest this size.

    Used to bump a tuning knob multiplicatively off its per-size default. CADO's
    defaults jump non-linearly with size (e.g. ropteffort c140=16, c145=18,
    c150=25), so a fixed bumped value would be a regression at some sizes —
    reading the per-size default avoids that."""
    pdir = None
    cado = _find_cado_script()
    cands = []
    if cado:
        for up in Path(cado).resolve().parents:
            cands.append(up / "parameters" / "factor")
    cands.append(Path("/opt/cado-nfs/parameters/factor"))
    for c in cands:
        if c.is_dir():
            pdir = c
            break
    if not pdir:
        return None
    best, best_d = None, 1 << 30
    for f in pdir.glob("params.c[0-9]*"):
        try:
            nn = int(f.name[len("params.c"):])
        except ValueError:
            continue
        d = abs(nn - ndigits)
        if d < best_d:
            best, best_d = f, d
    if not best:
        return None
    try:
        for line in best.read_text().splitlines():
            m = re.match(r"\s*" + re.escape(key) + r"\s*=\s*([0-9.]+)", line)
            if m:
                return float(m.group(1))
    except OSError:
        pass
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
        # NOTE: the broker is deliberately left in OUR process group/session (no
        # start_new_session). It must share the session so the LD_PRELOAD shim in
        # CADO's worker subprocesses talks to it correctly — giving the broker its
        # own session makes CADO's freerel step fail to see its output files.
        # Because of that, the broker must be torn down by PID only (see
        # _terminate_pid), never via os.killpg, which would signal our own group.
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


def _invoke_cado(cmd: List[str], env: dict, n: mpz, deadline: float, label: str,
                 log) -> Tuple[Optional[Tuple[int, int]], int]:
    """Run one cado-nfs.py invocation under the deadline watchdog.

    Returns (factors_or_None, return_code). Factors are parsed from stdout (only
    the linalg+sqrt phases print them); a filter-only run returns (None, 0)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, start_new_session=True)
    factors: Optional[Tuple[int, int]] = None
    tail: List[str] = []
    last_log = 0.0
    t0 = time.time()

    # Deadline watchdog. CADO's later phases (linear algebra, sqrt) can run for
    # many minutes without emitting a stdout line, during which the blocking
    # `for raw in proc.stdout` below would sleep right past the wall-clock budget.
    # The watchdog kills CADO when the deadline passes, which closes its stdout
    # and unblocks the read loop so we always return (and emit output) in time.
    stop = threading.Event()

    def _watchdog() -> None:
        while not stop.wait(2.0):
            if time.time() >= deadline:
                log(f"Stage 2: {label} hit deadline after {int(time.time() - t0)}s; terminating")
                _kill(proc)
                return

    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()
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
                    log(f"Stage 2: {label} found factors")
                    break
                now = time.time()
                if now - last_log > 120:
                    log(f"Stage 2: {label} running ... {int(now - t0)}s elapsed")
                    last_log = now
            if factors:
                break
    except Exception as e:  # noqa: BLE001
        log(f"Stage 2: {label} read error: {e}")
    finally:
        stop.set()
        _kill(proc)
    rc = proc.poll()
    if not factors and rc not in (0, None):
        for ln in tail[-10:]:
            log(f"  cado: {ln[:200]}")
    return factors, (rc if rc is not None else 1)


def _parse_cpulist(cl: str) -> List[int]:
    """Expand a Linux cpulist ("0-7,16" form) into explicit CPU ids."""
    out: List[int] = []
    for part in cl.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _numa_plan(threads: int, las_threads: int, log) -> Optional[list]:
    """Plan one pinned sieve-client group per CPU locality domain.

    Returns [(membind_node|None, [cpu ids], las_threads), ...] or None when NUMA
    pinning doesn't apply. The launcher slices each node's CPUs into DISTINCT
    per-client core sets (idea #2): the old plan pinned every client to the whole
    node's cpuset, so N clients floated over the same N cores and the scheduler
    migrated them, thrashing per-core L1/L2 on the bandwidth-bound sieve. Real
    kernel NUMA nodes (/sys) drive production; CADO_NUMA_TEST=k simulates k CPU
    groups on one node (CPU-pin only) so the launcher can be validated on
    single-node hardware."""
    test_k = _env_int("CADO_NUMA_TEST", 0)
    if test_k > 1:
        cpus = list(range(threads))
        per = max(1, len(cpus) // test_k)
        groups = []
        for g in range(test_k):
            chunk = cpus[g * per:] if g == test_k - 1 else cpus[g * per:(g + 1) * per]
            if chunk:
                groups.append((None, chunk, las_threads))
        return groups if len(groups) > 1 else None
    nodes = []
    for d in sorted(glob.glob("/sys/devices/system/node/node[0-9]*")):
        try:
            cl = Path(d, "cpulist").read_text().strip()
        except OSError:
            continue
        if not cl:
            continue
        cpus = _parse_cpulist(cl)
        if cpus:
            nodes.append((int(os.path.basename(d)[4:]), cpus, las_threads))
    if len(nodes) <= 1:
        log(f"NUMA: {len(nodes)} node(s) — pinning not applicable, using auto-clients")
        return None
    return nodes


def _invoke_cado_numa(server_cmd: List[str], plan: list, client_py: str,
                      bindir: str, workdir: str, env: dict, n: mpz,
                      deadline: float, label: str, log
                      ) -> Tuple[Optional[Tuple[int, int]], int]:
    """Run CADO as a server (no auto-clients) plus manually-launched sieve
    clients, each numactl-pinned to one CPU/NUMA group. Mirrors _invoke_cado's
    watchdog + factor-parsing read loop, but feeds many local NUMA-local clients
    so sieving stays on local memory on big multi-socket boxes."""
    server = subprocess.Popen(server_cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, env=env,
                              start_new_session=True)
    clients: List[subprocess.Popen] = []
    factors: Optional[Tuple[int, int]] = None
    tail: List[str] = []
    launched = False
    last_log = 0.0
    t0 = time.time()
    conn_re = re.compile(r"--server=(\S+)\s+--certsha1=(\S+)")

    stop = threading.Event()

    def _watchdog() -> None:
        while not stop.wait(2.0):
            if time.time() >= deadline:
                log(f"Stage 2: {label} hit deadline after {int(time.time()-t0)}s; terminating")
                _kill(server)
                return

    def _launch(url: str, sha: str) -> None:
        total = 0
        for gi, (node, cpus, lt) in enumerate(plan):
            nc = max(1, len(cpus) // lt)
            for i in range(nc):
                # Each client gets its OWN distinct cores (last absorbs remainder)
                # + that node's local memory — no two clients share a core, and
                # the scheduler can't migrate them off-node.
                chunk = cpus[i * lt:] if i == nc - 1 else cpus[i * lt:(i + 1) * lt]
                cl = ",".join(str(c) for c in chunk)
                numa = ["numactl", f"--physcpubind={cl}"]
                # --membind needs the set_mempolicy capability, which the
                # validator's unprivileged (--read-only) container DENIES
                # ("Operation not permitted") — that aborts every client before
                # it can sieve. CPU-pinning is allowed (sched_setaffinity), and
                # Linux first-touch then places each pinned client's pages on its
                # local node anyway, so we get locality without the privileged
                # syscall. Only add --membind when explicitly enabled on a
                # permissive host (CADO_NUMA_MEMBIND=1).
                if node is not None and os.environ.get("CADO_NUMA_MEMBIND"):
                    numa.append(f"--membind={node}")
                ccmd = numa + [sys.executable, client_py,
                               f"--server={url}", f"--certsha1={sha}",
                               f"--bindir={bindir}",
                               f"--basepath={os.path.join(workdir, f'cl.{gi}.{i}')}"]
                try:
                    clients.append(subprocess.Popen(
                        ccmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env=env, start_new_session=True))
                    total += 1
                except Exception as e:  # noqa: BLE001
                    log(f"Stage 2: {label} client launch failed: {e}")
        log(f"Stage 2: {label} launched {total} NUMA-pinned clients "
            f"(distinct cores) across {len(plan)} node(s)")

    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()
    try:
        assert server.stdout
        for raw in server.stdout:
            for line in raw.replace("\r", "\n").splitlines():
                s = line.strip()
                if not s:
                    continue
                tail.append(s)
                del tail[:-40]
                if not launched:
                    m = conn_re.search(s)
                    if m:
                        _launch(m.group(1), m.group(2))
                        launched = True
                factors = _factors_from_line(s, n)
                if factors:
                    log(f"Stage 2: {label} found factors")
                    break
                now = time.time()
                if now - last_log > 120:
                    log(f"Stage 2: {label} running ... {int(now - t0)}s elapsed")
                    last_log = now
            if factors:
                break
    except Exception as e:  # noqa: BLE001
        log(f"Stage 2: {label} read error: {e}")
    finally:
        stop.set()
        for c in clients:
            _kill(c)            # each client is its own session (las children)
        _kill(server)
    rc = server.poll()
    if not factors and rc not in (0, None):
        for ln in tail[-10:]:
            log(f"  cado: {ln[:200]}")
    return factors, (rc if rc is not None else 1)


def _run_cado(n: mpz, deadline: float, log) -> Optional[Tuple[int, int]]:
    """Stage 2: GNFS via CADO with ramnfs, optionally offloading the linear
    algebra to the GPU (msieve block Lanczos) when a GPU is present.

    Always-on safety net: the GPU path is attempted only when a GPU is detected,
    and ANY failure (export, build, LA, sqrt, or a product that doesn't equal N)
    falls back to CADO's own linear algebra by RESUMING on the same, still-alive
    broker — so the relations collected during sieving are never thrown away and
    the result is never worse than the CPU-only solution."""
    cado = _find_cado_script()
    if not cado:
        log("Stage 2: CADO-NFS script not found; skipping")
        return None
    shim = _find_bin("RAMNFS_SHIM", ["/opt/ramnfs/shim.so", "/app/ramnfs/shim.so"])
    sock = os.environ.get("RAMNFS_SOCK", "/tmp/ramnfs.sock")
    workdir = os.environ.get("RAMNFS_WORKDIR", "/ramwork/factor.work")
    threads = _env_int("CADO_THREADS", 0) or _cpu_count()

    # Start every run from a clean slate. CADO resumes from a SQLite state DB; the
    # ramnfs shim keeps that DB on the REAL filesystem (/tmp/cado-sqlite) while the
    # bulk data lives in the ephemeral broker. A DB left over from a prior run makes
    # CADO skip steps whose data files no longer exist in the fresh broker and abort
    # (e.g. "freerel.gz does not exist"). A fresh container never sees this, but
    # clearing the stale CADO scratch makes every invocation idempotent. /tmp/cado-
    # sqlite mirrors SQLITE_REAL_DIR in ramnfs/shim.c.
    for stale in ("/tmp/cado-sqlite",
                  os.path.join(os.environ.get("TMPDIR", "/tmp"), "cado_run")):
        shutil.rmtree(stale, ignore_errors=True)

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
    ndigits = len(str(int(n)))
    nbits = int(n).bit_length()

    # Sieve throughput tuning (research-backed; see git history). On many-core
    # NUMA servers, ~2-4 threads per las with several clients beats 1 thread per
    # core (sieving saturates at a few threads and CADO's NUMA placement wants
    # several clients per node). All knobs are env-overridable so the optimal
    # values can be A/B'd on the actual target hardware.
    las_threads = _env_int("CADO_LAS_THREADS", 1)
    n_clients = _env_int("CADO_NCLIENTS", 0) or max(1, threads // las_threads)

    base_cmd = [
        sys.executable, cado, str(int(n)),
        f"tasks.workdir={workdir}",
        f"tasks.threads={threads}",
        "server.address=localhost", "server.port=0", "server.threaded=1",
        f"slaves.nrclients={n_clients}",
        f"slaves.cado_nfs_client.bindir={cado_build}",
        f"tasks.linalg.bwc.threads={threads}",
        f"tasks.sieve.las.threads={las_threads}",
        # NOTE: do NOT set tasks.sieve.adjust_strategy (benchmarked value 2 made the
        # sieve ~5x slower). Let CADO pick size-appropriate polyselect params from
        # its calibrated params.cNNN; do not force degree/admax (see git history).
    ]
    # Explicit per-knob overrides (empty = CADO's calibrated default). I and
    # las.threads are intentionally NOT auto-tuned — they must be A/B'd on the
    # actual target CPU (e.g. I=13->14, las.threads=1->2), so they stay manual.
    for env_key, cado_key in (("CADO_ADMAX", "tasks.polyselect.admax"),
                              ("CADO_DEGREE", "tasks.polyselect.degree"),
                              ("CADO_I", "tasks.I"),
                              ("CADO_QMIN", "tasks.sieve.qmin"),
                              # idea #5: bucket-allocation margin. CADO grows the
                              # bucket arrays and RE-SIEVES the region when one
                              # overflows; pre-allocating more margin (e.g. 1.15)
                              # trades a little RAM to avoid those restarts. (las
                              # has no hugepage flag, so bkmult is the lever.)
                              ("CADO_BKMULT", "tasks.sieve.bkmult")):
        v = os.environ.get(env_key, "").strip()
        if v:
            base_cmd.append(f"{cado_key}={v}")

    # Size-aware sieve tuning — applied ONLY to large (production c130-c145)
    # inputs, where it cuts sieve wall time; never to small inputs (the c90 A/B
    # showed no benefit there and these defaults are calibrated for the big
    # range). Explicit env vars still win.
    #   * ropteffort: a better polynomial (higher Murphy-E) yields more relations
    #     per special-q, so sieving needs fewer of them. Bumped MULTIPLICATIVELY
    #     off CADO's per-size default so it is always an increase.
    #   * rels_wanted=1: stop sieving as soon as filtering can succeed, instead of
    #     collecting CADO's built-in safety margin (c140 ships 71M).
    tune_min = _env_int("SIEVE_TUNE_MIN_DIGITS", 120)
    tuned: List[str] = []

    def _bump(env_name: str, mult_env: str, cado_key: str, default_mult: float,
              fallback: float, auto: bool = True) -> None:
        """Set `cado_key` to (per-size default x mult) for large inputs.
        `env_name` (explicit value) always wins. When auto=False the bump is
        OPT-IN: it applies only if the *_MULT env is explicitly set — used for
        levers whose benefit a fast benchmark could not confirm, so the default
        stays exactly CADO's calibrated value (no silent regression)."""
        v = os.environ.get(env_name, "").strip()
        enabled = auto or bool(os.environ.get(mult_env, "").strip())
        if not v and enabled and ndigits >= tune_min:
            base = _default_param(ndigits, cado_key) or fallback
            try:
                mult = float(os.environ.get(mult_env, str(default_mult)) or default_mult)
            except ValueError:
                mult = default_mult
            v = str(round(base * mult, 1)).rstrip("0").rstrip(".")
        if v:
            base_cmd.append(f"{cado_key}={v}")
            tuned.append(f"{cado_key.split('.')[-1]}={v}")

    # idea #4 (better poly): ropteffort (more root-opt) stays auto-on (it was
    # already the shipped default). nrkeep (keep more stage-1 survivors) is
    # OPT-IN: the fast benchmark showed no Murphy-E gain at testable search sizes
    # while tripling poly-select time, so enable it only via CADO_NRKEEP[_MULT].
    _bump("CADO_ROPTEFFORT", "CADO_ROPTEFFORT_MULT", "tasks.polyselect.ropteffort", 1.5, 16.0)
    _bump("CADO_NRKEEP", "CADO_NRKEEP_MULT", "tasks.polyselect.nrkeep", 1.5, 60, auto=False)

    # idea #3 (sieve/LA rebalance): rels_wanted=1 stops sieving as soon as
    # filtering can build a matrix, instead of collecting CADO's safety margin
    # (c140 ships 71M). The denser/just-enough matrix is handed to the fast GPU
    # block-Lanczos, so we spend cheap GPU-LA time to save expensive sieve time.
    relsw = os.environ.get("CADO_RELS_WANTED", "").strip()
    if not relsw and ndigits >= tune_min:
        relsw = "1"
    if relsw:
        base_cmd.append(f"tasks.sieve.rels_wanted={relsw}")
        tuned.append(f"rels_wanted={relsw}")

    # Optional NUMA-pinned client launch (opt-in via CADO_NUMA). On big
    # multi-socket boxes, binding each sieve client to one node's CPUs + local
    # memory removes remote-memory penalties on the bandwidth-bound sieve. When
    # it doesn't apply (single node / not requested), we use CADO's auto-clients.
    numa = (_numa_plan(threads, las_threads, log)
            if os.environ.get("CADO_NUMA") else None)
    client_py = os.path.join(cado_build, "cado-nfs-client.py")
    use_numa = bool(numa) and os.path.isfile(client_py)
    if use_numa:
        # server only — clients are launched (pinned) by us, so disable auto-spawn
        base_cmd = ["slaves.nrclients=0" if x.startswith("slaves.nrclients=")
                    else x for x in base_cmd]
        # CADO only auto-whitelists hosts of clients IT launches; with nrclients=0
        # the whitelist stays empty and every one of our manually-launched clients
        # is rejected with HTTP 403 (api_limit_remote_addr), so the sieve collects
        # zero relations and the job times out. Explicitly whitelist localhost.
        base_cmd.append("server.whitelist=localhost")

    def _cado(extra: List[str], label: str) -> Tuple[Optional[Tuple[int, int]], int]:
        if use_numa:
            return _invoke_cado_numa(base_cmd + extra, numa, client_py, cado_build,
                                     workdir, env, n, deadline, label, log)
        return _invoke_cado(base_cmd + extra, env, n, deadline, label, log)

    # GPU offload only pays off once the linear algebra is substantial. Below
    # ~c100 the matrix is tiny, CADO is already fast, and the filter-split +
    # msieve handoff is pure overhead (measured ~+25% wall on c60), so gate the
    # GPU path on input size. The production targets (c130-c145) are well above.
    gpu_min = _env_int("GPU_MIN_DIGITS", 100)
    use_gpu = (bool(shim) and ndigits >= gpu_min and gpu_la.gpu_available(log))

    # idea #3 (sieve/LA rebalance, GPU half): when the fast GPU block-Lanczos is
    # doing the LA, a denser target matrix is cheap to solve AND lets the filter
    # build a matrix from FEWER relations -> less sieving. Only raise density on
    # the GPU path; CADO's CPU bwc prefers its calibrated (lower) density.
    td = os.environ.get("CADO_TARGET_DENSITY", "").strip()
    # OPT-IN (unverified by fast benchmark): auto-raise density only when
    # CADO_TARGET_DENSITY_MULT is explicitly set, and only on the GPU path.
    if (not td and os.environ.get("CADO_TARGET_DENSITY_MULT", "").strip()
            and use_gpu and ndigits >= tune_min):
        base_td = _default_param(ndigits, "tasks.filter.target_density") or 125.0
        try:
            mult = float(os.environ.get("CADO_TARGET_DENSITY_MULT", "1.4") or "1.4")
        except ValueError:
            mult = 1.4
        td = str(round(base_td * mult, 1))
    if td:
        base_cmd.append(f"tasks.filter.target_density={td}")
        tuned.append(f"target_density={td}")

    factors: Optional[Tuple[int, int]] = None
    try:
        log(f"Stage 2: CADO c{ndigits} ({nbits}-bit) threads={threads} "
            f"clients={n_clients} las_threads={las_threads} "
            f"numa={'on(' + str(len(numa)) + ' grp)' if use_numa else 'off'} "
            f"ram_shim={'on' if shim else 'off'} "
            f"gpu_la={'on' if use_gpu else 'off'} "
            f"tune=[{', '.join(tuned) if tuned else 'none'}] budget={remaining}s")

        if use_gpu:
            # Phase 1: sieve + filter only (skip CADO's own linalg/sqrt).
            _, rc = _cado(["tasks.linalg.run=false", "tasks.sqrt.run=false"],
                          "CADO filter")
            if rc == 0:
                # Phase 2: GPU linear algebra + square root on CADO's matrix.
                # CADO names its files "c<digits>" by default; gpu_la discovers
                # the actual prefix from the workdir.
                factors = gpu_la.run_gpu_linalg(
                    workdir, int(n), deadline, shim, sock, env, log)
                if factors:
                    log("Stage 2: GPU linear algebra produced factors")
                else:
                    log("Stage 2: GPU path failed; resuming CADO linear algebra")
            else:
                log("Stage 2: filtering did not finish; resuming full CADO")

        if not factors:
            # CPU path / fallback: full CADO (resumes from the filtered state in
            # the SQLite DB, reusing all relations already in the broker).
            factors, _ = _cado([], "CADO")
    finally:
        if broker:
            _terminate_pid(broker)
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


def _terminate_pid(proc: subprocess.Popen) -> None:
    """Terminate a single process by PID only — never its process group.

    The ramnfs broker shares our process group (it must, or CADO's freerel step
    fails), so killing its *group* would also kill this solver before it can emit
    the solution output. The broker has no children, so a PID-targeted
    SIGTERM->SIGKILL is sufficient and safe."""
    if proc is None or proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            proc.send_signal(sig)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=5)
            return
        except Exception:  # noqa: BLE001
            continue


# --- Pipeline ----------------------------------------------------------------

def factor_semiprime(n_int: int, num_bits: int, deadline: float,
                     log) -> Tuple[Optional[int], Optional[int], str]:
    n = mpz(n_int)
    log(f"Factoring {num_bits}-bit ({len(str(n_int))}-digit) semiprime")
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
