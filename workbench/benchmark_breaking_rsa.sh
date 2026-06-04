#!/usr/bin/env bash

# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

#
# Benchmark Breaking RSA solutions across bit sizes.
#
# Runs each solution through the workbench Docker pipeline with increasing
# bit sizes until failure (timeout or error). Results are written to a CSV.
#
# Usage:
#   ./workbench/benchmark_breaking_rsa.sh
#
# Prerequisites:
#   - Docker installed and running
#   - Python 3.12+ with click, gmpy2, cryptography installed
#   - Run from the repo root (enigma-staging/)
#
# Environment variables (optional):
#   PYTHON        Path to Python 3.12+ binary (default: auto-detect)
#   SEED          Random seed for reproducibility (default: 42)
#   WALL_TIME     Wall time per run in seconds (default: 14400 = 4h)
#   CSV_FILE      Output CSV path (default: benchmark_results.csv)
#   SOLUTIONS     Comma-separated solution dirs to test (default: both)
#
set -euo pipefail

# --- Configuration -----------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SEED="${SEED:-42}"
WALL_TIME="${WALL_TIME:-14400}"
CSV_FILE="${CSV_FILE:-benchmark_results.csv}"

# Bit sizes to try: 300, 335, then 340, 345, 350, ... by 5
BIT_SIZES=(300 335 340 345 350 355 360 365 370 375 380)

# Solutions to benchmark
EXAMPLE_SOL="workbench/challenges/breaking_rsa/example_solution"

if [[ -n "${SOLUTIONS:-}" ]]; then
    IFS=',' read -ra SOL_DIRS <<< "$SOLUTIONS"
else
    SOL_DIRS=("$EXAMPLE_SOL")
fi

# --- Find Python 3.12+ -------------------------------------------------------

find_python() {
    for candidate in "${PYTHON:-}" python3.12 python3.13 python3; do
        [[ -z "$candidate" ]] && continue
        local bin
        bin="$(command -v "$candidate" 2>/dev/null || true)"
        [[ -z "$bin" ]] && continue
        local ver
        ver="$("$bin" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)"
        if [[ "$ver" == "(3, 12)"* ]] || [[ "$ver" == "(3, 13)"* ]] || [[ "$ver" == "(3, 14)"* ]]; then
            echo "$bin"
            return 0
        fi
    done
    # Try common locations
    for loc in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
        [[ -x "$loc" ]] && echo "$loc" && return 0
    done
    return 1
}

PYTHON_BIN="$(find_python)" || {
    echo "ERROR: Python 3.12+ not found. Set PYTHON env var." >&2
    exit 1
}
echo "Using Python: $PYTHON_BIN"

# --- Verify Docker ------------------------------------------------------------

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is not running." >&2
    exit 1
fi
echo "Docker: OK"

# --- Check dependencies -------------------------------------------------------

if ! "$PYTHON_BIN" -c "import click, gmpy2, cryptography" 2>/dev/null; then
    echo "ERROR: Missing Python dependencies. Install with:"
    echo "  $PYTHON_BIN -m pip install click gmpy2 cryptography"
    exit 1
fi
echo "Python dependencies: OK"

# --- Initialize CSV -----------------------------------------------------------

echo "solution,bits,digits,status,duration_s,method,p,q" > "$CSV_FILE"
echo ""
echo "Results will be written to: $CSV_FILE"
echo "Wall time per run: ${WALL_TIME}s ($((WALL_TIME / 3600))h)"
echo "Seed: $SEED"
echo ""
echo "=========================================="

# --- Run benchmarks -----------------------------------------------------------

run_one() {
    local sol_dir="$1"
    local bits="$2"
    local sol_name
    sol_name="$(basename "$sol_dir")"

    echo ""
    echo "--- $sol_name @ ${bits}-bit ---"
    echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    local tmpdir
    tmpdir="$(mktemp -d -t workbench-bench-XXXX)"

    local start_ts
    start_ts="$(date +%s)"

    # Run through workbench
    local exit_code=0
    PYTHONPATH="." "$PYTHON_BIN" -m workbench \
        test breaking-rsa \
        --solution "$sol_dir" \
        --difficulty "$bits" \
        --seed "$SEED" \
        --wall-time "$WALL_TIME" \
        --keep-output \
        2>&1 | tee "${tmpdir}/workbench.log" || exit_code=$?

    local end_ts
    end_ts="$(date +%s)"
    local duration=$(( end_ts - start_ts ))

    # Parse results
    local status="failed"
    local method=""
    local p_val=""
    local q_val=""
    local digits=""

    # Get digit count from log (macOS-compatible)
    digits="$(grep -o '([0-9]* digits)' "${tmpdir}/workbench.log" | head -1 | sed 's/[^0-9]//g' || true)"

    if [[ $exit_code -eq 0 ]]; then
        status="success"
        # Extract factors from verification line
        local verify_line
        verify_line="$(grep 'Correct factorization' "${tmpdir}/workbench.log" || true)"
        if [[ -n "$verify_line" ]]; then
            p_val="$(echo "$verify_line" | sed -n 's/.*p=\([0-9]*\).*/\1/p' || true)"
            q_val="$(echo "$verify_line" | sed -n 's/.*q=\([0-9]*\).*/\1/p' || true)"
        fi
        # Extract method from solve_info if available
        local output_dir
        output_dir="$(grep 'Output kept at:' "${tmpdir}/workbench.log" | awk '{print $NF}' || true)"
        if [[ -n "$output_dir" && -f "$output_dir/solve_info.json" ]]; then
            method="$(python3 -c "import json; print(json.load(open('$output_dir/solve_info.json')).get('method',''))" 2>/dev/null || true)"
        fi
    elif grep -q 'TIMEOUT' "${tmpdir}/workbench.log"; then
        status="timeout"
    fi

    echo "Finished: $(date -u '+%Y-%m-%d %H:%M:%S UTC') (${duration}s)"
    echo "Status: $status"

    # Write CSV row
    echo "${sol_name},${bits},${digits},${status},${duration},${method},${p_val},${q_val}" >> "$CSV_FILE"

    # Clean up
    rm -rf "$tmpdir"

    # Return 0 for success, 1 for failure
    [[ "$status" == "success" ]]
}

for sol_dir in "${SOL_DIRS[@]}"; do
    sol_name="$(basename "$sol_dir")"
    echo ""
    echo "=========================================="
    echo "Benchmarking: $sol_name"
    echo "=========================================="

    for bits in "${BIT_SIZES[@]}"; do
        if run_one "$sol_dir" "$bits"; then
            echo "PASS -- continuing to next size"
        else
            echo "FAIL/TIMEOUT @ ${bits}-bit -- stopping $sol_name"
            # Record that we stopped
            break
        fi
    done
done

echo ""
echo "=========================================="
echo "Benchmark complete. Results in: $CSV_FILE"
echo "=========================================="
cat "$CSV_FILE"
