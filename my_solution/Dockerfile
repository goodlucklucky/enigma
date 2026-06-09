FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Runtime libs for the precompiled CADO-NFS binaries (libhwloc15, libgomp1) plus
# a toolchain to build GMP-ECM and the small ramnfs broker/shim.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    build-essential gcc g++ make \
    autoconf automake libtool m4 perl \
    libgmp-dev libhwloc15 libhwloc-dev libgomp1 \
    zlib1g-dev git \
    && rm -rf /var/lib/apt/lists/*

# gmpy2 for the solver; flask + requests are required by CADO-NFS master's
# cadofactor orchestrator (api_server) and its clients.
RUN pip install --no-cache-dir --break-system-packages gmpy2 flask requests

# GMP-ECM for the Stage 1 ECM pretest (cheap insurance against a weak/unbalanced
# modulus before committing to GNFS).
RUN git clone --depth 1 https://gitlab.inria.fr/zimmerma/ecm.git /tmp/ecm-src \
    && cd /tmp/ecm-src \
    && autoreconf -i && ./configure CFLAGS="-O2" \
    && make -j"$(nproc)" && make install && ldconfig \
    && cp ecm /usr/local/bin/ecm \
    && rm -rf /tmp/ecm-src

# Precompiled CADO-NFS (compiled for ubuntu:24.04 / glibc 2.39 at /opt/cado-nfs,
# matching the extraction path so the build-tree cado-nfs.py resolves its
# parameter files via source-location.txt). Shipping the binaries avoids the
# long from-source cmake build that can exceed the image build timeout. flask is
# already installed, so the --help smoke test imports cleanly.
COPY cado-nfs.tar.gz /tmp/
RUN tar xzf /tmp/cado-nfs.tar.gz -C / \
    && rm /tmp/cado-nfs.tar.gz \
    && chmod +x /opt/cado-nfs/build/release/sieve/las \
                /opt/cado-nfs/build/release/polyselect/polyselect \
    && python3 /opt/cado-nfs/build/release/cado-nfs.py --help > /dev/null 2>&1

# ramnfs: broker daemon + LD_PRELOAD shim. Routes CADO-NFS file I/O under
# /ramwork to memfd_create RAM-backed files, bypassing the validator's small
# (~1 GB) noexec /tmp tmpfs — no mount, no root, no extra privileges.
COPY ramnfs/broker.c ramnfs/shim.c /opt/ramnfs/
RUN cd /opt/ramnfs \
    && gcc -O2 -pthread -o broker broker.c -lpthread \
    && gcc -O2 -fPIC -shared -o shim.so shim.c -ldl -lpthread \
    && chmod +x broker

# ubuntu:24.04 ships a UID-1000 "ubuntu" user; remove it so --user miner is UID 1000.
RUN userdel -r ubuntu 2>/dev/null; useradd -m -u 1000 -s /usr/sbin/nologin miner

WORKDIR /app
COPY enigma_challenges /app/enigma_challenges/
COPY breaking_rsa.py /app/

ENV ECM_BIN=/usr/local/bin/ecm \
    CADO_NFS=/opt/cado-nfs/build/release/cado-nfs.py \
    RAMNFS_BROKER=/opt/ramnfs/broker \
    RAMNFS_SHIM=/opt/ramnfs/shim.so \
    RAMNFS_SOCK=/tmp/ramnfs.sock \
    RAMNFS_WORKDIR=/ramwork/factor.work \
    HOME=/tmp \
    TMPDIR=/tmp \
    WALL_TIME=14400 \
    DEADLINE_MARGIN=120 \
    ECM_PRETEST_CAP=60 \
    PYTHONUNBUFFERED=1

USER miner
ENTRYPOINT ["python3", "/app/breaking_rsa.py"]
