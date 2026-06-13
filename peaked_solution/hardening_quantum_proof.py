#!/usr/bin/env python3
# Peaked Circuits solver for the Enigma "Hardening Quantum Proof" challenge (SN63).
#
# Recovers the hidden peak bitstring from an OpenQASM peaked circuit, classically,
# on a single GPU. It is a thin orchestrator over the reference implementation of
#
#     Kremer & Dupuis, "Efficient Classical Simulation of Heuristic Peaked
#     Quantum Circuits", arXiv:2604.21908
#     https://github.com/d-kremer/peaked-circuit-simulation   (Apache-2.0)
#
# The heavy lifting lives in the VERBATIM vendored modules utils.py, unswap.py and
# circuit_mpo.py (see NOTICE / LICENSE-Apache-2.0). This file only (a) locates and
# parses the QASM, (b) chains the reference drivers exactly as their notebooks do,
# (c) verifies/refines candidates with the amplitude oracle, and (d) emits the
# answer through the Enigma stdout protocol.
#
# Pipeline (see the project plan):
#   Stage 0  Locate + parse QASM, structural fingerprint
#   Stage 1  Distillation: low-bond CircuitPermMPS + per-bit majority vote
#               (peaked-circuit-distillation.ipynb)
#   Stage 2  Mirror unswapping: MPO iterative cancellation + unswapping
#               (peaked-circuit-unswapping.ipynb)  -- the heavy hammer
#   Stage 3  TNO contraction from the centre + marginal extraction
#               (peaked-circuit-tno.ipynb)         -- for permutation-free circuits
#   Stage 4  Amplitude oracle (|<s|C|0>|^2) + greedy hill-climb to verify/correct
#   Stage 5  Confidence gate -> emit
#
# Convention (matches the reference, which validates against BlueQubit's true_bs):
#   the output bitstring is indexed so that character i corresponds to qubit i
#   (Qiskit qubit index i, qubit 0 leftmost). All three methods are normalised to
#   this convention here.
#
# Input is discovered robustly (argv path, the docker_runner mount at
# /app/peaked-circuit.qasm, a /challenge_input/ dir, or any *.qasm nearby).
#
# Output: result.json carries the peak under SEVERAL alias keys; Serde.from_dict
# on the validator ignores keys it does not expect, so a superset is safe. Confirm
# the canonical key from the official peaked-circuit challenge README at launch and,
# if it differs, set PEAKED_RESULT_KEY (it is always added as an alias regardless).

from __future__ import annotations

import glob
import json
import os
import signal
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from _hqp_output import build_solution_zip, write_solution_output
from _hqp_types import Solution, Problem, load_solver_input


# --------------------------------------------------------------------------- #
#  Logging
# --------------------------------------------------------------------------- #

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
#  Hardware-aware auto-scaling
#
#  The SUBMITTED image runs with these defaults (the validator passes no -e
#  flags), so we scale bond dimensions to the detected VRAM at startup: a bigger
#  card gets bigger bonds -> more GPU utilisation AND sharper marginals/oracle.
#  Everything stays guarded by the existing time/OOM fallbacks, and an explicit
#  env var always overrides the auto value (see auto_int).
# --------------------------------------------------------------------------- #

AUTO = {
    "DISTILL_MAX_BOND": 128, "VERIFY_MAX_BOND": 1024, "TNO_MAX_BOND": 16,
    "TNE_MAX_BOND": 8, "UNSWAP_MAX_BOND": 8192, "VERIFY_QUBIT_CAP": 56,
}


def auto_int(name: str) -> int:
    """Explicit env override if set, else the VRAM-auto-scaled default."""
    return env_int(name, AUTO[name])


