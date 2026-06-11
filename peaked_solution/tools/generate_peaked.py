#!/usr/bin/env python3
"""Generate a small peaked circuit with a BRUTE-FORCE-VERIFIED ground-truth peak.

Construction (mirror + peaking layer, the BlueQubit-style toy):

    C = A  ▷  A^{-1}  ▷  X_s

where A is a random entangling brickwork circuit (RY/RZ rotations + RZZ on a
brickwork pattern) with optional SWAPs sprinkled in, and X_s flips the qubits
where the secret bitstring s is 1. Since A·A^{-1} = I, the exact output state is
|s> — but the *circuit* carries the full mirror + hidden-permutation structure
the unswapping method must cancel. For n <= --max-statevector we additionally
confirm the true peak by exact statevector simulation, so the sidecar answer is
never assumed — it is computed.

Outputs:
    <out>.qasm           OpenQASM 2.0 circuit
    <out>.peak.json      {"peak": "...", "peak_weight": ..., "n": ..., ...}

Convention: peak string index i == qubit i (Qiskit index, qubit 0 leftmost),
matching solve_peaked.py.
"""
import argparse
import json
import math
import random
from pathlib import Path


def build_A(n, depth, n_swaps, rng):
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(n)
    swap_positions = set(rng.sample(range(depth * max(1, n)), k=min(n_swaps, depth * max(1, n)))) if n_swaps else set()
    tick = 0
    for layer in range(depth):
        for q in range(n):
            qc.ry(rng.uniform(0, 2 * math.pi), q)
            qc.rz(rng.uniform(0, 2 * math.pi), q)
        # brickwork RZZ
        for q in range(layer % 2, n - 1, 2):
            qc.rzz(rng.uniform(0, 2 * math.pi), q, q + 1)
            if tick in swap_positions and q + 1 < n:
                qc.swap(q, q + 1)
            tick += 1
    return qc


def true_peak_statevector(qc, n):
    """Exact argmax over |amplitude|^2; returns (peak_str[i]=qubit i, weight)."""
    from qiskit.quantum_info import Statevector
    probs = Statevector(qc).probabilities()  # index j: bit k of j is qubit k
    j = int(probs.argmax())
    peak = "".join(str((j >> i) & 1) for i in range(n))
    return peak, float(probs[j])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--depth", type=int, default=6, help="layers in A")
    ap.add_argument("--swaps", type=int, default=4, help="random SWAPs sprinkled into A")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=str, default="/tmp/peaked.qasm")
    ap.add_argument("--max-statevector", type=int, default=18,
                    help="verify the peak by exact simulation up to this many qubits")
    ap.add_argument("--measure", action="store_true", help="append measure_all()")
    args = ap.parse_args()

    from qiskit import QuantumCircuit, qasm2

    rng = random.Random(args.seed)
    n = args.n
    s = "".join(rng.choice("01") for _ in range(n))  # planted secret (char i = qubit i)

    A = build_A(n, args.depth, args.swaps, rng)
    C = QuantumCircuit(n)
    C = C.compose(A)
    C = C.compose(A.inverse())
    for i, b in enumerate(s):           # peaking layer X_s
        if b == "1":
            C.x(i)

    if n <= args.max_statevector:
        peak, weight = true_peak_statevector(C, n)
        verified = True
        if peak != s:
            print(f"[note] statevector argmax {peak} != planted {s} "
                  f"(weight {weight:.3f}); using the simulated argmax as ground truth.")
    else:
        peak, weight, verified = s, None, False
        print(f"[note] n={n} > {args.max_statevector}: trusting planted peak (not simulated).")

    if args.measure:
        C.measure_all()

    out = Path(args.out)
    out.write_text(qasm2.dumps(C))
    sidecar = out.with_suffix(out.suffix + ".peak.json") if out.suffix else Path(str(out) + ".peak.json")
    sidecar = Path(str(out) + ".peak.json")
    sidecar.write_text(json.dumps({
        "peak": peak,
        "planted": s,
        "peak_weight": weight,
        "verified_by_statevector": verified,
        "n": n,
        "depth": args.depth,
        "swaps": args.swaps,
        "seed": args.seed,
        "two_qubit_gates": dict(C.count_ops()),
    }, indent=2))
    print(f"wrote {out}  ({C.num_qubits} qubits, {sum(C.count_ops().values())} ops)")
    print(f"wrote {sidecar}  peak={peak} weight={weight}")


if __name__ == "__main__":
    main()
