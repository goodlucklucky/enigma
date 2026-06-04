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
# Package the example_solution benchmark for deployment to a remote machine.
#
# Creates a self-contained tarball that can be scp'd to any Linux box
# with Docker and Python 3.12+ installed.
#
# Usage:
#   ./workbench/deploy_benchmark.sh
#   scp breaking_rsa_benchmark.tar.gz user@rtx6000-host:~/
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

OUTDIR="$(mktemp -d -t deploy-XXXX)"
DEST="$OUTDIR/breaking_rsa_benchmark"
mkdir -p "$DEST"

echo "Packaging benchmark..."

# Workbench CLI + runner
cp -r workbench "$DEST/workbench"

# challenges package (vendored under 'enigma_challenges' name for benchmark layout + solution imports)
cp -r qbittensor/challenges "$DEST/enigma_challenges"

# Clean up __pycache__ and egg-info
find "$DEST" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$DEST" -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true

# Create the run script
cat > "$DEST/run_benchmark.sh" << 'RUNEOF'
#!/usr/bin/env bash
#
# Run the Breaking RSA benchmark.
#
# This runs through the workbench with validator constraints:
#   - 4-hour wall time per run
#   - --network none
#   - linux/amd64 Docker container
#
# Bit sizes increase until failure (timeout or error).
#
# Usage:
#   ./run_benchmark.sh                    # defaults: example_solution, 300..500 bits
#   SOLUTION=example_solution_red_herring ./run_benchmark.sh
#   WALL_TIME=3600 ./run_benchmark.sh     # 1h cap per run (for quick testing)
#   BIT_SIZES="300 350 400" ./run_benchmark.sh  # custom sizes
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SEED="${SEED:-42}"
WALL_TIME="${WALL_TIME:-14400}"
SOLUTION="${SOLUTION:-example_solution}"
CSV_FILE="${CSV_FILE:-benchmark_${SOLUTION}_$(date +%Y%m%d_%H%M%S).csv}"

# Bit sizes: start conservatively, then push into hard territory
if [[ -n "${BIT_SIZES:-}" ]]; then
    read -ra SIZES <<< "$BIT_SIZES"
else
    SIZES=(300 320 340 360 380 400 420 440 460 480 500)
fi

SOLUTION_DIR="$SCRIPT_DIR/workbench/challenges/breaking_rsa/$SOLUTION"

if [[ ! -d "$SOLUTION_DIR" ]]; then
    echo "ERROR: Solution directory not found: $SOLUTION_DIR" >&2
    echo "Available solutions:" >&2
    ls -d "$SCRIPT_DIR/workbench/challenges/breaking_rsa"/*_solution* 2>/dev/null | xargs -n1 basename >&2
    exit 1
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
    return 1
}

PYTHON_BIN="$(find_python)" || {
    echo "ERROR: Python 3.12+ not found. Install it or set PYTHON env var." >&2
    exit 1
}

# --- Preflight ----------------------------------------------------------------

echo "=== Breaking RSA Benchmark ==="
echo "Python:     $PYTHON_BIN"
echo "Solution:   $SOLUTION_DIR"
echo "Wall time:  ${WALL_TIME}s ($((WALL_TIME / 3600))h) per run"
echo "Seed:       $SEED"
echo "Bit sizes:  ${SIZES[*]}"
echo "CSV output: $CSV_FILE"
echo ""

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is not running." >&2
    exit 1
fi
echo "Docker: OK"

# Install dependencies if needed
if ! "$PYTHON_BIN" -c "import click, gmpy2, cryptography" 2>/dev/null; then
    echo "Installing Python dependencies..."
    "$PYTHON_BIN" -m pip install click gmpy2 cryptography
fi
echo "Dependencies: OK"
echo ""

# Symlink (or ensure) the vendored challenges package is available as 'enigma_challenges' for PYTHONPATH/import
CHALLENGES_PKG="$SCRIPT_DIR/enigma_challenges"
if [[ -d "$CHALLENGES_PKG" ]]; then
    ln -sf "$CHALLENGES_PKG" "$SCRIPT_DIR/enigma_challenges"
fi

# --- CSV header ---------------------------------------------------------------

echo "solution,bits,status,duration_s,timestamp" > "$CSV_FILE"

# --- Run ----------------------------------------------------------------------

for bits in "${SIZES[@]}"; do
    echo "=========================================="
    echo "Testing ${bits}-bit semiprime..."
    echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo ""

    start_ts="$(date +%s)"
    exit_code=0

    PYTHONPATH="$SCRIPT_DIR" \
        "$PYTHON_BIN" -m workbench test breaking-rsa \
            --solution "$SOLUTION_DIR" \
            --difficulty "$bits" \
            --seed "$SEED" \
            --wall-time "$WALL_TIME" \
            --keep-output \
        2>&1 | tee "/tmp/bench_${bits}.log" || exit_code=$?

    end_ts="$(date +%s)"
    duration=$(( end_ts - start_ts ))
    timestamp="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    if [[ $exit_code -eq 0 ]]; then
        status="success"
        echo ""
        echo "PASS @ ${bits}-bit in ${duration}s"
    else
        status="failed"
        echo ""
        echo "FAIL @ ${bits}-bit after ${duration}s -- stopping benchmark"
        echo "${SOLUTION_DIR##*/},${bits},${status},${duration},${timestamp}" >> "$CSV_FILE"
        break
    fi

    echo "${SOLUTION_DIR##*/},${bits},${status},${duration},${timestamp}" >> "$CSV_FILE"
    echo ""
done

echo ""
echo "=========================================="
echo "Benchmark complete."
echo ""
echo "Results:"
column -t -s',' "$CSV_FILE" 2>/dev/null || cat "$CSV_FILE"
echo ""
echo "CSV saved to: $CSV_FILE"
RUNEOF

chmod +x "$DEST/run_benchmark.sh"

# Create the tarball
TARBALL="$REPO_ROOT/breaking_rsa_benchmark.tar.gz"
tar -czf "$TARBALL" -C "$OUTDIR" breaking_rsa_benchmark
rm -rf "$OUTDIR"

echo ""
echo "Package created: $TARBALL"
echo "Size: $(du -h "$TARBALL" | cut -f1)"
echo ""
echo "=== Deployment Instructions ==="
echo ""
echo "1. Copy to the RTX 6000 machine:"
echo "   scp $TARBALL user@host:~/"
echo ""
echo "2. SSH in and extract:"
echo "   ssh user@host"
echo "   tar xzf breaking_rsa_benchmark.tar.gz"
echo "   cd breaking_rsa_benchmark"
echo ""
echo "3. Install prerequisites (if needed):"
echo "   sudo apt install -y python3.12 python3.12-venv docker.io"
echo "   pip install click gmpy2 cryptography"
echo ""
echo "4. Run the benchmark (4h wall time per run, validator constraints):"
echo "   ./run_benchmark.sh"
echo ""
echo "5. Quick test first (1h cap per run):"
echo "   WALL_TIME=3600 ./run_benchmark.sh"
echo ""
echo "6. Custom bit sizes:"
echo '   BIT_SIZES="300 350 400 450 500 512" ./run_benchmark.sh'
echo ""
echo "Results are written to benchmark_results_<timestamp>.csv"
