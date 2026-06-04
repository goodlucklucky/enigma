# Breaking RSA Challenge

## What is it

Given a semiprime N (the product of two prime numbers), find the prime factors
p and q such that p * q = N.

## Why it matters

Semiprime factorization is the hard mathematical problem underlying RSA
encryption. The security of RSA relies on the assumption that factoring large
semiprimes is computationally infeasible. Breaking larger semiprimes
demonstrates increasing capability against real-world cryptographic systems,
and is a key benchmark for both classical and quantum approaches.

## Status

**Live** — For current prize pools and milestones, visit the [Enigma page](https://www.qbittensorlabs.com/enigma).

## Runtime constraints

Solutions are executed by validators under these conditions:

- **Wall time:** 4 hours (14,400 seconds)
- **Network:** None (`--network none`, no internet access)
- **Platform:** `linux/amd64`
- **Compute:** RTX PRO 6000 (96 GB VRAM), 26 cores, 96 GB RAM

## Challenge parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `difficulty` | Controls the bit-width of the semiprime to factor | 1 |

## Input

Your solver receives two positional CLI arguments:

1. `<challenge_id>` -- a UUID identifying the challenge instance
2. `<problem_json>` -- a JSON string with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `difficulty` | int | Difficulty label |
| `num` | int | The semiprime to factor |
| `num_bits` | int | Bit-width of the semiprime |

## Output

Your solver must write three files to `/output/` (Docker mode) or the
directory specified by the `OUTPUT_DIR` environment variable (direct mode):

### `result.json`

```json
{
    "status": "success",
    "p": 7,
    "q": 11
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | str | Final solution status (e.g., `"success"` or `"failure"`) |
| `p` | int or null | First prime factor |
| `q` | int or null | Second prime factor |

### `stdout.log`

Captured standard output from your solver.

### `solve_info.json`

Timing and metadata about the solve. Include at minimum:

| Field | Type | Description |
|-------|------|-------------|
| `challenge_id` | str | The challenge ID received as input |
| `timestamp_utc` | str | ISO 8601 timestamp when the solve started |
| `solution_status` | str | Final status (`"success"`, `"timeout"`, `"failed"`) |

Additional fields (solve duration, method used, etc.) are encouraged.

## Example

For a small semiprime N = 77:

- Input: `{"difficulty": 1, "num": 77, "num_bits": 7}`
- Expected output: `{"status": "success", "p": 7, "q": 11}`

## Example solution

A working reference implementation is provided in `example_solution/`. It uses
a multi-stage pipeline (trial division, Pollard's rho, algebraic attacks, ECM,
msieve SIQS) and includes a `Dockerfile` and `breaking_rsa.py` solver script.

## Testing

```bash
python -m workbench test breaking-rsa --solution ./my_solver/
```

Use `--mode direct` to skip Docker and run your solver script directly.
Use `--seed <int>` for reproducible challenge generation.
