#!/usr/bin/env python3
# GPU linear-algebra handoff for the Breaking-RSA hybrid solver.
#
# After CADO-NFS finishes filtering (purged + history + relations live in the
# ramnfs broker under /ramwork), this module exports CADO's matrix to msieve and
# runs the linear algebra + square root on the GPU (msieve block Lanczos, CUDA).
# This is README.msieve "Section III": reuse CADO's superior filtering, do only
# the LA/sqrt on the GPU.
#
# Every external tool (replay, the savefile builder, msieve) is launched with
# LD_PRELOAD=<shim> and all of its data paths under /ramwork, so the multi-GB
# relation/matrix scratch stays in the broker's RAM, never touching the small
# container /tmp. The GPU runtime files (cub/*.so, lanczos_kernel.ptx) live on
# the real filesystem next to the msieve binary and load normally.
#
# The whole path is best-effort: ANY problem (no GPU, missing file, tool error,
# wrong/short matrix, factors that don't verify) returns None, and the caller
# falls back to CADO's own linear algebra on the same still-alive broker.

import glob
import os
from pathlib import Path
import re
import subprocess
import time
from typing import List, Optional, Tuple

import gmpy2
from gmpy2 import mpz


def gpu_available(log) -> bool:
    """True only if a usable NVIDIA GPU and the msieve GPU runtime are present."""
    if os.environ.get("DISABLE_GPU"):
        log("GPU: disabled via DISABLE_GPU")
        return False
    if not (os.path.exists("/dev/nvidiactl") or glob.glob("/dev/nvidia[0-9]*")):
        log("GPU: no /dev/nvidia* device; using CPU linear algebra")
        return False
    # libcuda is injected by `docker run --gpus`; without it the binary can't run.
    if not any(os.path.exists(p) for p in (
            "/usr/lib/x86_64-linux-gnu/libcuda.so.1", "/usr/lib/libcuda.so.1",
            "/lib/x86_64-linux-gnu/libcuda.so.1")):
        log("GPU: libcuda.so.1 not present (no --gpus?); using CPU linear algebra")
        return False
    msieve = _msieve_bin()
    if not msieve:
        log("GPU: msieve binary not found; using CPU linear algebra")
        return False
    log("GPU: NVIDIA device + msieve runtime detected")
    return True


def _msieve_bin() -> Optional[str]:
    for c in (os.environ.get("MSIEVE_BIN", ""), "/app/msieve/msieve",
              "/opt/msieve/msieve"):
        if c and os.path.isfile(c):
            return c
    return None


def _msieve_dir() -> str:
    """Dir holding msieve + cub/ + lanczos_kernel.ptx (the GPU runtime CWD)."""
    b = _msieve_bin()
    return str(Path(b).parent) if b else "/app/msieve"


def _run(cmd: List[str], env: dict, cwd: str, deadline: float, log,
         tag: str) -> Tuple[int, str]:
    """Run a handoff tool with a deadline; return (rc, combined tail)."""
    timeout = max(5, int(deadline - time.time()))
    try:
        p = subprocess.run(cmd, env=env, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"GPU/{tag}: timed out")
        return 124, ""
    except Exception as e:  # noqa: BLE001
        log(f"GPU/{tag}: launch error: {e}")
        return 1, ""
    out = p.stdout or ""
    if p.returncode != 0:
        for ln in out.strip().splitlines()[-8:]:
            log(f"GPU/{tag}: {ln[:200]}")
    return p.returncode, out


