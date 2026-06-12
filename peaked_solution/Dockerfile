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

# Optional cuQuantum/cuTensorNet acceleration for contraction paths. Kept in a
# separate, failure-tolerant layer so the image still builds if a wheel is
# unavailable for the target platform.
RUN pip install cuquantum-python-cu13 cupy-cuda13x || \
    echo "cuquantum/cupy not installed (optional) — quimb will use the torch backend."

# --- Non-root runtime user (validator runs --user + read-only rootfs) ---
RUN useradd -r -s /sbin/nologin -d /app -U miner

WORKDIR /app
# Vendored verbatim from github.com/d-kremer/peaked-circuit-simulation (Apache-2.0)
COPY --chown=miner:miner utils.py circuit_mpo.py unswap.py /app/
COPY --chown=miner:miner LICENSE-Apache-2.0 NOTICE /app/
# Enigma stdout protocol + our orchestrator
COPY --chown=miner:miner _hqp_serde.py _hqp_types.py _hqp_output.py /app/
COPY --chown=miner:miner hardening_quantum_proof.py /app/
# Test tooling (circuit generator + harness) so the image is self-testing on any
# machine. Ignored by the validator, which just runs the ENTRYPOINT on the qasm.
COPY --chown=miner:miner tools /app/tools/

# Default wall clock; the validator overrides WALL_TIME as needed.
ENV WALL_TIME=14400 \
    DEADLINE_MARGIN=180

USER miner
ENTRYPOINT ["python", "/app/hardening_quantum_proof.py"]
