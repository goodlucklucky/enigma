FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Toolchain + runtime libs. libgmp-dev provides gmpxx.h/libgmpxx for the C++
# orchestrator; zlib1g-dev provides crc32 for the zip writer; libhwloc15/libgomp1
# are runtime deps of the precompiled CADO binaries.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    build-essential g++ gcc make \
    autoconf automake libtool m4 perl \
    libgmp-dev libhwloc15 libhwloc-dev libgomp1 \
    zlib1g-dev git \
    && rm -rf /var/lib/apt/lists/*

# CADO-NFS master's Python orchestrator (cado-nfs.py) still needs flask + requests.
RUN pip install --no-cache-dir --break-system-packages flask requests

# GMP-ECM for the Stage 1 ECM pretest.
RUN git clone --depth 1 https://gitlab.inria.fr/zimmerma/ecm.git /tmp/ecm-src \
    && cd /tmp/ecm-src \
    && autoreconf -i && ./configure CFLAGS="-O2" \
    && make -j"$(nproc)" && make install && ldconfig \
    && cp ecm /usr/local/bin/ecm \
    && rm -rf /tmp/ecm-src

# Precompiled CADO-NFS (avoids the long source build).
COPY cado-nfs.tar.gz /tmp/
RUN tar xzf /tmp/cado-nfs.tar.gz -C / \
    && rm /tmp/cado-nfs.tar.gz \
    && chmod +x /opt/cado-nfs/build/release/sieve/las \
                /opt/cado-nfs/build/release/polyselect/polyselect \
    && python3 /opt/cado-nfs/build/release/cado-nfs.py --help > /dev/null 2>&1

# ramnfs: broker + LD_PRELOAD shim (RAM scratch beyond the 1 GB /tmp). The shim
# is used until the CADO source patch (memfd-aware I/O) replaces it.
COPY ramnfs/broker.c ramnfs/shim.c /opt/ramnfs/
RUN cd /opt/ramnfs \
    && gcc -O2 -pthread -o broker broker.c -lpthread \
    && gcc -O2 -fPIC -shared -o shim.so shim.c -ldl -lpthread \
    && chmod +x broker

# The all-C/C++ orchestrator.
WORKDIR /app
COPY breaking_rsa.cpp /app/
RUN g++ -O2 -std=c++17 /app/breaking_rsa.cpp -o /app/breaking_rsa -lgmpxx -lgmp -lz \
    && test -x /app/breaking_rsa

# ubuntu:24.04 ships a UID-1000 "ubuntu" user; remove it so --user miner is UID 1000.
RUN userdel -r ubuntu 2>/dev/null; useradd -m -u 1000 -s /usr/sbin/nologin miner

ENV ECM_BIN=/usr/local/bin/ecm \
    CADO_NFS=/opt/cado-nfs/build/release/cado-nfs.py \
    RAMNFS_BROKER=/opt/ramnfs/broker \
    RAMNFS_SHIM=/opt/ramnfs/shim.so \
    RAMNFS_SOCK=/tmp/ramnfs.sock \
    RAMNFS_WORKDIR=/ramwork/factor.work \
    RAMNFS_MODE=shim \
    HOME=/tmp \
    TMPDIR=/tmp \
    WALL_TIME=14400 \
    DEADLINE_MARGIN=120 \
    ECM_PRETEST_CAP=60 \
    PYTHONUNBUFFERED=1

USER miner
ENTRYPOINT ["/app/breaking_rsa"]
