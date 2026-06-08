FROM python:3.12-slim

# ---------------------------------------------------------------------------
# Build dependencies for GMP-ECM and CADO-NFS.
#   - build-essential/cmake/git: compile both projects (CADO needs cmake>=3.18,
#     Debian bookworm ships 3.25; gcc-12 satisfies CADO's gcc>=10).
#   - libgmp-dev: core bignum lib used by both.
#   - libhwloc-dev: lets CADO pin sieving threads to cores (throughput).
#   - perl: required by parts of the CADO build/run scripts.
#   - util-linux: provides `unshare` + `mount`, used at runtime to self-provision
#     a large RAM tmpfs in an unprivileged user namespace (the GNFS scratch
#     does not fit the validator's 256 MB /tmp). Essential in Debian but pinned
#     here explicitly so a future slim base cannot drop it.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ make cmake git \
    libgmp-dev zlib1g-dev libhwloc-dev \
    autoconf automake libtool m4 perl \
    util-linux mount \
    && rm -rf /var/lib/apt/lists/*

# GMP-ECM from source: provides the `ecm` binary (Stage 0.5 pre-test) and
# libecm, which CADO-NFS uses for batch cofactorization during sieving.
ARG ECM_REF=master
RUN git clone --depth 1 --branch "$ECM_REF" https://gitlab.inria.fr/zimmerma/ecm.git /tmp/ecm-src \
    && cd /tmp/ecm-src \
    && autoreconf -i && ./configure --enable-shared && make -j"$(nproc)" && make install && ldconfig \
    && cp /tmp/ecm-src/ecm /usr/local/bin/ecm 2>/dev/null || true \
    && rm -rf /tmp/ecm-src

# Python deps that CADO-NFS's orchestrator (cado-nfs.py) needs, plus gmpy2 for
# the solver wrapper. Installed into the image's python (3.12), which is the
# interpreter the wrapper uses to launch cado-nfs.py.
RUN pip install --no-cache-dir requests flask gmpy2

# CADO-NFS — the GNFS engine. Cloned from the canonical Inria GitLab repo
# (GitHub mirror: https://github.com/cado-nfs/cado-nfs). CADO_REF is a build-arg
# so the image is reproducibly pinnable to a tag/commit, e.g.
#   docker build --build-arg CADO_REF=<commit-sha> .
# `make` wraps cmake; cado-nfs.py auto-detects the build dir under build/.
ARG CADO_REF=master
RUN git clone https://gitlab.inria.fr/cado-nfs/cado-nfs.git /opt/cado-nfs \
    && cd /opt/cado-nfs \
    && git checkout "$CADO_REF" \
    # Build, then `make install` to a FIXED prefix. The in-build-tree launcher
    # locates binaries via build/$(hostname); that breaks when the runtime
    # hostname differs from the build host (always true in the validator). The
    # installed layout under /opt/cado/{bin,share} is hostname-independent.
    && echo 'PREFIX="/opt/cado"' > local.sh \
    && make -j"$(nproc)" \
    && make install \
    && test -f /opt/cado/bin/cado-nfs.py \
    # build-time smoke factorization via the INSTALLED binary (correctness gate)
    && /opt/cado/bin/cado-nfs.py 90377629292003121684002147101760858109247336549001090677693 -t 2 \
       | tee /tmp/cado_smoke.txt \
    && grep -Eq '[0-9]+ [0-9]+' /tmp/cado_smoke.txt \
    && rm -f /tmp/cado_smoke.txt

# ---------------------------------------------------------------------------
# OPTIONAL — GPU polynomial selection (the RTX PRO 6000 the CPU-only engines
# leave idle). GNFS *sieving* is CPU-only in every production tool, so the GPU
# only accelerates poly-select (and, in msieve, linear algebra). To enable it,
# install the CUDA toolkit and rebuild CADO with NVCC, then run with
# USE_GPU_POLYSELECT=1. Left commented so the baseline image stays buildable and
# correct without CUDA:
#
#   RUN apt-get update && apt-get install -y --no-install-recommends nvidia-cuda-toolkit
#   RUN cd /opt/cado-nfs && cmake -B build-gpu -DENABLE_CUDA=ON . && cmake --build build-gpu
#   ENV CADO_GPU_DIR=/opt/cado-nfs/build-gpu
# ---------------------------------------------------------------------------

# The validator runs containers as a NON-ROOT account named "miner"
# (docker run --user miner). That account must exist or the container won't start.
RUN useradd -m -u 1000 -s /usr/sbin/nologin miner

WORKDIR /app
COPY enigma_challenges /app/enigma_challenges/
COPY breaking_rsa.py /app/

# Root filesystem is mounted read-only at runtime; the only pre-existing writable
# path is the 256 MB tmpfs at /tmp. HOME/TMPDIR point there for small control
# files; the multi-GB GNFS scratch is provisioned at runtime into RAM (see
# breaking_rsa.py: provision_scratch). CADO_NFS pins the orchestrator path.
ENV CADO_NFS=/opt/cado/bin/cado-nfs.py \
    HOME=/tmp \
    TMPDIR=/tmp \
    RAMDISK_SIZE_GB=60 \
    SCRATCH_MIN_GB=3 \
    USE_USERNS_RAMDISK=1 \
    USE_GPU_POLYSELECT=0 \
    ECM_PRETEST=1 \
    PYTHONUNBUFFERED=1

USER miner
ENTRYPOINT ["python", "/app/breaking_rsa.py"]
