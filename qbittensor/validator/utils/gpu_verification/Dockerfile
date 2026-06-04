# Minimal CUDA GPU smoke test.
#
# Requires: NVIDIA driver + NVIDIA Container Toolkit on the host.
#
# Build:
#   docker build -t cuda-gpu-poc .
#
# Run:
#   docker run --rm --gpus all cuda-gpu-poc

FROM nvidia/cuda:12.6.0-devel-ubuntu22.04

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && pip3 install --no-cache-dir "numba>=0.60.0" "numpy>=1.26.0" \
    && rm -rf /var/lib/apt/lists/*

COPY main.py /app/main.py

ENTRYPOINT ["python3", "/app/main.py"]
