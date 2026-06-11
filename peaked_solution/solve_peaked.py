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

from enigma_challenges.solution_output import build_solution_zip, write_solution_output
from enigma_challenges.hardening_quantum_proof import Solution, Problem, load_solver_input


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
    def __init__(self, seconds: float):
        self.seconds = max(1, int(seconds))
        self._old = None

    def __enter__(self):
        def _raise(signum, frame):
            raise KeyboardInterrupt(f"budget of {self.seconds}s elapsed")
        self._old = signal.signal(signal.SIGALRM, _raise)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb):
        signal.alarm(0)
        if self._old is not None:
            signal.signal(signal.SIGALRM, self._old)
        # Do NOT suppress: let the timeout KeyboardInterrupt propagate to the
        # stage's own `except KeyboardInterrupt` handler. Suppressing it here would
        # let execution fall through into code that assumes the stage finished
        # (e.g. a verifier `psi` that was never assigned).
        return False


def budget(seconds: float) -> _Budget:
    return _Budget(seconds)


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
    }


# --------------------------------------------------------------------------- #
#  Stage 2: mirror unswapping (the heavy hammer)
#  Faithful to peaked-circuit-unswapping.ipynb.
# --------------------------------------------------------------------------- #

def stage_unswapping(circuit, device, seed=123, cutoff=0.002, max_bond=8192,
                     unswap_threshold=1e6, max_its=20, shots=1000):
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
        unswap_threshold=unswap_threshold, center_ratio=0.5, equal=False,
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

    # ---- Stage 1: distillation (cheap; often solves Level-1 outright) ----
    if os.environ.get("SKIP_DISTILL", "").strip() not in ("", "0", "false", "False"):
        log("Stage 1 (distillation): skipped via SKIP_DISTILL")
    else:
        try:
            b1 = min(remaining() * 0.20, env_float("T_DISTILL", 600))
            log(f"Stage 1 (distillation): budget {b1:.0f}s")
            with budget(b1):
                r = stage_distillation(circuit, device,
                                       max_bond=auto_int("DISTILL_MAX_BOND"))
            log(f"  voted={r['voted'][:24]}... mc_freq={r['mc_freq']:.3f} decisiveness={r['decisiveness']:.3f}")
            add(r["voted"], "distill_vote", conf=r["decisiveness"])
            add(r["most_common"], "distill_mc", conf=r["mc_freq"])
        except KeyboardInterrupt:
            log("  Stage 1 hit its budget.")
        except Exception as e:  # noqa: BLE001
            log(f"  Stage 1 failed: {e}")

    # ---- Stage 3 / Stage 2: escalate by structure ----
    structured = (not info["all_to_all_ish"]) and (not info["has_swap"])
    if structured:
        try:
            b3 = min(remaining() * 0.40, env_float("T_TNO", 1800))
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

    # Always try unswapping when there is real budget left and the circuit is
    # non-trivial -- it is the most general method for BlueQubit all-to-all circuits.
    run_unswap = (info["all_to_all_ish"] or info["has_swap"] or not structured
                  or len(candidates) == 0) and remaining() > env_float("UNSWAP_MIN", 180)
    if run_unswap:
        try:
            b2 = max(0.0, remaining() - env_float("RESERVE_FOR_VERIFY", 240))
            log(f"Stage 2 (mirror unswapping): budget {b2:.0f}s")
            with budget(b2):
                r = stage_unswapping(
                    circuit, device,
                    cutoff=env_float("UNSWAP_CUTOFF", 0.002),
                    max_bond=auto_int("UNSWAP_MAX_BOND"),
                    unswap_threshold=env_float("UNSWAP_THRESHOLD", 1e6),
                    max_its=env_int("UNSWAP_MAX_ITS", 20),
                )
            for k, c in enumerate(r["candidates"]):
                add(c, "unswap", conf=r["top_freq"] if k == 0 else r["top_freq"] * 0.5)
            log(f"  unswap pred={r['pred'][:24] if r['pred'] else None}... top_freq={r['top_freq']:.3f}")
        except KeyboardInterrupt:
            log("  Stage 2 hit its budget (returned partial / no result).")
        except Exception as e:  # noqa: BLE001
            log(f"  Stage 2 failed: {e}\n{traceback.format_exc()}")

    if not candidates:
        log("No candidates from any stage; emitting all-zero fallback.")
        return "0" * n, {"reason": "no_candidate"}

    # ---- Stage 4: amplitude-oracle verification + hill-climb ----
    verify_cap = auto_int("VERIFY_QUBIT_CAP")
    best_bs, best_amp = None, -1.0
    used_oracle = False
    if n <= verify_cap and remaining() > 30:
        psi = None
        try:
            vb = min(remaining() * 0.5, env_float("T_VERIFY_BUILD", 180))
            log(f"Stage 4: building verification MPS (n={n} <= cap {verify_cap}), budget {vb:.0f}s")
            with budget(vb):
                psi = build_verifier_psi(circuit, device, max_bond=auto_int("VERIFY_MAX_BOND"))
            if psi is None:
                raise RuntimeError("verifier build did not complete")
            used_oracle = True
            # score every candidate, then hill-climb the best one
            from utils import bitstring_probability
            scored = sorted(candidates.keys(),
                            key=lambda b: float(bitstring_probability(psi, b)), reverse=True)
            seed_bs = scored[0]
            base_amp = float(bitstring_probability(psi, seed_bs))
            log(f"  best raw candidate amp^2 = {base_amp:.4e} (baseline 2^-n = {2.0**-n:.2e})")
            with budget(max(10.0, remaining() - 30)):
                best_bs, best_amp = hill_climb(psi, seed_bs, deadline - 15)
            log(f"  after hill-climb amp^2 = {best_amp:.4e} | bs={best_bs[:24]}...")
        except KeyboardInterrupt:
            log("  Stage 4 hit its budget.")
        except Exception as e:  # noqa: BLE001
            log(f"  Stage 4 (oracle) unavailable: {e}")

    # ---- Stage 5: choose final answer ----
    if best_bs is not None and best_amp > 0:
        final = best_bs
        reason = f"amplitude_oracle amp2={best_amp:.4e}"
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
        "used_oracle": used_oracle,
        "best_amp2": best_amp if best_amp > 0 else None,
        "baseline_2_minus_n": 2.0 ** -n,
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
