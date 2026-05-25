# Build context: this directory (contains network.py).
#
#   docker build -t enigma-network-test .
#   docker run --rm enigma-network-test
#
# Optional: override target (still must be reachable from the host network).
#   docker run --rm -e NETWORK_TEST_URL=https://httpbin.org/get enigma-network-test

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY network.py /app/network.py

ENTRYPOINT ["python3", "/app/network.py"]