def run_gpu_linalg(workdir: str, n: int, deadline: float,
                   shim: str, sock: str, base_env: dict, log
                   ) -> Optional[Tuple[int, int]]:
    """Run GPU LA+sqrt on CADO's filtered matrix. Return (p, q) or None."""
    msieve = _msieve_bin()
    if not msieve:
        return None
    mdir = _msieve_dir()

    # shim env for tools that touch /ramwork
    senv = dict(base_env)
    senv["LD_PRELOAD"] = shim
    senv["RAMNFS_SOCK"] = sock
    senv["RAMNFS_PREFIX"] = "/ramwork"

    # Discover CADO's file prefix (it names files "c<digits>" by default). We do
    # NOT force a name= on CADO, because that was observed to break CADO's own
    # bwc linear algebra / sqrt on the fallback path.
    name = _discover_name(workdir, senv, mdir, deadline, log)
    if not name:
        log("GPU: could not locate CADO's purged file; skipping GPU path")
        return None

    # CADO owns the "<name>.*" files (read-only to us). msieve writes everything
    # under a DISTINCT prefix so CADO's namespace stays pristine — otherwise a
    # msieve-written "<name>.purged" collides with CADO's "<name>.purged.gz" and
    # also breaks the CADO-resume fallback. ("mq" = msieve job.)
    mname = f"{name}mq"
    purged_gz = os.path.join(workdir, f"{name}.purged.gz")
    history_gz = os.path.join(workdir, f"{name}.history.gz")
    cyc = os.path.join(workdir, f"{mname}.cyc")
    savefile = os.path.join(workdir, mname)             # msieve -s
    fb = os.path.join(workdir, f"{mname}.fb")           # msieve -nf
    worktodo = os.path.join(workdir, f"{mname}.todo")   # msieve -i (holds N)
    mlog = os.path.join(os.environ.get("TMPDIR", "/tmp"), "msieve_la.log")

    # 1. export CADO's matrix to a msieve cycle file
    replay = _find_replay()
    if not replay:
        log("GPU: CADO replay binary not found")
        return None
    rc, _ = _run([replay, "--for_msieve", "skip=0", "--purged", purged_gz,
                  "--his", history_gz, "--out", cyc], senv, mdir, deadline, log,
                 "replay")
    if rc != 0:
        return None

    # 2. build msieve inputs under the shim: factor base (.fb), savefile (raw
    #    relations in purged order) and the .purged index file. Reads CADO's
    #    <name>.* outputs; writes only <mname>.* files.
    builder = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "section3_build.py")
    rc, _ = _run(["python3", builder, workdir, name, mname, str(n)],
                 senv, mdir, deadline, log, "build")
    if rc != 0:
        return None

    # 3. GPU linear algebra
    rc, out = _run([msieve, "-v", "-i", worktodo, "-l", mlog,
                    "-g", os.environ.get("GPU_DEVICE", "0"),
                    "-nf", fb, "-s", savefile, "-nc2", "cado_filter=1"],
                   senv, mdir, deadline, log, "la")
    if rc != 0 or "lanczos halted" not in out:
        log("GPU: linear algebra did not complete")
        return None
    m = re.search(r"BLanczosTime:\s*(\d+)", out)
    if m:
        log(f"GPU: linear algebra done in {m.group(1)}s")

    # 4. square root -> factors
    rc, out = _run([msieve, "-v", "-i", worktodo, "-l", mlog,
                    "-nf", fb, "-s", savefile, "-nc3", "cado_filter=1"],
                   senv, mdir, deadline, log, "sqrt")
    if rc != 0:
        return None
    return _factors_from_msieve(out, n)


def _discover_name(workdir, senv, mdir, deadline, log) -> Optional[str]:
    """Find CADO's file prefix by locating <prefix>.purged.gz under the shim."""
    code = (
        "import glob,os,sys;"
        f"g=sorted(glob.glob(os.path.join({workdir!r},'*.purged.gz')));"
        "sys.stdout.write(os.path.basename(g[0])[:-10] if g else '')"
    )
    rc, out = _run(["python3", "-c", code], senv, mdir, deadline, log, "discover")
    if rc != 0:
        return None
    name = out.strip().splitlines()[-1].strip() if out.strip() else ""
    return name or None


def _find_replay() -> Optional[str]:
    for c in (os.environ.get("CADO_REPLAY", ""),
              "/opt/cado-nfs/build/release/filter/replay"):
        if c and os.path.isfile(c):
            return c
    m = sorted(glob.glob("/opt/cado-nfs/build/*/filter/replay"))
    return m[-1] if m else None


def _factors_from_msieve(out: str, n: int) -> Optional[Tuple[int, int]]:
    """Parse 'pNN factor: D' lines; return the prime pair whose product is N."""
    facs = [int(x) for x in re.findall(r"factor:\s*(\d+)", out)]
    primes = [f for f in facs if 1 < f < n and gmpy2.is_prime(mpz(f))]
    for i in range(len(primes)):
        for j in range(i + 1, len(primes)):
            a, b = primes[i], primes[j]
            if a * b == n:
                return (a, b) if a <= b else (b, a)
    return None
