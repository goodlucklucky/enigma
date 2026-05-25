# Zip layout (build context = directory that contains this Dockerfile):
#
#   Dockerfile
#   mock_solution.py
#
# ``mock_solution.py`` is self-contained (no COPY of repo ``qbittensor/``).
#
# Build:
#   docker build -t enigma-mock-example .
#
# Run:
#   docker run --rm enigma-mock-example > out.bin
#
# Output contract: the miner writes text logs to stderr, then the
# ``SOLUTION_OUTPUT_SEPARATOR`` line to stdout, then a raw zip of the
# solution artifacts to stdout. No volumes are mounted by the validator —
# the validator captures stdout via ``docker logs`` after the container
# exits and splits it into logs and the solution-output zip on its own host
# workspace. The validator's read-only-rootfs + ``--tmpfs /tmp`` flags still
# apply, so the miner can use ``/tmp`` for scratch space but writes its
# final output exclusively to stdout.

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir "cryptography>=42.0.0" \
    && useradd -r -s /sbin/nologin -d /app -U miner

COPY --chown=miner:miner mock_solution.py /app/mock_solution.py

USER miner
ENTRYPOINT ["python3", "/app/mock_solution.py"]
