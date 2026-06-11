# Peaked Circuits Solver — Enigma "Hardening Quantum Proof"

Classically recovers the hidden **peak bitstring** of an OpenQASM peaked circuit, on a
single GPU, within the validator's 4-hour wall clock.

It is a thin orchestrator over the **verbatim** reference implementation of
Kremer & Dupuis, *Efficient Classical Simulation of Heuristic Peaked Quantum Circuits*
([arXiv:2604.21908](https://arxiv.org/abs/2604.21908),
[github.com/d-kremer/peaked-circuit-simulation](https://github.com/d-kremer/peaked-circuit-simulation), Apache-2.0).
See [NOTICE](NOTICE) and [LICENSE-Apache-2.0](LICENSE-Apache-2.0).

## Pipeline

| Stage | What | Source |
|------|------|--------|
| 0 | Locate + parse QASM, structural fingerprint | `solve_peaked.py` |
| 1 | **Distillation** — low-bond `CircuitPermMPS` + per-bit majority vote | `peaked-circuit-distillation.ipynb` |
| 2 | **Mirror unswapping** — MPO iterative cancellation + greedy unswapping (the heavy hammer) | `unswap.py` |
| 3 | **TNO contraction** from the centre + marginal extraction (permutation-free circuits) | `utils.py` |
| 4 | **Amplitude oracle** `|⟨s|C|0⟩|²` + greedy hill-climb (verify & correct) | `utils.bitstring_probability` |
| 5 | Confidence gate → emit | `solve_peaked.py` |

The orchestrator runs the cheap stage first, escalates by circuit structure, then verifies
every candidate on one footing with the amplitude oracle. All methods are normalised to one
convention: **output character `i` = qubit `i`** (Qiskit index, qubit 0 leftmost) — exactly
the convention the reference validates against BlueQubit's `true_bs`.

## I/O contract

- **Input** (searched in order): an argv `.qasm` path · the docker mount `/app/peaked-circuit.qasm`
  · a `/challenge_input/` dir (`*.qasm` or `challenge_input.json`) · any `*.qasm` in `/app`/CWD.
- **Output**: the Enigma stdout protocol — logs, the `SOLUTION_OUTPUT_SEPARATOR` line, then a
  base64 zip of `result.json` + `solve_info.json`.

### ⚠️ One unknown to confirm at launch
The peaked-circuit challenge handler is **not yet committed** to the Enigma repo (only
`breaking_rsa` and `mock` exist), so the exact `result.json` field name is unconfirmed.
`result.json` therefore emits the peak under **several aliases** — `bitstring`, `peak_bitstring`,
`peak`, `solution`, `answer`, and `predictions{circuit_id: bitstring}`. The validator's
`Serde.from_dict` ignores unexpected keys, so a superset is safe. If the official README names a
different field, set `PEAKED_RESULT_KEY=<name>` (added as an extra alias).

## Tuning knobs (env)

`WALL_TIME`, `DEADLINE_MARGIN`, `ALLOW_CPU` (debug), `PEAKED_DEVICE`,
`DISTILL_MAX_BOND` (128), `UNSWAP_MAX_BOND` (8192), `UNSWAP_CUTOFF` (0.002),
`UNSWAP_THRESHOLD` (1e6), `UNSWAP_MAX_ITS` (20), `TNO_MAX_BOND` (16), `TNE_MAX_BOND` (8),
`VERIFY_QUBIT_CAP` (40), `VERIFY_MAX_BOND` (1024).

## Local testing

```bash
# 1) generate a small peaked circuit with a brute-force-verified ground-truth peak
python tools/generate_peaked.py --n 12 --depth 6 --swaps 4 --seed 1 --out /tmp/t.qasm

# 2) run the real solver entrypoint and check it recovers the planted peak
python tools/test_local.py --qasm /tmp/t.qasm
```

Build the container with `docker build -t peaked .` and run it like the validator:
`docker run --rm --gpus all --network none -v /tmp/t.qasm:/app/peaked-circuit.qasm:ro peaked`.
