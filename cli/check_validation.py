#!/usr/bin/env python
"""Thin launcher for the check-validation CLI tool.

This allows running the tool directly with:

    python cli/check_validation.py

It is also exposed as the console script "check-validation" after
`pip install -e .`.
"""

from qbittensor.cli.validator.check_validation import main

if __name__ == "__main__":
    main()
