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

def setup_device():
    import torch
    allow_cpu = os.environ.get("ALLOW_CPU", "").strip() not in ("", "0", "false", "False")
    if torch.cuda.is_available():
        dev = os.environ.get("PEAKED_DEVICE", "cuda:0")
        log(f"GPU OK: {torch.cuda.get_device_name(0)} | torch {torch.__version__} | device={dev}")
        return dev
    msg = ("!!! NO CUDA GPU VISIBLE. This solver is GPU-first; running the tensor-network "
           "methods on CPU will not finish within the wall clock for non-trivial circuits.")
    log(msg)
    if not allow_cpu:
        log("Refusing to run on CPU. Set ALLOW_CPU=1 to override (debugging only).")
        sys.exit(2)
    log("ALLOW_CPU set -> continuing on CPU (debug).")
    return "cpu"


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

    # complex64 halves VRAM and speeds GPU ops (fine above the ~2e-3 cutoff);
    # default complex128 matches the reference exactly.
    dtype = torch.complex64 if os.environ.get("UNSWAP_DTYPE") == "complex64" else torch.complex128

    def to_backend(x):
        return torch.tensor(x, dtype=dtype, device=device)

    collect_2q = PassManager([Collect2qBlocks(), ConsolidateBlocks(force_consolidate=True)])
    circ = collect_2q.run(circuit)

    mpo, layers_left, layers_right, _stats = mpo_compress_unswap(
        circ, seed=seed, to_backend=to_backend,
        cutoff=cutoff, max_bond=max_bond,
        unswap_threshold=unswap_threshold, center_ratio=0.5, equal=False,
        flip_freq=None, max_its=max_its, early_stopping_gates=0,
        hows=("both", "left", "right"),
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
                                       max_bond=env_int("DISTILL_MAX_BOND", 128))
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
                              max_bond=env_int("TNO_MAX_BOND", 16),
                              tne_max_bond=env_int("TNE_MAX_BOND", 8))
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
                    max_bond=env_int("UNSWAP_MAX_BOND", 8192),
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
    verify_cap = env_int("VERIFY_QUBIT_CAP", 40)
    best_bs, best_amp = None, -1.0
    used_oracle = False
    if n <= verify_cap and remaining() > 30:
        psi = None
        try:
            vb = min(remaining() * 0.5, env_float("T_VERIFY_BUILD", 180))
            log(f"Stage 4: building verification MPS (n={n} <= cap {verify_cap}), budget {vb:.0f}s")
            with budget(vb):
                psi = build_verifier_psi(circuit, device, max_bond=env_int("VERIFY_MAX_BOND", 1024))
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

def emit(bitstring: str, circuit_id: str, status: str, info: dict, diag: dict,
         solve_time: float, started: str) -> None:
    # Superset of plausible field names. Serde.from_dict ignores unexpected keys,
    # so including aliases is safe and hedges the unknown canonical field name.
    result = {
        "status": status,
        "bitstring": bitstring,
        "peak_bitstring": bitstring,
        "peak": bitstring,
        "solution": bitstring,
        "answer": bitstring,
        "predictions": {circuit_id: bitstring},
    }
    extra_key = os.environ.get("PEAKED_RESULT_KEY", "").strip()
    if extra_key:
        result[extra_key] = bitstring

    solve_info = {
        "solution_status": status,
        "circuit_id": circuit_id,
        "timestamp_utc": started,
        "solve_time_seconds": round(solve_time, 2),
        "num_qubits": info.get("num_qubits"),
        "two_qubit_gates": info.get("two_qubit_gates"),
        "depth": info.get("depth"),
        "fingerprint": info,
        "diagnostics": diag,
        "reversed_bitstring": bitstring[::-1],  # convention fallback, for debugging
    }

    result_json = json.dumps(result, indent=2)
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

def main() -> None:
    wall = env_int("WALL_TIME", 14400)
    margin = env_int("DEADLINE_MARGIN", 180)
    start = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()
    deadline = start + wall - margin

    device = setup_device()

    try:
        qasm_path, circuit_id = locate_qasm()
    except SystemExit as e:
        log(str(e))
        emit("", "circuit", "failed", {}, {"reason": str(e)}, time.time() - start, started_iso)
        os._exit(1)

    log(f"QASM: {qasm_path}  (circuit_id={circuit_id})")
    circuit = load_circuit(qasm_path)
    info = fingerprint(circuit)
    log(f"Fingerprint: n={info['num_qubits']} 2q={info['two_qubit_gates']} "
        f"depth={info['depth']} avg_deg={info['avg_degree']} "
        f"all_to_all={info['all_to_all_ish']} has_swap={info['has_swap']}")
    log(f"Ops: {info['ops']}")

    try:
        bitstring, diag = solve(circuit, info, device, deadline)
        status = "success"
    except Exception as e:  # noqa: BLE001
        log(f"FATAL in solve(): {e}\n{traceback.format_exc()}")
        bitstring, diag, status = "0" * info["num_qubits"], {"reason": f"exception: {e}"}, "failed"

    solve_time = time.time() - start
    log(f"FINAL bitstring ({len(bitstring)} bits): {bitstring}")
    log(f"Decision: {diag.get('reason')} | solve_time={solve_time:.1f}s")

    emit(bitstring, circuit_id, status, info, diag, solve_time, started_iso)
    os._exit(0 if status == "success" else 1)


if __name__ == "__main__":
    main()
