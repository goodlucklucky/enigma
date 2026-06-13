# Peaked Circuits solver — GPU-first (CUDA via self-contained PyTorch wheels).
#
# The validator runs this on an NVIDIA RTX PRO 6000 (96 GB, Blackwell) with
# --network none and a read-only rootfs, as a non-root user, 4-hour wall.
# Build has network; runtime does not. All scratch goes to /tmp (tmpfs).
#
# Torch cu130 wheels bundle their own CUDA runtime, so no system CUDA toolkit is
# needed in the image — a slim Python base is enough and keeps the image small.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TMPDIR=/tmp \
    NUMBA_CACHE_DIR=/tmp \
    MPLCONFIGDIR=/tmp \
    XDG_CACHE_HOME=/tmp

RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential \
    && rm -rf /var/lib/apt/lists/*

# --- GPU PyTorch (Blackwell/sm_120 capable; matches the locally-validated stack) ---
RUN pip install --index-url https://download.pytorch.org/whl/cu130 torch

# --- Quantum / tensor-network stack ---
# EXACT pins from the reference repo's uv.lock (Kremer & Dupuis), the environment
# their code is tested against. The QASM loader in hardening_quantum_proof.py is robust to
# qiskit's qasm2/qasm3/legacy API differences.
RUN pip install \
    "numpy==2.4.4" \
    "qiskit==2.4.0" \
    "qiskit-qasm3-import" \
    "qiskit-quimb==0.0.10" \
    "quimb==1.13.0" \
    "cotengra==0.7.5" \
    "autoray==0.8.10" \
    "pandas==2.3.3" \
    "scipy==1.17.1"

# Better contraction-path search for the amplitude oracle (optional, tolerant).
RUN pip install kahypar optuna || \
    echo "kahypar/optuna not installed (optional) — cotengra falls back to random sampling."

# NOTE: we deliberately DO NOT install cuquantum-python-cu13 / cupy-cuda13x.
# The solver uses the torch backend exclusively (it never imports cupy/cuquantum),
# but having them installed lets quimb/cotengra/autoray auto-detect and import cupy
# at startup -- a large, CUDA-13-pinned, DRIVER-SENSITIVE stack that can fail to
# initialise on a validator whose NVIDIA driver doesn't match cu130, crashing the
# run before it starts. Omitting it removes that startup failure surface, shrinks
# the image (~0.5-1 GB), and speeds the (uncached) validator build.

# --- Non-root runtime user (validator runs --user + read-only rootfs) ---
RUN useradd -r -s /sbin/nologin -d /app -U miner

WORKDIR /app
# One catch-all COPY of the whole submission context: the vendored Apache-2.0
# reference code (utils.py, circuit_mpo.py, unswap.py + LICENSE/NOTICE), the Enigma
# stdout-protocol modules (_hqp_*.py) and our orchestrator (hardening_quantum_proof.py).
# A single COPY is deliberately robust -- the build cannot fail on one missing source
# file -- and .dockerignore keeps test-only/scratch files (tools/, __pycache__, logs)
# out of the image. Paths are relative, so this passes the validator's Dockerfile policy.
COPY --chown=miner:miner . /app/

# Default wall clock; the validator overrides WALL_TIME as needed.
ENV WALL_TIME=14400 \
    DEADLINE_MARGIN=180

USER miner
ENTRYPOINT ["python", "/app/hardening_quantum_proof.py"]
