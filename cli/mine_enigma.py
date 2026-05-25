#!/usr/bin/env python
"""Thin launcher for the miner CLI.

This allows running the tool directly with:

    python cli/mine_enigma.py

It provides the same reliable argv0-based database root detection
as the neuron entrypoints in neurons/.
"""

from qbittensor.cli.miner.mine_enigma import main

if __name__ == "__main__":
    main()