def cpu_count() -> int:
    """Cores available to THIS container, honoring docker --cpus (cgroup quota),
    which os.cpu_count() ignores (it reports host cores)."""
    n = os.cpu_count() or 8
    try:  # cgroup v2
        parts = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if parts and parts[0] != "max":
            return max(1, min(n, int(parts[0]) // int(parts[1])))
    except (OSError, ValueError, IndexError):
        pass
    try:  # cgroup v1
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            return max(1, min(n, quota // period))
    except (OSError, ValueError):
        pass
    return n


def set_thread_env(cores: int) -> None:
    """Pin BLAS/OpenMP pools to the core count. Must run before numpy/torch are
    imported to take effect, so call it first thing in main()."""
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, str(max(1, cores)))


def configure_resources(device, cores: int) -> None:
    """Populate AUTO bond defaults from the detected GPU VRAM and pin torch threads."""
    import torch
    vram_gb = 0.0
    if str(device).startswith("cuda"):
        try:
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        except Exception:  # noqa: BLE001
            vram_gb = 0.0
    # DISTILL/VERIFY (and the oracle cap) are the accuracy levers -> scale with VRAM.
    # TNO_MAX_BOND is kept modest on purpose: TNO blow-up is driven by link count,
    # not bond size, so a bigger TNO bond just diverges faster on dense circuits.
    if vram_gb >= 80:        # RTX PRO 6000 96GB / A100 80GB
        tier = dict(DISTILL_MAX_BOND=1024, VERIFY_MAX_BOND=4096, TNO_MAX_BOND=24,
                    TNE_MAX_BOND=10, UNSWAP_MAX_BOND=8192, VERIFY_QUBIT_CAP=64)
    elif vram_gb >= 40:      # A6000 48GB / A100 40GB
        tier = dict(DISTILL_MAX_BOND=512, VERIFY_MAX_BOND=2048, TNO_MAX_BOND=20,
                    TNE_MAX_BOND=8, UNSWAP_MAX_BOND=8192, VERIFY_QUBIT_CAP=56)
    elif vram_gb >= 20:      # 24 GB cards
        tier = dict(DISTILL_MAX_BOND=256, VERIFY_MAX_BOND=1024, TNO_MAX_BOND=16,
                    TNE_MAX_BOND=8, UNSWAP_MAX_BOND=6144, VERIFY_QUBIT_CAP=50)
    else:                    # small GPU / CPU
        tier = dict(DISTILL_MAX_BOND=128, VERIFY_MAX_BOND=512, TNO_MAX_BOND=16,
                    TNE_MAX_BOND=8, UNSWAP_MAX_BOND=4096, VERIFY_QUBIT_CAP=44)
    AUTO.update(tier)
    try:
        torch.set_num_threads(max(1, cores))
    except Exception:  # noqa: BLE001
        pass
    log(f"Auto-scaled to {vram_gb:.0f} GB VRAM / {cores} cores -> "
        f"DISTILL={AUTO['DISTILL_MAX_BOND']} VERIFY={AUTO['VERIFY_MAX_BOND']} "
        f"TNO={AUTO['TNO_MAX_BOND']} UNSWAP={AUTO['UNSWAP_MAX_BOND']} "
        f"VERIFY_CAP={AUTO['VERIFY_QUBIT_CAP']} (env vars override)")


# --------------------------------------------------------------------------- #
#  Deadline-bounded execution via SIGALRM
#
#  mpo_compress_unswap() catches KeyboardInterrupt internally and returns the
#  partial MPO, so raising KeyboardInterrupt from an alarm makes the heavy stage
#  deadline-safe. For stages that do not catch it, the budget() context still
#  unwinds cleanly and we fall through to whatever candidates we already have.
# --------------------------------------------------------------------------- #

class _Budget:
    def __init__(self, seconds: float, repeat: float = 5.0):
        self.seconds = max(1.0, float(seconds))
        self.repeat = max(1.0, float(repeat))
        self._old = None

    def __enter__(self):
        def _raise(signum, frame):
            raise KeyboardInterrupt(f"budget of {self.seconds:.0f}s elapsed")
        self._old = signal.signal(signal.SIGALRM, _raise)
        # PERIODIC, not one-shot. A heavy stage like mpo_compress_unswap catches the
        # first KeyboardInterrupt internally (to return its partial MPO) -- with a
        # one-shot alarm that CONSUMES the deadline, and the next phase (mpo_to_mps
        # reconstruction) then runs unbounded, blowing past WALL_TIME by hours. By
        # re-firing every `repeat`s the deadline survives a caught interrupt and stops
        # the very next long op, bounding the overrun to ~one tensor contraction.
        signal.setitimer(signal.ITIMER_REAL, self.seconds, self.repeat)
        return self

    def __exit__(self, exc_type, exc, tb):
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        if self._old is not None:
            signal.signal(signal.SIGALRM, self._old)
        # Do NOT suppress: let the timeout KeyboardInterrupt propagate to the
        # stage's own `except KeyboardInterrupt` handler. Suppressing it here would
        # let execution fall through into code that assumes the stage finished
        # (e.g. a verifier `psi` that was never assigned).
        return False


def budget(seconds: float, repeat: float = 5.0) -> _Budget:
    return _Budget(seconds, repeat=repeat)


# --------------------------------------------------------------------------- #
#  GPU setup + loud guard
# --------------------------------------------------------------------------- #

def gpu_self_test(dev) -> None:
    """Run matmul + SVD in complex64 & complex128 to (a) verify the torch build
    actually has kernels for this GPU's architecture and (b) prime cuBLAS/cuSOLVER.

    On a Blackwell card (RTX PRO 6000, compute 12.0 / sm_120) a torch build without
    matching kernels raises 'no kernel image is available for execution on the
    device' on the first real op -- this surfaces that immediately with a clear
    message instead of mid-solve."""
    import torch
    for dt in (torch.complex128, torch.complex64):
        a = torch.randn(96, 96, dtype=dt, device=dev)
        _ = (a @ a.conj().transpose(-1, -2)).abs().sum().item()
        _ = torch.linalg.svd(a, full_matrices=False)
    if str(dev).startswith("cuda"):
        torch.cuda.synchronize()


def setup_device():
    """Pick the compute device. GPU-first, but NEVER crash on a device problem:
    on any GPU failure we fall back to CPU (loudly) so the solver still produces
    output. On the validator's working RTX PRO 6000 the self-test passes and we
    use the GPU."""
    import torch
    allow_cpu = os.environ.get("ALLOW_CPU", "").strip() not in ("", "0", "false", "False")

    if not torch.cuda.is_available():
        log("!!! NO CUDA GPU VISIBLE -- falling back to CPU. This is GPU-first; CPU will not "
            "finish non-trivial circuits in time, but it will still emit a result.")
        return "cpu"

    dev = os.environ.get("PEAKED_DEVICE", "cuda:0")
    try:
        name = torch.cuda.get_device_name(0)
        cap = torch.cuda.get_device_capability(0)
        log(f"GPU: {name} | compute {cap[0]}.{cap[1]} | torch {torch.__version__} "
            f"(CUDA {torch.version.cuda}) | device={dev}")
    except Exception as e:  # noqa: BLE001
        log(f"GPU info query failed: {e}")

    try:
        gpu_self_test(dev)
        log("GPU self-test passed (matmul + SVD, complex64 & complex128).")
        return dev
    except Exception as e:  # noqa: BLE001
        log(f"!!! GPU SELF-TEST FAILED: {e}")
        log("    The torch build may lack kernels for this GPU architecture. For a Blackwell "
            "RTX PRO 6000 (sm_120) the image needs a CUDA 12.8+/13 torch wheel (cu130).")
        if allow_cpu or os.environ.get("PEAKED_GPU_FALLBACK_CPU", "1").strip() not in ("0", "false", "False"):
            log("    Falling back to CPU so a result is still produced (set "
                "PEAKED_GPU_FALLBACK_CPU=0 to fail instead).")
            return "cpu"
        raise


def install_robust_svd():
    """Make torch.linalg.svd robust for FP32/complex64 on CUDA.

    cuSOLVER's default Jacobi SVD ('gesvdj') fails to converge on the
    ill-conditioned, repeated-singular-value matrices that arise in MPO/MPS
    compression at single precision (LinAlgError "error code: 4"). We route
    FP32/complex64 CUDA SVDs through the QR-based 'gesvd' driver, and on any
    residual failure upcast *just that one SVD* to FP64. complex128 keeps the
    default fast path untouched. This lets the unswapping run in complex64 -- far
    faster on low-FP64 GPUs (A6000 / workstation cards) -- without the crash.

    Patching torch.linalg.svd is sufficient because quimb's svd_truncated calls
    `xp.linalg.svd(x)` (decomp.py:814), so the vendored modules stay verbatim.
    """
    import torch
    if getattr(torch.linalg, "_peaked_robust_svd", False):
        return
    orig = torch.linalg.svd
    up = {torch.complex64: torch.complex128, torch.float32: torch.float64}
    real = {torch.complex64: torch.float32, torch.float32: torch.float32,
            torch.complex128: torch.float64, torch.float64: torch.float64}

    stats = {"gpu32": 0, "gpu64": 0, "cpu": 0, "nonfinite": 0}

    def robust(A, full_matrices=True, *, driver=None):
        on_cuda = getattr(A, "is_cuda", False)
        fp32 = A.dtype in up
        # 1) fast GPU path (gesvd for FP32 -- robust vs the default Jacobi driver)
        try:
            if on_cuda and fp32:
                out = orig(A, full_matrices=full_matrices, driver="gesvd"); stats["gpu32"] += 1; return out
            return orig(A, full_matrices=full_matrices, driver=driver)
        except Exception:
            pass
        # 2) GPU FP64 upcast (handles ill-conditioning when the matrix is finite)
        try:
            if on_cuda:
                tgt = up.get(A.dtype, A.dtype)
                U, s, Vh = orig(A.to(tgt), full_matrices=full_matrices, driver="gesvd")
                stats["gpu64"] += 1
                return U.to(A.dtype), s.to(real.get(A.dtype, torch.float64)), Vh.to(A.dtype)
        except Exception:
            pass
        # 3) CPU FP64 LAPACK -- bulletproof for finite matrices (no cuSOLVER quirks)
        finite = bool(torch.isfinite(A).all().item())
        if not finite:
            stats["nonfinite"] += 1
            if stats["nonfinite"] <= 3:
                log(f"  [robust_svd] NON-FINITE matrix into SVD shape={tuple(A.shape)} "
                    f"dtype={A.dtype} -> complex64 overflow/NaN (sanitizing)")
        tgt = up.get(A.dtype, A.dtype)
        A2 = torch.nan_to_num(A).to("cpu", tgt)
        U, s, Vh = orig(A2, full_matrices=full_matrices)
        stats["cpu"] += 1
        dev = A.device
        return (U.to(dev, A.dtype), s.to(dev, real.get(A.dtype, torch.float64)), Vh.to(dev, A.dtype))

    torch.linalg.svd = robust
    torch.linalg._peaked_robust_svd_stats = stats
    torch.linalg._peaked_robust_svd = True
    log("Robust SVD installed (FP32 -> gesvd, FP64 fallback on failure).")


# --------------------------------------------------------------------------- #
#  Stage 0: locate + parse QASM, fingerprint
# --------------------------------------------------------------------------- #

CHALLENGE_INPUT_DIR = "/challenge_input"
DOCKER_QASM_MOUNT = "/app/peaked-circuit.qasm"


def _derive_id(path: str) -> str:
    return Path(path).stem or "circuit"


def locate_qasm() -> tuple[str, str]:
    """Return (qasm_path, circuit_id), searching the plausible input locations."""
    # 1) explicit argv path
    for a in sys.argv[1:]:
        a = a.strip()
        if a.lower().endswith(".qasm") and os.path.isfile(a):
            return a, _derive_id(a)

    # 2) docker_runner.py mount
    if os.path.isfile(DOCKER_QASM_MOUNT):
        cid = (sys.argv[1].strip() if len(sys.argv) > 1 and not sys.argv[1].endswith(".qasm") else "") \
            or _derive_id(DOCKER_QASM_MOUNT)
        return DOCKER_QASM_MOUNT, cid

    # 3) /challenge_input: a .qasm file, or a challenge_input.json describing it
    if os.path.isdir(CHALLENGE_INPUT_DIR):
        qs = sorted(glob.glob(os.path.join(CHALLENGE_INPUT_DIR, "*.qasm")))
        if qs:
            return qs[0], _derive_id(qs[0])
        cij = os.path.join(CHALLENGE_INPUT_DIR, "challenge_input.json")
        if os.path.isfile(cij):
            data = json.loads(Path(cij).read_text())
            cid = str(data.get("circuit_id") or data.get("id") or data.get("challenge_id") or "circuit")
            # inline QASM text under a key, or a path to a file
            for k in ("qasm", "circuit", "openqasm", "qasm_str"):
                if isinstance(data.get(k), str) and "qreg" in data[k] or (isinstance(data.get(k), str) and "OPENQASM" in str(data.get(k))):
                    tmp = os.path.join(os.environ.get("TMPDIR", "/tmp"), "challenge.qasm")
                    Path(tmp).write_text(data[k])
                    return tmp, cid
            for k in ("qasm_path", "path", "file"):
                if isinstance(data.get(k), str) and os.path.isfile(data[k]):
                    return data[k], cid

    # 4) any *.qasm under /app or cwd
    for root in ("/app", "."):
        qs = sorted(glob.glob(os.path.join(root, "*.qasm")))
        if qs:
            return qs[0], _derive_id(qs[0])

    raise SystemExit("No QASM input found (looked at argv, /app/peaked-circuit.qasm, "
                     "/challenge_input/, and *.qasm in /app and CWD).")


def load_circuit(path: str):
    """Robust OpenQASM loader (qasm2 -> qasm3 -> legacy from_qasm_file)."""
    from qiskit import QuantumCircuit
    errs = []
    try:
        from qiskit import qasm2
        return qasm2.load(path, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS,
                          custom_classical=qasm2.LEGACY_CUSTOM_CLASSICAL, strict=False)
    except Exception as e:  # noqa: BLE001
        errs.append(f"qasm2: {e}")
    try:
        from qiskit import qasm3
        return qasm3.load(path)  # needs qiskit-qasm3-import
    except Exception as e:  # noqa: BLE001
        errs.append(f"qasm3: {e}")
    try:
        from qiskit.qasm3 import load_experimental  # native Rust parser, no extra dep
        return load_experimental(path)
    except Exception as e:  # noqa: BLE001
        errs.append(f"qasm3-exp: {e}")
    try:
        return QuantumCircuit.from_qasm_file(path)
    except Exception as e:  # noqa: BLE001
        errs.append(f"legacy: {e}")
    raise RuntimeError("Could not parse QASM. Tried:\n  " + "\n  ".join(errs))


def fingerprint(circuit) -> dict:
    ops = dict(circuit.count_ops())
    n = circuit.num_qubits
    twoq = 0
    degree = [set() for _ in range(n)]
    for inst in circuit.data:
        qs = [circuit.find_bit(q).index for q in inst.qubits]
        if len(qs) == 2:
            twoq += 1
            degree[qs[0]].add(qs[1])
            degree[qs[1]].add(qs[0])
    avg_deg = (sum(len(d) for d in degree) / n) if n else 0.0
    has_swap = ops.get("swap", 0) > 0
    info = {
        "num_qubits": n,
        "depth": circuit.depth(),
        "ops": ops,
        "two_qubit_gates": twoq,
        "avg_degree": round(avg_deg, 2),
        "has_swap": has_swap,
        "all_to_all_ish": avg_deg > 0.5 * (n - 1) if n > 1 else False,
    }
    return info


# --------------------------------------------------------------------------- #
#  Convention helpers
# --------------------------------------------------------------------------- #

def flip(bs: str, i: int) -> str:
    return bs[:i] + ("1" if bs[i] == "0" else "0") + bs[i + 1:]


def strip_swaps(circuit):
    """Decompose literal SWAP gates to CX for the MPS-based stages.

    quimb 1.13.0's CircuitPermMPS/CircuitMPS route a literal SWAP through
    swap_sites_with_compress_, which forwards a `swap_back` kwarg into
    svd_truncated and raises TypeError. Surgically decomposing only SWAPs (3 CX
    each) sidesteps it and leaves every other gate untouched. The unswapping
    stage is unaffected (it consolidates 2q blocks into `unitary`)."""
    if circuit.count_ops().get("swap", 0) == 0:
        return circuit
    return circuit.decompose(gates_to_decompose=["swap"])


# --------------------------------------------------------------------------- #
#  Stage 1: distillation (low-bond MPS + per-bit majority vote)
#  Faithful to peaked-circuit-distillation.ipynb.
# --------------------------------------------------------------------------- #

def stage_distillation(circuit, device, max_bond=128, shots=1000, seed=1234):
    import numpy as np
    import quimb
    import torch
    from qiskit_quimb import quimb_circuit

    def to_backend(x):
        return torch.tensor(x, dtype=torch.complex64, device=device)

    circuit = strip_swaps(circuit)
    qc = quimb_circuit(
        circuit,
        quimb_circuit_class=quimb.tensor.CircuitPermMPS,  # tolerant of long-range gates
        to_backend=to_backend,
        max_bond=max_bond,
        cutoff=1e-12,
        progbar=False,
    )
    # Map permuted MPS qubit order back to logical qubit order (notebook recipe).
    qubit_mapping = [qc.qubits.index(q) for q in range(qc.N)]
    qubit_mapping = [qubit_mapping[q] for q in qubit_mapping]
    samples = ["".join(bs[q] for q in qubit_mapping) for bs in qc.sample(shots, seed=seed)]
    csamples = Counter(samples)
    bit_probs = np.array([[int(s) for s in ss] for ss in samples]).mean(axis=0)
    voted = "".join(str(i) for i in (bit_probs > 0.5).astype(int).tolist())
    most_common, mc_count = csamples.most_common(1)[0]
    # decisiveness of the per-bit vote = how close every bit is to 0/1
    decisiveness = float(np.min(np.maximum(bit_probs, 1.0 - bit_probs)))
    return {
        "candidates": [voted, most_common],
        "voted": voted,
        "most_common": most_common,
        "mc_freq": mc_count / shots,
        "decisiveness": decisiveness,
        "counter": csamples,
        "psi": qc.psi,   # the built MPS, reused by Stage 4's amplitude hill-climb
    }


# --------------------------------------------------------------------------- #
#  Stage 2: mirror unswapping (the heavy hammer)
#  Faithful to peaked-circuit-unswapping.ipynb.
# --------------------------------------------------------------------------- #

def install_parallel_unswap() -> bool:
    """OPT-IN: replace ``unswap.unswap`` with a probe-parallel variant.

    The reference inner loop evaluates the 6 ``(how, parity)`` candidate-swap
    probes one at a time, each a separate MPO compression. On a big box this runs
    the GPU at ~6% utilisation (latency/overhead-bound, not throughput-bound), so
    the 24 cores + 96 GB card sit mostly idle. This variant fans the 6 probes out
    across a thread pool: each ``get_good_swaps`` releases the GIL inside torch/
    numpy SVDs, so their kernel launches overlap and the box actually gets used.

    SEMANTIC NOTE: probes are evaluated against the iteration-START MPO (then the
    selected swaps are applied greedily in the original order), whereas the
    reference re-evaluates each probe against the MPO already mutated by earlier
    probes in the same iteration. The recovered bitstring can therefore differ.
    This is why it is OFF by default and gated on UNSWAP_PARALLEL_PROBES -- A/B it
    on the GPU (same circuit, compare answer + wall-time) before trusting it on the
    validator. Returns True if the patch was installed.
    """
    if os.environ.get("UNSWAP_PARALLEL_PROBES", "").strip() in ("", "0", "false", "False"):
        return False
    import unswap as _u
    from concurrent.futures import ThreadPoolExecutor

    if getattr(_u.unswap, "_is_parallel_probe", False):
        return True
    _orig = _u.unswap
    n_workers = max(2, env_int("UNSWAP_PROBE_WORKERS", min(6, os.cpu_count() or 6)))

    def parallel_unswap(mpo, hows=("left", "right", "both"), max_bond=2048,
                        cutoff=0.0001, max_its=25, equal=False, to_backend=None, t0=0):
        num_qubits = len(mpo.sites)
        all_pairs = [(i, i + 1) for i in range(num_qubits - 1)]
        perm_left = list(range(num_qubits))
        perm_right = list(range(num_qubits))
        logging_ = _u.logging
        logging_.info("    [start unswap||] -> " + str(_u.get_tn_info(mpo)))
        num_improvements, start_counts, end_counts, ii = 1, 1, 0, 0
        stats_data = []
        jobs = [(how, parity) for how in hows for parity in (0, 1)]
        while num_improvements > 0 and ii < max_its and start_counts != end_counts:
            num_improvements = 0
            start_counts = _u.elem_counts(mpo)
            mpo_snapshot = mpo  # probes read this snapshot concurrently (each .copy()s)

            def _probe(job):
                how, parity = job
                ids = _u.get_good_swaps(
                    mpo_snapshot, qubit_pairs=all_pairs[parity::2], how=how,
                    max_bond=max_bond, cutoff=cutoff, to_backend=to_backend, equal=equal)
                return job, ids

            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                probed = dict(ex.map(_probe, jobs))

            # Apply greedily in the original order so perms/MPO update deterministically.
            for how in hows:
                for parity in (0, 1):
                    new_swap_ids = probed[(how, parity)]
                    new_swaps = [all_pairs[i] for i in new_swap_ids if i % 2 == parity]
                    swaps_l = new_swaps if how in ("left", "both") else []
                    swaps_r = new_swaps if how in ("right", "both") else []
                    mpo = _u.apply_swaps(mpo, swaps_l=swaps_l, swaps_r=swaps_r,
                                         max_bond=max_bond, cutoff=cutoff, to_backend=to_backend)
                    if how in ("left", "both"):
                        perm_left = _u.swap_perm(perm_left, new_swaps)
                    if how in ("right", "both"):
                        perm_right = _u.swap_perm(perm_right, new_swaps)
                    num_improvements += len(new_swap_ids)
                    stats_data.append({"time": _u.time.perf_counter() - t0, "stage": "unswapping",
                                       "iteration": ii, "side": how, "parity": parity,
                                       "new_swaps": len(new_swap_ids), "total_swaps": num_improvements,
                                       **_u.get_tn_info(mpo)})
            end_counts = _u.elem_counts(mpo)
            ii += 1
        logging_.info("    [end unswap||] -> " + str(_u.get_tn_info(mpo)))
        return mpo, (perm_left, perm_right), stats_data

    parallel_unswap._is_parallel_probe = True
    _u.unswap = parallel_unswap
    log(f"  [perf] EXPERIMENTAL probe-parallel unswap installed "
        f"({n_workers} workers); A/B-validate on GPU before trusting on the validator")
    return True


def stage_unswapping(circuit, device, seed=123, cutoff=0.002, max_bond=8192,
                     unswap_threshold=1e6, max_its=20, shots=1000, center_ratio=0.5):
    import torch
    from qiskit.transpiler import PassManager
    from qiskit.transpiler.passes import Collect2qBlocks, ConsolidateBlocks
    import utils
    import unswap
    from unswap import mpo_compress_unswap, mpo_to_mps

    utils.DEVICE = device

    # Runtime override of SabreSwap trials, applied to the symbol in unswap's
    # namespace so the vendored file stays byte-identical. The reference hardcodes
    # trials=10000 in rewire_layers, which is CPU-bound and dominates wall-time on
    # few-core machines; ~200 is ~50x faster with negligible quality loss. Default
    # (env unset / 0) preserves the verbatim trials=10000.
    sabre_trials = env_int("SABRE_TRIALS", 0)
    if sabre_trials > 0:
        from qiskit.transpiler.passes import SabreSwap as _RealSabreSwap

        def _sabre(*a, **k):
            k["trials"] = sabre_trials
            return _RealSabreSwap(*a, **k)
        unswap.SabreSwap = _sabre
        log(f"  [perf] SabreSwap trials overridden -> {sabre_trials}")

    # OPT-IN: fan the per-iteration swap probes across cores so the box gets used
    # (default off -> reference-identical sequential probing). See install_parallel_unswap.
    install_parallel_unswap()

    # Precision. The reference uses complex128 and it is REQUIRED for large
    # circuits: complex64's limited range overflows to Inf/NaN inside quimb's
    # compression sweep, after which truncation collapses the MPO to bond 1 (a
    # trivial product state -> wrong answer). This was confirmed empirically on
    # peaked_circuit_P9_Hqap_56x1917 (overflow at ~5 gates, then permanent bond-1
    # collapse). complex64 is therefore an EXPERIMENTAL opt-in, safe only for small
    # circuits. (Profiling also showed the unswapping is overhead/latency-bound at
    # ~6% GPU utilisation, not FP64-throughput-bound, so FP32 would not even speed
    # it up.)
    dtype = torch.complex64 if os.environ.get("UNSWAP_DTYPE") == "complex64" else torch.complex128
    if dtype == torch.complex64:
        log("  [WARN] UNSWAP_DTYPE=complex64 is EXPERIMENTAL and numerically unstable on "
            "large/deep circuits (FP32 overflow -> MPO collapse -> wrong answer). "
            "Use only for small circuits; default complex128 is correct.")

    def to_backend(x):
        return torch.tensor(x, dtype=dtype, device=device)

    collect_2q = PassManager([Collect2qBlocks(), ConsolidateBlocks(force_consolidate=True)])
    circ = collect_2q.run(circuit)

    # `hows` controls the unswap directions tried each iteration (3 dirs x 2
    # parities = 6 sweeps). Cutting it is the main lever to reduce the early-phase
    # operation count (the unswap cycles are ~70% of wall time and are
    # overhead-bound, not compute-bound). Default matches the reference.
    hows = tuple(h.strip() for h in os.environ.get("UNSWAP_HOWS", "both,left,right").split(",") if h.strip())
    mpo, layers_left, layers_right, _stats = mpo_compress_unswap(
        circ, seed=seed, to_backend=to_backend,
        cutoff=cutoff, max_bond=max_bond,
        unswap_threshold=unswap_threshold, center_ratio=center_ratio, equal=False,
        flip_freq=None, max_its=max_its, early_stopping_gates=0,
        hows=hows,
    )
    mps, perm = mpo_to_mps(mpo, layers_left[:-2], layers_right, cutoff=cutoff, to_backend=to_backend)

    raw_samples = [p for p, _ in list(mps.sample(shots))]
    samples = ["".join(str(b) for b in bs) for bs in raw_samples]
    cs = Counter(samples)
    # candidates in logical-qubit order: perm-map each frequent raw sample
    cands = []
    for raw_bs, _c in cs.most_common(8):
        cands.append("".join(raw_bs[i] for i in perm))
    pred = cands[0] if cands else None
    return {
        "candidates": cands,
        "pred": pred,
        "perm": perm,
        "mps": mps,           # MPS in RAW (pre-perm) site order
        "counter": cs,
        "top_freq": (cs.most_common(1)[0][1] / shots) if cs else 0.0,
    }


# --------------------------------------------------------------------------- #
#  Stage 3: TNO contraction + marginal extraction (permutation-free circuits)
#  Faithful to peaked-circuit-tno.ipynb.
# --------------------------------------------------------------------------- #

def stage_tno(circuit, device, chunk_size=2, cutoff=0.01, max_bond=16, tne_max_bond=8):
    import torch
    import utils
    from utils import contract_core, tno_to_tne, extract_bitstring, iter_layers

    utils.DEVICE = device

    # NOTE: extract_bitstring() builds its Z-projector as cfloat (complex64), and
    # torch 2.12 refuses to contract complex64 with complex128, so the whole TNO
    # path must run in complex64 to match it.
    def to_backend(x):
        return torch.tensor(x, dtype=torch.complex64, device=device)

    layers = list(iter_layers(circuit))
    tno, _ = contract_core(layers, chunk_size=chunk_size, cutoff=cutoff,
                           max_bond=max_bond, to_backend=to_backend)
    tne = tno_to_tne(tno, max_bond=tne_max_bond, cutoff=cutoff, to_backend=to_backend)
    pred, marginals = extract_bitstring(tne)  # string index i == qubit i
    return {"candidates": [pred], "pred": pred, "marginals": marginals, "tne": tne}


# --------------------------------------------------------------------------- #
#  Stage 4: amplitude oracle (|<s|C|0>|^2) + greedy hill-climb
#
#  We build an independent verification MPS of the FULL circuit in logical-qubit
#  order (CircuitMPS keeps site == qubit) and score candidates with the vendored
#  utils.bitstring_probability. This both ranks candidates on one footing and
#  corrects single-bit marginal errors by hill-climbing. Guarded by qubit count
#  and a time budget; for circuits too entangled to build a faithful MPS we skip
#  it and trust the heavy method's native, self-consistent output.
# --------------------------------------------------------------------------- #

def build_verifier_psi(circuit, device, max_bond, cutoff=1e-10):
    import quimb
    import torch
    from qiskit_quimb import quimb_circuit

    def to_backend(x):
        return torch.tensor(x, dtype=torch.complex64, device=device)

    circuit = strip_swaps(circuit)
    qc = quimb_circuit(
        circuit,
        quimb_circuit_class=quimb.tensor.CircuitMPS,  # site i == qubit i (no permutation)
        to_backend=to_backend,
        max_bond=max_bond,
        cutoff=cutoff,
        progbar=False,
    )
    return qc.psi


def build_bp_oracle(circuit, device, tol=1e-6, damping=0.1):
    """Idea #4: a belief-propagation verifier for circuits the 1D MPS attack
    cannot reach.

    Builds the FULL circuit tensor network (general geometry, NOT an MPS) and
    contracts a projected amplitude <s|C|0> with dense 2-norm BP. BP does not
    assume 1D structure, so it stays meaningful on all-to-all / native-obfuscation
    circuits where unswapping diverges and the distillation MPS is pure noise.

    Returns ``(score_fn, seed_or_None)``:
      * ``score_fn(bitstring) -> float`` approximates |<s|C|0>|^2 via BP, drop-in
        for `beam_search`'s `score_fn` -- an INDEPENDENT second oracle.
      * ``seed`` is a bitstring read off the BP single-qubit marginals (best effort).

    Fully fault-tolerant: ANY failure (import, build, non-convergence, API drift)
    returns ``(None, None)`` and the pipeline continues on the banked answer.
    """
    try:
        import quimb
        import torch
        import numpy as _np
        from qiskit_quimb import quimb_circuit
        from quimb.tensor.belief_propagation import contract_d2bp
    except Exception as e:  # noqa: BLE001
        log(f"  BP oracle unavailable (import): {e}")
        return None, None

    max_it = env_int("BP_MAX_ITERS", 200)

    def to_backend(x):
        return torch.tensor(x, dtype=torch.complex64, device=device)

    try:
        circ = strip_swaps(circuit)
        qc = quimb_circuit(circ, quimb_circuit_class=quimb.tensor.Circuit,
                           to_backend=to_backend)
        psi = qc.psi  # full state TN with one open index per qubit
    except Exception as e:  # noqa: BLE001
        log(f"  BP oracle build failed: {e}")
        return None, None

    def _scalar(val):
        try:
            return float(abs(val))
        except TypeError:                         # (mantissa, exponent) form
            m, e = val
            return float(abs(m)) * (10.0 ** float(e))

    def score_fn(bitstring):
        tn = psi.isel({psi.site_ind(i): int(b) for i, b in zip(psi.sites, bitstring)})
        val = contract_d2bp(tn, max_iterations=max_it, tol=tol,
                            damping=damping, progbar=False)
        return _scalar(val)

    # Best-effort marginal seed: one BP run on <psi|psi> -> per-qubit P(0) vs P(1).
    seed = None
    try:
        from quimb.tensor.belief_propagation import D2BP
        bp = D2BP((psi.H | psi), damping=damping)
        runner = getattr(bp, "run", None) or getattr(bp, "run_iterations")
        runner(max_iterations=max_it, tol=tol)
        bits = []
        for i in psi.sites:
            m = bp.compute_marginal(psi.site_ind(i))
            arr = _np.asarray(getattr(m, "data", m)).real.ravel()
            bits.append("0" if arr[0] >= arr[-1] else "1")
        seed = "".join(bits)
    except Exception as e:  # noqa: BLE001
        log(f"  BP marginal seed skipped: {e}")

    return score_fn, seed


def hill_climb(psi, bs, deadline, max_passes=12):
    from utils import bitstring_probability
    best = bs
    best_p = float(bitstring_probability(psi, best))
    for _ in range(max_passes):
        if time.time() > deadline:
            break
        improved = False
        # pick the single best-improving flip this pass (steepest ascent)
        cand_best, cand_best_p = best, best_p
        for i in range(len(best)):
            if time.time() > deadline:
                break
            c = flip(best, i)
            p = float(bitstring_probability(psi, c))
            if p > cand_best_p:
                cand_best, cand_best_p = c, p
        if cand_best_p > best_p:
            best, best_p, improved = cand_best, cand_best_p, True
        if not improved:
            break
    return best, best_p


def beam_search(psi, seeds, deadline, width=None, max_passes=10, score_fn=None):
    """Beam search over a scalar score s -> score(s), keeping the top-`width` strings.

    Idea #2: single-bit steepest-ascent hill-climb (the special case width=1)
    gets trapped in local optima -- e.g. it left d2_s1 three bits short of the
    peak. A beam keeps the `width` best candidates and, each pass, expands EVERY
    member by all single-bit flips, dedupes, and keeps the best `width`. This
    explores several basins at once and walks past the 1- and 2-bit traps that
    stop greedy. Scores are memoised so each distinct string is scored once.

    `score_fn(bitstring) -> float` defaults to the exact MPS amplitude
    |<s|psi>|^2; the BP oracle (Idea #4) passes a belief-propagation amplitude
    instead, so the SAME search drives both verifiers. Returns (best, best_score).
    """
    width = max(1, width if width is not None else env_int("BEAM_WIDTH", 16))
    if score_fn is None:
        from utils import bitstring_probability
        score_fn = lambda b: float(bitstring_probability(psi, b))  # noqa: E731
    cache: dict[str, float] = {}

    def amp(b):
        v = cache.get(b)
        if v is None:
            v = float(score_fn(b))
            cache[b] = v
        return v

    beam = sorted(dict.fromkeys(s for s in seeds if s), key=amp, reverse=True)[:width]
    if not beam:
        return None, -1.0
    best, best_p = beam[0], amp(beam[0])
    # Keep ALL beam members each pass (including ones that locally look worse) --
    # that is what lets the beam carry a barrier-crossing string through a stale
    # pass to a higher basin. Terminate on `patience` stale passes or a fixed point,
    # NOT on a single non-improving pass (which would collapse it back to greedy).
    patience = max(1, env_int("BEAM_PATIENCE", 3))
    stale, prev_set = 0, None
    for _ in range(max_passes):
        if time.time() > deadline:
            break
        cand = set(beam)
        stop = False
        for b in beam:
            for i in range(len(b)):
                if time.time() > deadline:
                    stop = True
                    break
                cand.add(flip(b, i))
            if stop:
                break
        new_beam = sorted(cand, key=amp, reverse=True)[:width]
        top_p = amp(new_beam[0])
        if top_p > best_p:
            best, best_p, stale = new_beam[0], top_p, 0
        else:
            stale += 1
        cur_set = frozenset(new_beam)
        if cur_set == prev_set or stale >= patience:
            break   # fixed point, or no improvement for `patience` passes
        prev_set, beam = cur_set, new_beam
    return best, best_p


# --------------------------------------------------------------------------- #
#  Orchestrator
# --------------------------------------------------------------------------- #

def solve(circuit, info, device, deadline) -> tuple[str, dict]:
    n = info["num_qubits"]
    candidates: dict[str, dict] = {}   # bitstring(qubit order) -> {methods:set, conf:float}

    def add(bs, method, conf=0.0):
        if not bs or len(bs) != n or any(c not in "01" for c in bs):
            return
        e = candidates.setdefault(bs, {"methods": set(), "conf": 0.0})
        e["methods"].add(method)
        e["conf"] = max(e["conf"], conf)

    def remaining():
        return max(0.0, deadline - time.time())

    # ---- Stage 1: adaptive distillation ----
    # Climb a bond-dimension ladder under ONE time budget. A low bond finishes fast
    # (guaranteeing a candidate); higher bonds sharpen the per-bit vote only while
    # time remains. A too-big bond that would time out just leaves us with the last
    # completed rung -- so we never end up with NO candidate the way a single fixed
    # high bond can (which is exactly what happened at bond 1024).
    distill_psi = None  # best completed distillation MPS, reused as Stage 4's verifier
    if os.environ.get("SKIP_DISTILL", "").strip() not in ("", "0", "false", "False"):
        log("Stage 1 (distillation): skipped via SKIP_DISTILL")
    else:
        b1 = min(remaining() * 0.20, env_float("T_DISTILL", 600))
        target = auto_int("DISTILL_MAX_BOND")
        b = max(8, env_int("DISTILL_START_BOND", 128))
        ladder = []
        while b < target:
            ladder.append(b)
            b *= 2
        ladder.append(target)
        log(f"Stage 1 (adaptive distillation): budget {b1:.0f}s, bond ladder {ladder}")
        t1 = time.time()
        prev_voted = None  # previous rung's voted bitstring, for convergence early-exit
        conv_h = env_int("DISTILL_CONVERGE_HAMMING", 1)
        for bd in ladder:
            left = b1 - (time.time() - t1)
            if left < 8:
                break
            try:
                with budget(left):
                    r = stage_distillation(circuit, device, max_bond=bd)
                add(r["voted"], "distill_vote", conf=r["decisiveness"])
                add(r["most_common"], "distill_mc", conf=r["mc_freq"])
                distill_psi = r.get("psi", distill_psi)  # keep highest completed rung's MPS
                log(f"  distill@bond{bd}: voted={r['voted'][:20]}.. "
                    f"decisiveness={r['decisiveness']:.3f} mc_freq={r['mc_freq']:.3f}")
                # Convergence early-exit: if the voted string stopped changing vs the
                # previous rung (Hamming <= conv_h), a higher bond won't move the answer
                # -- hand the residual (<=conv_h bits) to the hill-climb instead of
                # burning the rest of the budget climbing. This is the real signal on
                # mirror circuits, where `decisiveness` stays ~0.5 even when correct.
                if prev_voted is not None:
                    h = sum(a != b for a, b in zip(r["voted"], prev_voted))
                    if h <= conv_h:
                        log(f"  distill: vote converged (Hamming {h} vs prev rung); "
                            f"stopping ladder early, hill-climb will fix residual")
                        break
                prev_voted = r["voted"]
                if r["decisiveness"] >= env_float("DISTILL_STOP_DECISIVENESS", 0.95):
                    log("  distill: vote already decisive; stopping ladder early")
                    break
            except KeyboardInterrupt:
                log(f"  distill@bond{bd}: hit budget; keeping last completed rung")
                break
            except Exception as e:  # noqa: BLE001
                log(f"  distill@bond{bd} failed: {e}")
                break

    # ---- Stage 3 / Stage 2: escalate by structure ----
    structured = (not info["all_to_all_ish"]) and (not info["has_swap"])
    if structured:
        try:
            # TNO is fast when it works (sparse/heavy-hex: seconds-minutes) and
            # diverges on dense circuits -- cap it low by default so it can't burn
            # the budget the decisive oracle needs. Raise T_TNO for sparse circuits.
            b3 = min(remaining() * 0.40, env_float("T_TNO", 300))
            log(f"Stage 3 (TNO, permutation-free): budget {b3:.0f}s")
            with budget(b3):
                r = stage_tno(circuit, device,
                              max_bond=auto_int("TNO_MAX_BOND"),
                              tne_max_bond=auto_int("TNE_MAX_BOND"))
            log(f"  tno pred={r['pred'][:24]}...")
            add(r["pred"], "tno", conf=0.5)
        except KeyboardInterrupt:
            log("  Stage 3 hit its budget.")
        except Exception as e:  # noqa: BLE001
            log(f"  Stage 3 failed: {e}")

    # ---- Amplitude hill-climb oracle (runs BEFORE and AFTER unswapping) -------
    # The hill-climb is cheap (a few hundred `bitstring_probability` evals on an
    # already-built MPS) and is the decisive step that fixes residual single-bit
    # errors in the marginal vote (proven on d1: 45/46 -> 46/46). We REUSE the
    # distillation MPS as the verifier -- it is already built and faithful enough
    # to rank the peak, so we do NOT pay to build a fresh high-bond verifier.
    from utils import bitstring_probability
    verify_cap = auto_int("VERIFY_QUBIT_CAP")
    oracle = {"bs": None, "amp": -1.0, "used": False, "psi": None, "oracle": "mps"}

    def oracle_pass(budget_s, label):
        """Score the current candidates against the verifier MPS and beam-search
        from the best ones (Idea #2). Updates the global-best in `oracle`. Safe to
        call multiple times -- a no-op when there is nothing new to improve."""
        if n > verify_cap or not candidates or remaining() < 15:
            return
        psi = distill_psi if distill_psi is not None else oracle["psi"]
        if psi is None and remaining() > 90:   # no distillation MPS -> build one once
            try:
                vb = min(remaining() * 0.5, env_float("T_VERIFY_BUILD", 600))
                log(f"{label}: no distillation MPS; building verifier (budget {vb:.0f}s)")
                with budget(vb):
                    psi = build_verifier_psi(circuit, device, max_bond=auto_int("VERIFY_MAX_BOND"))
                oracle["psi"] = psi
            except Exception as e:  # noqa: BLE001
                log(f"  {label} verifier build failed: {e}")
                psi = None
        if psi is None:
            return
        try:
            oracle["used"] = True
            # Seed the beam from ALL candidates (distillation vote/mc, tno, unswap
            # samples), ranked by amplitude -- diversity helps the beam escape the
            # single-basin trap that left greedy hill-climb a few bits short.
            scored = sorted(candidates.keys(),
                            key=lambda b: float(bitstring_probability(psi, b)), reverse=True)
            base_amp = float(bitstring_probability(psi, scored[0]))
            log(f"{label}: best seed amp^2 = {base_amp:.4e} (baseline 2^-n = {2.0**-n:.2e})")
            cap = max(8.0, min(budget_s, remaining() - 10))
            seeds = scored[:max(1, env_int("BEAM_SEEDS", 8))]
            # beam's graceful deadline sits a few seconds INSIDE the hard budget so it
            # returns its best-so-far instead of being killed mid-pass by the SIGALRM.
            with budget(cap):
                bs, amp = beam_search(psi, seeds, min(deadline - 5, time.time() + cap - 3))
            if bs is not None and amp > oracle["amp"]:
                oracle["bs"], oracle["amp"] = bs, amp
            log(f"  {label} -> amp^2 = {amp:.4e} | bs={(bs or '')[:24]}...")
        except KeyboardInterrupt:
            log(f"  {label} hit its budget.")
        except Exception as e:  # noqa: BLE001
            log(f"  {label} (oracle) unavailable: {e}")

    # ---- Stage 4a: cheap hill-climb BEFORE the (possibly diverging) unswap -----
    # This is the routing fix: bank a best-effort answer NOW so a dense all-to-all
    # circuit still emits a result even if unswapping later eats its whole budget
    # without returning. On a structured circuit where distillation already nailed
    # it, this IS the solve and unswap is skipped below.
    oracle_pass(env_float("T_HILLCLIMB_EARLY", 150), "Stage 4a (early hill-climb)")

    # ---- Stage 2: mirror unswapping (general method for all-to-all) -----------
    n_cands_before_unswap = len(candidates)
    run_unswap = (info["all_to_all_ish"] or info["has_swap"] or not structured
                  or len(candidates) == 0) and remaining() > env_float("UNSWAP_MIN", 180)
    if run_unswap:
        # Idea #3: optionally scan the mirror split-point. Transpilation shifts gates,
        # so the true mirror midpoint may not be at 0.5 -- a misaligned split is what
        # makes the MPO bond grow uncontrollably (the d2_s1 divergence). Try several
        # center_ratios; every config's MPS samples become candidates and the amp^2
        # oracle picks the winner. Idea #1: with Stage 4a already banking an answer,
        # the whole remaining wall (minus a small verify reserve) goes to contraction.
        scan = os.environ.get("UNSWAP_CENTER_SCAN", "").strip()
        ratios = ([float(x) for x in scan.split(",") if x.strip()] if scan
                  else [env_float("UNSWAP_CENTER_RATIO", 0.5)])
        reserve = env_float("RESERVE_FOR_VERIFY", 240)
        total_b2 = max(0.0, remaining() - reserve)
        per = total_b2 / max(1, len(ratios))
        log(f"Stage 2 (mirror unswapping): budget {total_b2:.0f}s over center_ratios {ratios}")
        for cr in ratios:
            cap = min(per, max(0.0, remaining() - reserve))
            if cap < 30:
                log("  Stage 2: out of budget for the next center_ratio; stopping scan")
                break
            try:
                log(f"  unswap @center_ratio={cr}: budget {cap:.0f}s")
                with budget(cap):
                    r = stage_unswapping(
                        circuit, device,
                        cutoff=env_float("UNSWAP_CUTOFF", 0.002),
                        max_bond=auto_int("UNSWAP_MAX_BOND"),
                        unswap_threshold=env_float("UNSWAP_THRESHOLD", 1e6),
                        max_its=env_int("UNSWAP_MAX_ITS", 20),
                        center_ratio=cr,
                    )
                for k, c in enumerate(r["candidates"]):
                    add(c, "unswap", conf=r["top_freq"] if k == 0 else r["top_freq"] * 0.5)
                log(f"    pred={r['pred'][:24] if r['pred'] else None}... top_freq={r['top_freq']:.3f}")
                # A strongly-concentrated MPS means this split aligned the mirror --
                # no need to spend budget scanning the rest.
                if r["top_freq"] >= env_float("UNSWAP_TOPFREQ_STOP", 0.10):
                    log("    unswap concentrated (mirror aligned); stopping center scan early")
                    break
            except KeyboardInterrupt:
                log(f"  unswap @center_ratio={cr} hit its budget (partial); next ratio")
                continue
            except Exception as e:  # noqa: BLE001
                log(f"  unswap @center_ratio={cr} failed: {e}\n{traceback.format_exc()}")
                continue

    # ---- Stage 4b: refine using the FULL candidate set (incl. unswap) ---------
    # Only re-run if unswap actually produced new candidates (otherwise 4a already
    # gave the best answer and re-climbing the same seed is wasted work).
    if len(candidates) > n_cands_before_unswap or oracle["bs"] is None:
        oracle_pass(max(10.0, remaining() - 10), "Stage 4b (final hill-climb)")

    # ---- Stage 4c: belief-propagation oracle (Idea #4) + portfolio select (#5) -
    # The portfolio rule: keep whichever INDEPENDENT oracle certifies the highest
    # peak (amp^2). If the MPS oracle's best is at/near the uniform baseline 2^-n,
    # the 1D attack found nothing -- fall through to a BP contraction of the full
    # TN, which works on the non-1D geometry the MPS can't. Also runs on BP_ENABLE=1.
    baseline = 2.0 ** -n
    bp_force = os.environ.get("BP_ENABLE", "").strip() not in ("", "0", "false", "False")
    bp_trigger = baseline * env_float("BP_TRIGGER_MULT", 4.0)
    if (n <= verify_cap and candidates and remaining() > env_float("BP_MIN", 120)
            and (bp_force or oracle["amp"] < bp_trigger)):
        try:
            cap = max(20.0, remaining() - 15)
            log(f"Stage 4c (BP oracle): MPS amp^2={oracle['amp']:.2e} vs baseline "
                f"{baseline:.2e}; trying belief propagation (budget {cap:.0f}s)")
            with budget(cap):
                bp_score, bp_seed = build_bp_oracle(circuit, device)
                if bp_score is not None:
                    seeds = sorted(candidates.keys(),
                                   key=lambda b: candidates[b]["conf"],
                                   reverse=True)[:env_int("BEAM_SEEDS", 8)]
                    if bp_seed:
                        add(bp_seed, "bp_marginal", conf=0.6)
                        seeds = [bp_seed] + seeds
                    bs, amp = beam_search(
                        None, seeds, min(deadline - 5, time.time() + cap - 3),
                        width=env_int("BP_BEAM_WIDTH", 6), max_passes=4, score_fn=bp_score)
                    log(f"  Stage 4c (BP) -> bp_amp^2 = {amp:.4e} | bs={(bs or '')[:24]}...")
                    # Adopt BP's answer only if it certifies a clear peak (>> baseline)
                    # that beats the MPS oracle -- the portfolio max-amp^2 select.
                    if (bs is not None and amp > baseline * env_float("BP_ADOPT_MULT", 10.0)
                            and amp > oracle["amp"]):
                        oracle.update(bs=bs, amp=amp, used=True, oracle="bp")
                        log(f"  Stage 4c (BP) WINS the portfolio (bp_amp^2 {amp:.2e} "
                            f">> baseline {baseline:.2e})")
        except KeyboardInterrupt:
            log("  Stage 4c (BP) hit its budget.")
        except Exception as e:  # noqa: BLE001
            log(f"  Stage 4c (BP) failed: {e}")

    if not candidates and oracle["bs"] is None:
        log("No candidates from any stage; emitting all-zero fallback.")
        return "0" * n, {"reason": "no_candidate"}

    # ---- Stage 5: choose final answer (portfolio winner) ----
    if oracle["bs"] is not None and oracle["amp"] > 0:
        final = oracle["bs"]
        reason = f"amplitude_oracle[{oracle['oracle']}] amp2={oracle['amp']:.4e}"
    else:
        # trust method confidence; prefer unswap > tno > distill on hard circuits
        def rank(item):
            bs, meta = item
            prio = (3 if "unswap" in meta["methods"] else
                    2 if "tno" in meta["methods"] else 1)
            return (meta["conf"], prio)
        final = max(candidates.items(), key=rank)[0]
        reason = f"method_confidence methods={sorted(candidates[final]['methods'])}"

    diag = {
        "reason": reason,
        "used_oracle": oracle["used"],
        "winning_oracle": oracle["oracle"],
        "best_amp2": oracle["amp"] if oracle["amp"] > 0 else None,
        "baseline_2_minus_n": 2.0 ** -n,
        "amp2_over_baseline": (oracle["amp"] / (2.0 ** -n)) if oracle["amp"] > 0 else None,
        "num_candidates": len(candidates),
        "candidate_methods": {bs[:16] + "...": sorted(m["methods"]) for bs, m in list(candidates.items())[:8]},
    }
    return final, diag


# --------------------------------------------------------------------------- #
#  result.json emission
# --------------------------------------------------------------------------- #

def emit(bitstring: str, challenge_id: str, status: str, info: dict, diag: dict,
         solve_time: float, started: str, difficulty: int = 0) -> None:
    # Official HQP contract: result.json is the Solution dataclass
    # ({"status", "peaked_state"}). validate_hqp_solution accepts either bit order.
    peaked_state = bitstring if (bitstring and all(c in "01" for c in bitstring)) else None
    result_json = json.dumps(Solution(status, peaked_state).to_dict(), indent=2)

    solve_info = {
        "solution_status": status,
        "challenge_id": challenge_id,
        "timestamp_utc": started,
        "solve_time_seconds": round(solve_time, 2),
        "difficulty": difficulty,
        "num_qubits": info.get("num_qubits"),
        "two_qubit_gates": info.get("two_qubit_gates"),
        "depth": info.get("depth"),
        "method": diag.get("reason"),
        "fingerprint": info,
        "diagnostics": diag,
    }
    solve_info_json = json.dumps(solve_info, indent=2)

    # Local-dev convenience (no /output volume exists under the validator).
    out = os.environ.get("OUTPUT_DIR")
    if out:
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
            Path(out, "result.json").write_text(result_json)
            Path(out, "solve_info.json").write_text(solve_info_json)
        except OSError:
            pass

    write_solution_output(build_solution_zip({
        "result.json": result_json,
        "solve_info.json": solve_info_json,
    }))


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #

def _qasm_files_under(path: str) -> list[str]:
    """Return readable .qasm FILES at/under ``path`` (recursively), skipping dirs.

    Recursing matters because ``docker run -v /nonexistent.qasm:/x.qasm`` makes
    Docker create an empty *directory* at the source and mount it, so the target
    can be a directory named ``*.qasm``; the real file may also sit inside a
    mounted input directory."""
    if os.path.isfile(path):
        return [path]
    if os.path.isdir(path):
        return sorted(f for f in glob.glob(os.path.join(path, "**", "*.qasm"), recursive=True)
                      if os.path.isfile(f))
    return []


def get_solver_input():
    """Official HQP input via load_solver_input, with dev/robustness fallbacks.

    Returns (challenge_id, qasm_file, difficulty). Resolves to an actual readable
    .qasm FILE -- if a directory was mounted/pointed at (common docker -v mistake),
    it searches inside for the circuit rather than failing cryptically."""
    try:
        cid, problem = load_solver_input(sys.argv)
        files = _qasm_files_under(problem.qasm_file)
        if files:
            if files[0] != problem.qasm_file:
                log(f"qasm_file '{problem.qasm_file}' was not a plain file; using '{files[0]}'")
            return cid, files[0], int(problem.difficulty)
        log(f"qasm_file '{problem.qasm_file}' has no readable .qasm; trying fallbacks")
    except Exception as e:  # noqa: BLE001
        log(f"load_solver_input did not apply ({e}); trying fallbacks")
    # CLI/dev: a path argument that resolves to a .qasm file (or a dir holding one)
    for a in sys.argv[1:]:
        files = _qasm_files_under(a.strip())
        if files:
            return Path(files[0]).stem, files[0], 0
    # validator/standalone mounts
    for d in ("/challenge_input", "/app", "."):
        files = _qasm_files_under(d)
        if files:
            return Path(files[0]).stem, files[0], 0
    raise SystemExit(
        "No readable .qasm FILE found. NOTE: if you ran "
        "'docker run -v /ABS/PATH/circuit.qasm:...', substitute a REAL host path -- "
        "Docker silently creates an empty DIRECTORY when the source path does not "
        "exist. Mount a real .qasm file, or mount the /challenge_input directory.")


def main() -> None:
    # Bulletproof: ANY failure below still lands a valid result.json via the stdout
    # protocol. A crash with no output is an automatic validation loss.
    wall = env_int("WALL_TIME", 14400)
    margin = env_int("DEADLINE_MARGIN", 180)
    start = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()
    # Never let the safety margin push the deadline into the past (e.g. a small
    # WALL_TIME during local testing) -- that would starve every stage.
    deadline = start + max(60, wall - margin)

    # Pin BLAS/OpenMP threads BEFORE torch/numpy import (they read these at import).
    cores = cpu_count()
    set_thread_env(cores)

    challenge_id, info, diag, difficulty = "unknown", {}, {}, 0
    bitstring, status = "", "failed"
    try:
        # Install the robust SVD first so the GPU self-test below also exercises
        # (and benefits from) it.
        if os.environ.get("ROBUST_SVD", "1").strip() not in ("0", "false", "False"):
            install_robust_svd()
        device = setup_device()
        # Scale bond dimensions to the detected VRAM (uses the validator's 96 GB
        # without any -e flags); explicit env vars still override.
        configure_resources(device, cores)

        challenge_id, qasm_path, difficulty = get_solver_input()
        log(f"Challenge: {challenge_id} | difficulty={difficulty} | QASM: {qasm_path}")
        circuit = load_circuit(qasm_path)
        info = fingerprint(circuit)
        log(f"Fingerprint: n={info['num_qubits']} 2q={info['two_qubit_gates']} "
            f"depth={info['depth']} avg_deg={info['avg_degree']} "
            f"all_to_all={info['all_to_all_ish']} has_swap={info['has_swap']}")
        log(f"Ops: {info['ops']}")

        bitstring, diag = solve(circuit, info, device, deadline)
        # "success" means we are submitting a real candidate; the no-candidate
        # fallback is reported honestly as "failed" with peaked_state=None.
        status = "failed" if diag.get("reason") == "no_candidate" else "success"
    except SystemExit as e:  # noqa: BLE001
        log(f"FATAL (SystemExit): {e}")
        diag = {"reason": f"exit: {e}"}
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        diag = {"reason": f"exception: {type(e).__name__}: {e}"}

    n = info.get("num_qubits") or 0
    if status == "success" and (not bitstring or len(bitstring) != n or any(c not in "01" for c in bitstring)):
        status, bitstring = "failed", ""  # invalid candidate -> honest failure

    solve_time = time.time() - start
    log(f"FINAL: status={status} peaked_state={bitstring or None} "
        f"({solve_time:.1f}s, {diag.get('reason')})")

    try:
        emit(bitstring, challenge_id, status, info, diag, solve_time, started_iso, difficulty)
    except Exception as e:  # noqa: BLE001
        log(f"emit() failed, retrying minimal: {e}")
        try:
            ps = bitstring if (bitstring and all(c in "01" for c in bitstring)) else None
            write_solution_output(build_solution_zip({
                "result.json": json.dumps({"status": status, "peaked_state": ps}),
            }))
        except Exception as e2:  # noqa: BLE001
            log(f"minimal emit also failed: {e2}")
    os._exit(0 if status == "success" else 1)


if __name__ == "__main__":
    main()
