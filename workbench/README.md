# Enigma Developer Workbench

Local testing tool for Enigma challenge solutions. The workbench lets you
generate challenges, run your solver, validate output, and verify correctness
-- all without connecting to the subnet.

For live challenges, prize pools, and milestones, see the [Enigma page](https://www.qbittensorlabs.com/enigma).

## Setup

Requires Python 3.12+ and Docker.

```bash
pip install -r workbench/requirements.txt
```

## Usage

```bash
python -m workbench <command>
```

### Test a solution

```bash
# Breaking RSA (Docker mode, default)
python -m workbench test breaking-rsa \
    --solution workbench/challenges/breaking_rsa/example_solution/ \
    --difficulty 300

# Direct mode (no Docker, runs solver as a subprocess)
python -m workbench test breaking-rsa \
    --solution workbench/challenges/breaking_rsa/example_solution/ \
    --difficulty 20 --mode direct

# Hardening Quantum Proof (smoke test with trivial circuit)
python -m workbench test hardening-quantum-proof --difficulty 0 \
    --solution workbench/challenges/hardening_quantum_proof/example_solution/

# Mock challenge (plumbing test)
python -m workbench test mock \
    --solution <path> --private-key <hex>
```

### Other commands

```bash
# Show challenge parameters and defaults
python -m workbench milestones

# Validate output directory structure without running a solver
python -m workbench validate <output_dir> --challenge breaking_rsa

# Generate an Ed25519 keypair for the mock challenge
python -m workbench keygen
```

### Common options

| Option | Description |
|--------|-------------|
| `--solution <path>` | Path to your solution directory (must contain a `Dockerfile` for Docker mode, or a solver script for direct mode) |
| `--mode docker\|direct` | `docker` (default) builds and runs in a container; `direct` runs the solver script as a local subprocess |
| `--difficulty <int>` | Challenge difficulty (meaning varies by challenge type) |
| `--seed <int>` | Random seed for reproducible challenge generation |
| `--wall-time <secs>` | Max runtime in seconds (default: 14400 = 4h, matches validator) |
| `--allow-network` | Allow network access in Docker (validator runs with `--network none`) |
| `--keep-output` | Keep the output directory after the test |

## How it works

1. **Generate** -- Creates a challenge instance (problem + verification data)
2. **Build** -- (Docker mode) Assembles a build context with your solution and
   the `challenges` package (provided as `enigma_challenges/` in the context), then runs `docker build`
3. **Run** -- Executes your solver with the challenge ID and problem JSON as
   arguments. In Docker mode (the default, matching the validator), the root
   filesystem is read-only and no output volume is mounted. Your solver must
   emit results exclusively via stdout using the solution output protocol
   (logs + separator + base64 zip). The validator/workbench captures this
   after the container exits. In direct mode, the `OUTPUT_DIR` env var is
   provided for convenience and files may be written there.
4. **Validate** -- Checks that required output files exist and conform to the
   expected schema (`result.json`, `stdout.log`, `solve_info.json`)
5. **Verify** -- Compares your solution against the known answer

## Solution directory layout

Your solution directory should contain:

- **`Dockerfile`** -- (Docker mode) Builds an image with your solver and its
  dependencies. The build context will include an `enigma_challenges/` directory
  (the vendored challenges package for your solver to import types from).
  Your image must be runnable under the validator's runtime restrictions
  (read-only rootfs, non-root user, limited tmpfs at /tmp for scratch only,
  no network, etc.). See the example Dockerfiles and the "Runtime constraints"
  sections in the individual challenge READMEs.
- **`<challenge_name>.py`** -- Your solver script. Receives `<challenge_id>`
  and `<problem_json>` as CLI arguments. In Docker mode it must emit its final
  artifacts via the stdout protocol (not by writing files inside the container).
  Direct mode provides `OUTPUT_DIR` for local convenience.

See `workbench/challenges/*/example_solution/` for working examples.

## Benchmarking

Run the benchmark script to test Breaking RSA solutions across increasing bit
sizes. Each run goes through the full workbench Docker pipeline (build, run with
`--network none`, validate, verify).

```bash
# From the repo root
./workbench/benchmark_breaking_rsa.sh
```

The script will:
1. Auto-detect Python 3.12+ and verify Docker is running
2. Test `example_solution` across increasing bit sizes
3. Try bit sizes: 300, 335, 340, 345, 350, ... stepping by 5
4. Stop on first failure/timeout
5. Write results to `benchmark_results.csv`

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PYTHON` | auto-detect | Path to Python 3.12+ binary |
| `SEED` | `42` | Random seed for reproducibility |
| `WALL_TIME` | `14400` (4h) | Wall time per run in seconds |
| `CSV_FILE` | `benchmark_results.csv` | Output CSV path |
| `SOLUTIONS` | comma-separated | Solution dirs to test |

### Example

```bash
# Quick test with a 1-hour wall time
WALL_TIME=3600 ./workbench/benchmark_breaking_rsa.sh
```
