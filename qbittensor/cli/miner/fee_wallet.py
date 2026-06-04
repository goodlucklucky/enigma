# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

"""
Fee wallet / coldkey loading for Enigma miner submissions.

This module provides the only supported ways to load the coldkey that will pay
the on-chain TAO fee for a submission (the "fee payer" coldkey).

Supported mechanisms:
1. Standard Bittensor wallet name (recommended for most users).
2. Direct path to a coldkey keyfile (best for automation and custom setups).

Mnemonics are intentionally NOT supported here. Fee payment must use proper
encrypted keyfiles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bittensor as bt
from bittensor_wallet import Keyfile, Keypair


def load_fee_keypair_from_wallet(
    wallet_name: str,
    wallet_path: str | None = None,
) -> Keypair:
    """
    Load the coldkey keypair from a standard Bittensor wallet.

    This is the preferred method for interactive use.

    Args:
        wallet_name: Name of the wallet (coldkey directory).
        wallet_path: Optional custom path to the wallets directory.
                     If None, uses the default Bittensor location.

    Returns:
        The coldkey Keypair (ready for signing).

    Raises:
        click.ClickException on any failure (file not found, wrong password, etc.).
    """
    import click

    try:
        wallet = bt.Wallet(name=wallet_name, hotkey="default", path=wallet_path)
        # Accessing .coldkey forces the keyfile to be loaded (prompts for password if encrypted)
        keypair: Keypair = wallet.coldkey
        return keypair
    except Exception as e:
        # Provide a clear, actionable error
        raise click.ClickException(
            f"Failed to load fee wallet '{wallet_name}'.\n"
            f"Error: {e}\n\n"
            "Make sure the wallet exists and you can unlock it with its password "
            "(or set the WALLET_PASSWORD environment variable)."
        ) from e


def load_fee_keypair_from_keyfile(
    path: str | Path,
) -> Keypair:
    """
    Load a coldkey directly from a keyfile path.

    Useful for automation, custom keyfile locations, or when the operator
    wants to point at a specific encrypted coldkey file.

    Args:
        path: Path to the coldkey keyfile (e.g. ~/.bittensor/wallets/my-wallet/coldkey).

    Returns:
        The Keypair from the keyfile.
    """
    import click

    path = Path(path).expanduser().resolve()

    if not path.is_file():
        raise click.ClickException(f"Fee coldkey file not found: {path}")

    try:
        keyfile = Keyfile(path=str(path))
        # .keypair will prompt for password if the keyfile is encrypted
        keypair: Keypair = keyfile.keypair
        return keypair
    except Exception as e:
        raise click.ClickException(
            f"Failed to load fee coldkey from {path}.\n"
            f"Error: {e}\n\n"
            "If the keyfile is password-protected, make sure you can unlock it "
            "(WALLET_PASSWORD env var is supported by bittensor_wallet)."
        ) from e
