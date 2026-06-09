FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc make git libgmp-dev zlib1g-dev \
    autoconf automake libtool m4 \
    && rm -rf /var/lib/apt/lists/*

# GMP-ECM (YAFU dependency; also drives YAFU's ECM stage).
RUN git clone --depth 1 https://gitlab.inria.fr/zimmerma/ecm.git /tmp/ecm-src \
    && cd /tmp/ecm-src \
    && autoreconf -i && ./configure && make -j"$(nproc)" && make install && ldconfig \
    && rm -rf /tmp/ecm-src

# YAFU (bundles ytools/ysieve/msieve). SIQS + ECM engine.
RUN git clone --depth 1 https://github.com/bbuhrow/yafu.git /opt/yafu \
    && cd /opt/yafu \
    && make -f Makefile.gcc yafu USE_SSE41=1 ECM=1 \
    && test -x /opt/yafu/yafu

RUN pip install --no-cache-dir gmpy2

RUN useradd -m -u 1000 -s /usr/sbin/nologin miner

WORKDIR /app
COPY enigma_challenges /app/enigma_challenges/
COPY breaking_rsa.py /app/

ENV YAFU_BIN=/opt/yafu/yafu \
    HOME=/tmp \
    TMPDIR=/tmp \
    PYTHONUNBUFFERED=1

USER miner
ENTRYPOINT ["python", "/app/breaking_rsa.py"]
