FROM python:3.12-slim

# Build dependencies for GMP-ECM and CADO-NFS.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ make cmake git \
    libgmp-dev zlib1g-dev libhwloc-dev \
    autoconf automake libtool m4 perl \
    && rm -rf /var/lib/apt/lists/*

# GMP-ECM from source — provides libecm, which CADO-NFS uses for batch
# cofactorization during sieving (faster relation collection).
ARG ECM_REF=master
RUN git clone --depth 1 --branch "$ECM_REF" https://gitlab.inria.fr/zimmerma/ecm.git /tmp/ecm-src \
    && cd /tmp/ecm-src \
    && autoreconf -i && ./configure --enable-shared && make -j"$(nproc)" && make install && ldconfig \
    && rm -rf /tmp/ecm-src

# Python deps for the CADO orchestrator (cado-nfs.py) plus gmpy2 for the solver.
RUN pip install --no-cache-dir requests flask gmpy2

# CADO-NFS (canonical Inria GitLab; GitHub mirror github.com/cado-nfs/cado-nfs).
# CADO_REF is a build-arg for reproducible pinning. `make install` to a fixed
# prefix is required: the in-build-tree launcher locates binaries via
# build/$(hostname), which breaks when the runtime hostname differs from the
# build host (always true on the validator). A build-time smoke factorization
# via the installed binary gates the build.
ARG CADO_REF=master
RUN git clone https://gitlab.inria.fr/cado-nfs/cado-nfs.git /opt/cado-nfs \
    && cd /opt/cado-nfs && git checkout "$CADO_REF" \
    && echo 'PREFIX="/opt/cado"' > local.sh \
    && make -j"$(nproc)" && make install \
    && test -f /opt/cado/bin/cado-nfs.py \
    && /opt/cado/bin/cado-nfs.py 90377629292003121684002147101760858109247336549001090677693 -t 2 \
       | grep -Eq '[0-9]+ [0-9]+'

# The validator runs containers as the non-root account "miner".
RUN useradd -m -u 1000 -s /usr/sbin/nologin miner

WORKDIR /app
COPY enigma_challenges /app/enigma_challenges/
COPY breaking_rsa.py /app/

# HOME/TMPDIR point at /tmp (the writable path). The GNFS scratch is several GB
# at full size; if the runtime /tmp is too small, point CADO_WORKDIR at a larger
# writable path. CADO_NFS pins the orchestrator location.
ENV CADO_NFS=/opt/cado/bin/cado-nfs.py \
    HOME=/tmp \
    TMPDIR=/tmp \
    SCRATCH_MIN_GB=3 \
    PYTHONUNBUFFERED=1

USER miner
ENTRYPOINT ["python", "/app/breaking_rsa.py"]
