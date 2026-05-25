# Build context: this directory (contains timeout.py).
#
#   docker build -t enigma-timeout-test .
#   docker run --rm enigma-timeout-test
#
# Stop with Ctrl+C or: docker stop <container>

FROM python:3.12-slim-bookworm

WORKDIR /app

COPY timeout.py /app/timeout.py

ENTRYPOINT ["python3", "/app/timeout.py"]
