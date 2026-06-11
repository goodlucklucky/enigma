FROM python:3.12-slim

RUN pip install --no-cache-dir \
    "numpy>=1.23" \
    "qiskit>=1.0" \
    "qiskit-aer>=0.15" \
    "qiskit-qasm3-import>=0.6.0"

# Create non-root user for runtime (required by validator/workbench hardening)
RUN useradd -r -s /sbin/nologin -d /app -U miner

WORKDIR /app
COPY --chown=miner:miner enigma_challenges /app/enigma_challenges/
COPY --chown=miner:miner hardening_quantum_proof.py /app/

USER miner
ENTRYPOINT ["python", "/app/hardening_quantum_proof.py"]
