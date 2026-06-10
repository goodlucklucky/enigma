#!/usr/bin/env python
"""
Thin wrapper so you can run:

    python cli/upload_validator_diagnostics.py --wallet.name validator --wallet.hotkey <yourhotkey>

from the repo root (same as the other top-level CLIs).

Includes SQLite .db + .db-wal + .db-shm automatically (WAL mode).
"""
from qbittensor.cli.validator.upload_diagnostics import main

if __name__ == "__main__":
    main()
