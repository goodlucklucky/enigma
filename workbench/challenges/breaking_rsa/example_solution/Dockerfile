FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgmp-dev zlib1g-dev git nasm \
    autoconf automake libtool m4 \
    && rm -rf /var/lib/apt/lists/*

# Build msieve from source (SIQS factoring engine)
RUN git clone https://github.com/radii/msieve.git /tmp/msieve-src \
    && cd /tmp/msieve-src \
    && sed -i 's/-march=athlon-xp/-march=native/g' Makefile \
    && sed -i '/#include <util.h>/a #include <sys/time.h>' common/util.c \
    && make x86_64 \
    && cp msieve /usr/local/bin/msieve \
    && rm -rf /tmp/msieve-src

# Build GMP-ECM from source
RUN git clone https://gitlab.inria.fr/zimmerma/ecm.git /tmp/ecm-src \
    && cd /tmp/ecm-src \
    && autoreconf -i \
    && ./configure \
    && make -j$(nproc) \
    && make install \
    && ldconfig \
    && rm -rf /tmp/ecm-src

# Install Python dependencies
RUN pip install --no-cache-dir gmpy2

WORKDIR /app
COPY enigma_challenges /app/enigma_challenges/
COPY breaking_rsa.py /app/

ENV MSIEVE_BIN=/usr/local/bin/msieve
ENV ECM_BIN=/usr/local/bin/ecm

ENTRYPOINT ["python", "/app/breaking_rsa.py"]
