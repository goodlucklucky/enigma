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

import json
import random
from pathlib import Path

from qbittensor.challenges.hardening_quantum_proof import Problem, Solution, Verif

SAMPLES_DIR = Path(__file__).resolve().parent / "hardening_quantum_proof" / "sample_circuits"


def list_samples(difficulty: int | None = None):
    """List available sample circuits, optionally filtered by difficulty.

    Returns list of dicts with id, qubit_count, difficulty_type, difficulty.
    """
    samples = []
    for meta_file in sorted(SAMPLES_DIR.glob("*_meta.json")):
        circuit_id = meta_file.stem.replace("_meta", "")
        with meta_file.open() as f:
            meta = json.load(f)
        d = meta.get("difficulty")
        if difficulty is not None and d != difficulty:
            continue
        samples.append({
            "id": circuit_id,
            "qubit_count": len(meta["peaked_state"]),
            "difficulty_type": meta.get("metadata", {}).get("type", "unknown"),
            "difficulty": d,
        })
    return samples


def load_sample_circuit(circuit_id: str | None = None, difficulty: int = 1):
    """Load a sample circuit. Returns (problem, verif, circuit_id_used).

    If circuit_id is None, picks a random circuit matching the given difficulty.
    """
    if circuit_id is None:
        samples = list_samples(difficulty=difficulty)
        if not samples:
            all_samples = list_samples()
            available_difficulties = sorted({s["difficulty"] for s in all_samples})
            raise FileNotFoundError(
                f"No sample circuits for difficulty {difficulty}. "
                f"Available difficulties: {available_difficulties}"
            )
        circuit_id = random.choice(samples)["id"]

    meta_path = SAMPLES_DIR / f"{circuit_id}_meta.json"
    qasm_path = SAMPLES_DIR / f"{circuit_id}.qasm"

    if not meta_path.exists():
        available = [s["id"] for s in list_samples()]
        raise FileNotFoundError(
            f"Unknown circuit ID '{circuit_id}'. Available: {', '.join(available)}"
        )

    with meta_path.open() as f:
        meta = json.load(f)

    problem = Problem(
        difficulty=meta.get("difficulty", difficulty),
        qasm_file=str(qasm_path),
    )
    verif = Verif(peaked_state=meta["peaked_state"])
    return problem, verif, circuit_id
