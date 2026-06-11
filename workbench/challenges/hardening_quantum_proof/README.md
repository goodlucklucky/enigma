# Hardening Quantum Proof Challenge

*Developed in collaboration with [BlueQubit](https://www.bluequbit.io/).*

## Status

**Live** — For current prize pools and milestones, visit the [Enigma page](https://www.qbittensorlabs.com/enigma).

## What is it

Given a quantum circuit, find the output state with a disproportionately high
measurement probability -- the "peaked state."

A quantum circuit is a program that manipulates qubits (quantum bits). When you
run one, it produces a probability distribution over all possible output states.
Most circuits spread probability across many states. A **peaked circuit** is
different: one state has a disproportionately high probability compared to the
rest. Your job is to figure out which one.

The circuits are deliberately obfuscated with random circuit sampling, adding
massive complexity and making it infeasible to determine the peaked state by
analyzing the circuit structure alone. You need to actually simulate the circuit.

Think of it like a rigged lottery with 2^n possible outcomes. On inspection,
the circuit looks like it should produce random results, but one specific
outcome is weighted far more heavily than the rest. Your solver needs to
identify that outcome -- without access to a quantum computer.

The circuits are provided in [OpenQASM](https://openqasm.com/) format, a
standard text-based language for describing quantum circuits. Existing quantum
simulation frameworks (Qiskit, Cirq, Quantum Rings, BlueQubit, etc.) can load
and simulate these files.

## Why it matters

Today, there is no reliable way to verify that a quantum computer is actually
quantum. It could be a classical simulator, or broken hardware returning
garbage. A peaked circuit can serve as a **quantum proof** -- a discretely
verifiable test that a real quantum computer can solve but a classical system
cannot (at sufficient scale).

If classical solvers can crack these circuits efficiently, the proof doesn't
hold. This challenge pushes miners to find the limits of classical simulation,
directly hardening the viability of peaked circuits as quantum proofs.

## Runtime constraints

Solutions are executed by validators under these conditions:

- **Wall time:** 4 hours (14,400 seconds)
- **Network:** None (`--network none`, no internet access)
- **Platform:** `linux/amd64`
- **Compute:** NVIDIA RTX PRO 6000 (96 GB VRAM), 24 vCPU, 85 GB RAM
- **Filesystem:** Root filesystem is read-only. Use `/tmp` (tmpfs, noexec/nosuid) for scratch space.
- **User:** Runs as a non-root user (default `miner`). Your Dockerfile should create this user and ensure your solver and any required binaries are accessible to it (see the example Dockerfile).
- **Output contract:** There is **no** writable volume mounted. Your solver must emit results via the stdout protocol (text logs, then the `SOLUTION_OUTPUT_SEPARATOR` line, then a base64-encoded zip of `result.json` + `solve_info.json`). See `enigma_challenges/solution_output.py` for the exact protocol. The `OUTPUT_DIR` environment variable is only available in `--mode direct` for local development.

## Challenge parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `difficulty` | Difficulty level (set by BlueQubit; higher = harder) | 1 |

The workbench also accepts `--circuit <id>` to select a specific sample circuit
for reproducible local testing (e.g. `--circuit d1_s1_4043cafb`). If omitted,
a random circuit matching the difficulty is chosen.

Difficulty increases over time. BlueQubit controls multiple levers, including
but not limited to: qubit count, circuit depth, circuit complexity, and the
degree of peaking (how much probability concentrates on the peaked state vs.
the noise floor). The exact
relationship between difficulty level and circuit properties is not published —
your solver should be general-purpose.

## Input

In **validator Docker mode**, the validator mounts a read-only directory at
``/challenge_input/`` containing:

| File | Description |
|------|-------------|
| ``challenge_input.json`` | Problem fields plus ``challenge_id`` (see below) |
| ``circuit.qasm`` | OpenQASM circuit (HQP only) |

Your solver should read ``/challenge_input/challenge_input.json`` when no CLI
arguments are provided. See ``load_solver_input()`` in
``enigma_challenges.hardening_quantum_proof``.

**Workbench / local CLI mode** may also pass two positional arguments:

1. ``<challenge_id>`` -- a UUID identifying the challenge instance
2. ``<problem_json>`` -- a JSON string with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `difficulty` | int | Difficulty level |
| `qasm_file` | str | Path to the OpenQASM circuit file |

## Output

**Important (Docker/validator mode):** There is no writable volume inside the
container. The root filesystem is read-only. Your solver must deliver artifacts
exclusively by writing to **stdout** using the solution output protocol:

1. Human-readable text logs
2. The `SOLUTION_OUTPUT_SEPARATOR` line (see `enigma_challenges/solution_output.py`)
3. A base64-encoded zip containing `result.json` and `solve_info.json`

The validator captures stdout via `docker logs` after the container exits.

### `result.json`

```json
{
    "status": "success",
    "peaked_state": "0101101"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | str | Final solution status (`"success"`, `"failed"`, `"timeout"`) |
| `peaked_state` | str or null | The peaked bitstring (only `0`s and `1`s) |

### `solve_info.json`

| Field | Type | Description |
|-------|------|-------------|
| `challenge_id` | str | The challenge ID received as input |
| `timestamp_utc` | str | ISO 8601 timestamp when the solve started |
| `solution_status` | str | Final status |

Additional fields (solve duration, method used, etc.) are encouraged.

## Sample circuits

Sample circuits are in `sample_circuits/`. Each sample consists of a `.qasm`
circuit file and a `_meta.json` file with the known peaked state. The workbench
feeds these to your solver via the same plumbing used for real challenges —
your solver never sees the answer.

| ID | Difficulty | Qubits | Notes |
|----|-----------|--------|-------|
| `d0_s0_trivial` | 0 | 5 | Smoke test — completes in under a second on any simulator |
| `d1_s1_4043cafb` | 1 | 46 | Representative difficulty 1 circuit |
| `d1_s2_adeddcf3` | 1 | 48 | Representative difficulty 1 circuit |
| `d2_s1_39b370e4` | 2 | 40 | Representative difficulty 2 circuit |
| `d2_s2_1efabaf4` | 2 | 44 | Representative difficulty 2 circuit |

```bash
# List all available sample circuits
python -m workbench samples

# Filter by difficulty
python -m workbench samples --difficulty 1
```

## Example solution

A working reference implementation using [Qiskit Aer](https://qiskit.github.io/qiskit-aer/)
is provided in `example_solution/`. It uses statevector simulation for small
circuits and MPS (matrix product state) for larger ones. This is a starting
point — production circuits at higher difficulties will require more
sophisticated simulation techniques or hardware acceleration.

## Testing

```bash
# Smoke test with the trivial circuit (instant, any machine)
python -m workbench test hardening-quantum-proof --difficulty 0 \
    --solution workbench/challenges/hardening_quantum_proof/example_solution \
    --mode direct

# Docker mode (matches validator constraints)
python -m workbench test hardening-quantum-proof --difficulty 0 \
    --solution workbench/challenges/hardening_quantum_proof/example_solution \
    --mode docker

# Test with a specific sample circuit
python -m workbench test hardening-quantum-proof --circuit d1_s1_4043cafb \
    --solution ./my_solver/ --mode direct

# Test your own solver directory
python -m workbench test hardening-quantum-proof --difficulty 1 \
    --solution ./my_solver/
```

Use `--mode direct` to skip Docker during development. Always test with
`--mode docker` (the default) before submitting, as this applies the same
constraints the validator will enforce.
