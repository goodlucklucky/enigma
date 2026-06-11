#!/usr/bin/env python3
# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

"""
Hardening Quantum Proof solver -- peaked circuit simulation.

Simulates the given OpenQASM circuit and finds the computational basis state
with the highest measurement probability (the "peaked state").

Strategy:
  - Small circuits (<=26 qubits): statevector simulation (exact)
  - Larger circuits: matrix product state (MPS) with sampling

Output contract (stdout-only, no shared filesystem with the validator):
  1. Text logs are written to stdout.
  2. After all logs, a magic separator line is written.
  3. After the separator, a base64-encoded zip of result.json and
     solve_info.json is written to stdout.

Usage (workbench): python hardening_quantum_proof.py <challenge_id> <JSON-encoded Problem>

Validator Docker mode: no CLI args; read /challenge_input/challenge_input.json
from the read-only mount (see enigma_challenges.hardening_quantum_proof.load_solver_input).
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import *

import numpy as np

from enigma_challenges.hardening_quantum_proof import Solution, load_solver_input
from enigma_challenges.solution_output import build_solution_zip, write_solution_output


def _printlog(msg: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)


def _load_circuit(qasm_file: str):
    """Load a QASM circuit, handling both QASM 2.0 and 3.0 formats."""
    from qiskit import qasm2
    from qiskit.circuit.library import UGate

    with open(qasm_file, "r") as f:
        header = f.readline().strip()

    if "3.0" in header:
        import qiskit.qasm3 as qasm3
        return qasm3.load(qasm_file)
    else:
        # QASM 2.0 — the 'u' gate is not in standard qelib1.inc,
        # so register it as a builtin custom instruction.
        custom = [
            qasm2.CustomInstruction(
                'u', 3, 1,
                lambda t, p, lam: UGate(t, p, lam),
                builtin=True,
            ),
        ]
        return qasm2.load(qasm_file, custom_instructions=custom)


def solve(qasm_file: str, log=_printlog) -> str:
    """Simulate the circuit and return the peaked bitstring."""
    from qiskit_aer import AerSimulator

    circ = _load_circuit(qasm_file)
    nqubits = circ.num_qubits
    log(f"Circuit loaded: {nqubits} qubits, {circ.size()} gates")

    use_statevector = nqubits <= 26

    if use_statevector:
        method = "statevector"
        log(f"Using statevector simulation (exact, {nqubits} qubits)")
        circ.save_statevector()
        circ.remove_final_measurements(inplace=True)
        backend = AerSimulator(method=method)
        job = backend.run(circ, shots=1)
        result = job.result()
        sv = np.array(result.data(0)["statevector"])
        probs = np.abs(sv) ** 2
        peak_idx = int(np.argmax(probs))
        peak_bits = f"{peak_idx:0{nqubits}b}"[::-1]
    else:
        method = "matrix_product_state"
        shots = 10000
        log(f"Using MPS simulation ({nqubits} qubits, {shots} shots)")
        circ.measure_all()
        backend = AerSimulator(method=method)
        job = backend.run(circ, shots=shots)
        result = job.result()
        counts = result.get_counts()
        peak_bits = max(counts.keys(), key=lambda x: counts[x])
        # Qiskit returns bitstrings in big-endian order; reverse for our convention
        peak_bits = peak_bits[::-1]

    log(f"Peaked state: {peak_bits}")
    return peak_bits


def main() -> None:
    try:
        challenge_id, problem = load_solver_input(sys.argv)
    except Exception as err:
        print(f"Error loading HQP input:\n{err}")
        sys.exit(1)

    timestamp_start = datetime.now(timezone.utc).isoformat()
    start_time = time.time()

    _printlog(f"Starting Hardening Quantum Proof challenge: {challenge_id}")
    _printlog(f"Difficulty: {problem.difficulty}")
    _printlog(f"QASM file: {problem.qasm_file}")

    try:
        peak_bits = solve(problem.qasm_file)
        solution = Solution("success", peak_bits)
    except Exception as e:
        _printlog(f"Solver failed: {e}")
        solution = Solution("failed", None)

    solve_time = time.time() - start_time
    _printlog(f"Finished in {solve_time:.2f}s, status={solution.status}")

    result_json = json.dumps(solution.to_dict(), indent=2)
    solve_info_json = json.dumps({
        "solution_status": solution.status,
        "challenge_id": challenge_id,
        "timestamp_utc": timestamp_start,
        "solve_time_seconds": solve_time,
        "difficulty": problem.difficulty,
    })

    # Write to OUTPUT_DIR if set (direct/dev mode)
    output_dir = os.environ.get("OUTPUT_DIR")
    if output_dir:
        try:
            Path(output_dir).mkdir(exist_ok=True)
            Path(output_dir, "result.json").write_text(result_json)
            Path(output_dir, "solve_info.json").write_text(solve_info_json)
        except OSError:
            pass

    # Emit solution via stdout protocol (docker/validator mode)
    zip_bytes = build_solution_zip({
        "result.json": result_json,
        "solve_info.json": solve_info_json,
    })
    write_solution_output(zip_bytes)

    exit_code = 0 if solution.status == "success" else 1
    os._exit(exit_code)


if __name__ == "__main__":
    main()
