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

"""Enigma miner CLI — welcome and challenges browser.

Zip upload uses ``POST .../submissions/upload`` (slot) plus the presigned storage upload.
This CLI does **not** call ``POST .../challenges/milestones/{milestone_id}/submissions``
(validator synapse handler only).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from qbittensor.utils.env import get_api_config
from qbittensor.utils.services.challenges import ChallengesClient

_api_cfg = get_api_config()

import bittensor as bt
import click
import requests
from bittensor_wallet import Keypair

from qbittensor.database.db_connection import DBConnection
from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from qbittensor.cli.miner.utils.constants import MINER_DB_TABLE_PREFIX
from qbittensor.cli.miner.fee_wallet import (
    load_fee_keypair_from_keyfile,
    load_fee_keypair_from_wallet,
)
from qbittensor.cli.miner.tao_transfer import transfer_tao_for_submission
from qbittensor.utils.time import timestamp
from qbittensor.utils.transfer_proof import TRANSFER_DEST_SS58

from qbittensor.cli.miner.utils.color import c

try:
    import termios
    import tty

    _HAVE_TERMIOS = True
except ImportError:
    _HAVE_TERMIOS = False

KEY_UP = "\x1b[A"
KEY_DOWN = "\x1b[B"
KEY_LEFT = "\x1b[D"
KEY_RIGHT = "\x1b[C"
KEY_ENTER = "\r"
KEY_ENTER_ALT = "\n"
KEY_Q = "q"
KEY_Q_UPPER = "Q"


@dataclass(frozen=True)
class CliApiAuth:
    """Wallet + network used to build ``RequestManager`` for signed challenge API calls."""

    wallet_name: str
    wallet_hotkey: str
    network: str
    netuid: int
    wallet_path: str | None = None  # optional custom wallets directory


def _resolve_cli_netuid(netuid: int | None) -> int:
    if netuid is not None:
        return netuid
    env_val = (os.getenv("NETUID") or "63").strip()
    try:
        return int(env_val)
    except ValueError as e:
        raise click.ClickException(f"Invalid NETUID env value: {env_val!r}") from e


def _resolve_cli_api_auth(
    wallet_name: str | None,
    wallet_hotkey: str | None,
    network: str | None = None,
    netuid: int | None = None,
    wallet_path: str | None = None,
) -> CliApiAuth:
    """
    Resolve the wallet used for:
      - Platform API authentication (JWT signing via TensorAuth)
      - Fee payment coldkey (by default)
      - Miner hotkey identity (derived from the hotkey in this wallet)
    """
    cold = (
        wallet_name
        or os.getenv("WALLET_NAME")
        or os.getenv("BUY_WALLET_COLDKEY")
        or "default"
    ).strip() or "default"

    hot = (
        wallet_hotkey
        or os.getenv("WALLET_HOTKEY")
        or os.getenv("BUY_WALLET_HOTKEY")
        or "default"
    ).strip() or "default"

    net = (network or os.getenv("NETWORK") or "finney").strip() or "finney"

    return CliApiAuth(
        wallet_name=cold,
        wallet_hotkey=hot,
        network=net,
        netuid=_resolve_cli_netuid(netuid),
        wallet_path=wallet_path,
    )


def _prompt_for_keyfile_path(console: Console) -> Path | None:
    """Ask the user for a keyfile path on disk; returns the expanded ``Path`` or ``None`` to cancel."""
    console.print(
        _styled_panel(
            "Hotkey keyfile not found",
            Text.assemble(
                ("Enter the full path to your ", f"dim {c(3)}"),
                ("hotkey keyfile", f"bold {c(0)}"),
                (" used to sign challenges API requests.", f"dim {c(3)}"),
                ("\n", ""),
                ("Leave empty to cancel.", f"dim {c(3)}"),
            ),
            border=f"bold {c(4)}",
        )
    )
    if _HAVE_TERMIOS and sys.stdin.isatty():
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except OSError:
            pass
    raw = (
        Prompt.ask(
            Text.assemble(("Path to keyfile", f"dim {c(3)}")),
            console=console,
            default="",
            show_default=False,
        )
        or ""
    ).strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _load_signing_keypair(console: Console, auth: CliApiAuth) -> bt.Keypair:
    """Return the keypair ``RequestManager`` uses to sign API requests.

    Prefers ``MINER_KEYFILE_PATH`` from the environment (e.g. from ``.env``)
    when that path exists. Otherwise falls back to the on-disk wallet.
    """
    env_key = (os.getenv("MINER_KEYFILE_PATH") or "").strip()
    env_path = Path(env_key).expanduser() if env_key else None
    if env_path is not None and env_path.is_file():
        try:
            return bt.Keyfile(path=str(env_path)).keypair
        except Exception as e:
            console.print(
                _styled_panel(
                    "Failed to load keyfile",
                    f"MINER_KEYFILE_PATH={env_path}\n{e}",
                    border=f"bold {c(4)}",
                )
            )

    wallet = bt.Wallet(name=auth.wallet_name, hotkey=auth.wallet_hotkey, path=auth.wallet_path)
    if wallet.hotkey_file.exists_on_device():
        return wallet.hotkey
    while True:
        path = _prompt_for_keyfile_path(console)
        if path is None:
            raise click.ClickException(
                "No hotkey keyfile provided; cannot sign challenges API requests."
            )
        if not path.is_file():
            console.print(
                _styled_panel(
                    "Invalid keyfile path",
                    f"Not a file: {path}",
                    border=f"bold {c(4)}",
                )
            )
            continue
        try:
            return bt.Keyfile(path=str(path)).keypair
        except Exception as e:
            console.print(
                _styled_panel(
                    "Failed to load keyfile",
                    str(e),
                    border=f"bold {c(4)}",
                )
            )
            continue


def _challenges_client_for_api(
    auth: CliApiAuth, console: Console
) -> ChallengesClient:
    """Create an authenticated ChallengesClient for the challenges platform API.

    The client owns its own RequestManager (pointed at the challenges base URL).
    netuid is forwarded to the JWT layer for the signed token claim.
    """
    from qbittensor.utils.services.challenges import ChallengesClient

    keypair = _load_signing_keypair(console, auth)
    return ChallengesClient(
        keypair=keypair,
        base_url=_api_cfg.challenges_api_url,
        tensorauth_url=_api_cfg.tensorauth_url,
        netuid=auth.netuid,
    )


def _parse_api_datetime(raw: Any) -> datetime:
    """Parse ``start_date`` / ``end_date`` values from the challenges API."""
    if raw is None:
        raise ValueError("missing datetime")
    text = str(raw).strip()
    if not text:
        raise ValueError("empty datetime")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_api_datetime_display(raw: Any) -> str:
    if not raw:
        return "—"
    try:
        return _parse_api_datetime(raw).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(raw).replace("T", " ")


def _milestone_status(milestone: dict[str, Any]) -> str:
    raw = milestone.get("status")
    if raw is None:
        return ""
    return str(raw).strip()


def _format_milestone_status_display(milestone: dict[str, Any]) -> str:
    status = _milestone_status(milestone)
    if not status or status.lower() == "incomplete":
        return "Incomplete"
    return status


def _assert_milestone_status_incomplete(milestone: dict[str, Any]) -> None:
    """Reject submission unless the milestone status is ``Incomplete``."""
    milestone_id = milestone.get("id", "?")
    status = _milestone_status(milestone)
    if status.lower() == "incomplete":
        return
    if status.lower() == "validating":
        raise click.ClickException(
            f"Milestone {milestone_id} is not open for submissions (status: {status!r}). "
            "A potential solution is currently being validated. You may be able to try again later."
        )
    if status.lower() == "complete":
        raise click.ClickException(
            f"Milestone {milestone_id} is not open for submissions (status: {status!r}). "
            "This milestone has been successfully solved."
        )
    if status:
        raise click.ClickException(
            f"Milestone {milestone_id} is not open for submissions (status: {status!r}). "
            "Only milestones with status 'Incomplete' accept submissions."
        )
    raise click.ClickException(
        f"Milestone {milestone_id} is missing a status; "
        "only milestones with status 'Incomplete' accept submissions."
    )


def _assert_submission_window_open(milestone: dict[str, Any]) -> None:
    """Reject submission when the current time is outside the milestone window.

    Null or empty ``start_date`` / ``end_date`` are treated as unbounded:
    - missing start_date means submissions are allowed from any time in the past
    - missing end_date means the submission window never closes
    """
    milestone_id = milestone.get("id", "?")
    start_raw = milestone.get("start_date")
    end_raw = milestone.get("end_date")

    start: datetime | None = None
    if start_raw is not None:
        text = str(start_raw).strip()
        if text:
            try:
                start = _parse_api_datetime(text)
            except ValueError as e:
                raise click.ClickException(
                    f"Milestone {milestone_id} has an invalid start_date."
                ) from e

    end: datetime | None = None
    if end_raw is not None:
        text = str(end_raw).strip()
        if text:
            try:
                end = _parse_api_datetime(text)
            except ValueError as e:
                raise click.ClickException(
                    f"Milestone {milestone_id} has an invalid end_date."
                ) from e

    now = timestamp()

    if start is not None and now < start:
        raise click.ClickException(
            "Submissions are not open yet for this milestone. "
            f"Opens at {start.isoformat()} (current time: {now.isoformat()})."
        )
    if end is not None and now > end:
        raise click.ClickException(
            "The submission window for this milestone has closed. "
            f"Ended at {end.isoformat()} (current time: {now.isoformat()})."
        )


def _assert_milestone_allows_submission(milestone: dict[str, Any]) -> None:
    """Reject submission when milestone status or submission window is invalid."""
    _assert_milestone_status_incomplete(milestone)
    _assert_submission_window_open(milestone)


def _confirm_fee_amount_before_unlock(
    console: Console,
    challenges_client: "ChallengesClient",
    milestone_id: str,
    challenge_id: str,
) -> float | None:
    """
    Fetch the current submission fee (priceTao) for the milestone and ask the
    user for explicit confirmation before we attempt to load the fee-paying
    coldkey.

    This ensures the password prompt (for encrypted coldkeys) only happens
    after the operator has seen and approved the exact amount that will be
    transferred on-chain.

    Returns:
        The fee amount in TAO if the user confirms.
        None if the user declines (caller should treat this as cancellation).
    """
    try:
        price_tao = challenges_client.get_milestone_price_tao(
            challenge_id=challenge_id, milestone_id=milestone_id
        )
    except Exception as e:
        raise click.ClickException(f"Failed to fetch fee amount for milestone: {e}") from e

    dest = TRANSFER_DEST_SS58
    console.print(
        _styled_panel(
            "Confirm fee transfer",
            Text.assemble(
                ("You are about to pay a submission fee of ", f"dim {c(3)}"),
                (f"{price_tao} TAO", f"bold {c(0)}"),
                ("\n", ""),
                ("to the Enigma fee destination:", f"dim {c(3)}"),
                ("\n", ""),
                (dest, f"bold {c(2)}"),
                ("\n\n", ""),
                ("This fee is non-refundable. Continue?", f"bold {c(3)}"),
            ),
            border=f"bold {c(1)}",
        )
    )

    if _HAVE_TERMIOS and sys.stdin.isatty():
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except OSError:
            pass

    confirm = (
        Prompt.ask(
            Text.assemble(("Proceed?", f"dim {c(3)}")),
            console=console,
            default="n",
            show_default=True,
        )
        or ""
    ).strip().lower()

    if confirm not in ("y", "yes"):
        console.print(_styled_panel("Cancelled", "Transfer not confirmed by user.", border=f"dim {c(3)}"))
        return None

    return price_tao


def _styled_panel(
    title: str,
    body: str | Text | RenderableType,
    *,
    border: str | None = None,
    subtitle: str | None = None,
) -> Panel:
    """Wrap content in a titled panel. Pass ``Syntax``, ``Group``, etc. as ``body`` unchanged."""
    b = border or f"dim {c(4)}"
    if isinstance(body, Text):
        renderable: RenderableType = body
    elif isinstance(body, str):
        renderable = Text(body, style=f"dim {c(3)}")
    else:
        renderable = body
    return Panel(
        renderable,
        title=f"[bold {c(1)}]{title}[/bold {c(1)}]",
        subtitle=subtitle,
        border_style=b,
        box=box.ROUNDED,
    )


def run_milestone_solution_upload(
    console: Console,
    milestone_id: str,
    *,
    challenge_id: str,
    api_auth: CliApiAuth,
) -> None:
    """Prompt for a ``.zip``, then upload via ``submissions/upload`` and presigned storage."""
    console.print()
    console.print(
        _styled_panel(
            "Milestone",
            Text.assemble(
                ("Selected milestone id: ", f"dim {c(3)}"),
                (milestone_id, f"bold {c(2)}"),
            ),
            border=f"bold {c(1)}",
        )
    )

    # Validate that the milestone's submission window is currently open (from main)
    try:
        challenges_client = _challenges_client_for_api(api_auth, console)
        challenge_detail = challenges_client.get_challenge(challenge_id)
        milestone_detail = _find_milestone(challenge_detail, milestone_id)
        _assert_milestone_allows_submission(milestone_detail)
    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except Exception as e:
        err_code = getattr(e, "error_code", None)
        if err_code:
            # Surface the platform error_code prominently for support / bug reports
            raise click.ClickException(
                f"Failed to verify milestone submission window (error_code: {err_code}):\n{e}"
            ) from e
        raise click.ClickException(f"Failed to verify milestone submission window: {e}") from e

    # Derive the miner hotkey SS58 from the configured wallet (no MINER_HOTKEY_SS58).
    # The hotkey from --wallet.name / --wallet.hotkey is used as the registered miner identity.
    try:
        wallet = bt.Wallet(
            name=api_auth.wallet_name,
            hotkey=api_auth.wallet_hotkey,
            path=api_auth.wallet_path,
        )
        miner_hotkey = wallet.hotkey.ss58_address
        console.print(f"Using miner hotkey from wallet: {miner_hotkey}")
    except Exception as e:
        raise click.ClickException(
            f"Failed to load hotkey from wallet '{api_auth.wallet_name}' / '{api_auth.wallet_hotkey}'.\n"
            f"Error: {e}\n\n"
            "Make sure the wallet and hotkey exist and are unlocked if password-protected."
        ) from e
    console.print(
        _styled_panel(
            "Solution file",
            Text.assemble(
                ("Provide a path to a ", f"dim {c(3)}"),
                (".zip", f"bold {c(0)}"),
                (" solution archive.", f"dim {c(3)}"),
                ("\n", ""),
                ("Leave empty to cancel.", f"dim {c(3)}"),
            ),
        )
    )
    if _HAVE_TERMIOS and sys.stdin.isatty():
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except OSError:
            pass
    raw = (
        Prompt.ask(
            Text.assemble(
                ("Path to ", f"dim {c(3)}"),
                (".zip", f"bold {c(0)}"),
            ),
            console=console,
            default="",
            show_default=False,
        )
        or ""
    ).strip()
    if not raw:
        console.print(_styled_panel("Cancelled", "No file path entered.", border=f"dim {c(3)}"))
        return
    path = Path(raw).expanduser()
    if not path.is_file():
        console.print(
            _styled_panel(
                "Invalid path",
                f"Not a file: {path}",
                border=f"bold {c(4)}",
            )
        )
        return
    if path.suffix.lower() != ".zip":
        console.print(
            _styled_panel(
                "Invalid file",
                "The solution must be a .zip archive.",
                border=f"bold {c(4)}",
            )
        )
        return

    price_tao = _confirm_fee_amount_before_unlock(
        console=console,
        challenges_client=challenges_client,
        milestone_id=milestone_id,
        challenge_id=challenge_id,
    )
    if price_tao is None:
        # User declined the transfer
        return

    # By default we use the main wallet's coldkey (from --wallet.name / --wallet.path)
    # for the fee payment. This is the simplest and most intuitive model.
    # Advanced users can still override the payment coldkey using the legacy
    # MINER_FEE_WALLET_NAME or MINER_FEE_COLDKEY_PATH environment variables.
    fee_keypair = None

    # Legacy / power-user override for a completely different fee coldkey
    legacy_fee_keyfile = (
        os.getenv("MINER_FEE_COLDKEY_PATH") or os.getenv("FEE_WALLET_PATH") or ""
    ).strip()
    legacy_fee_wallet = (
        os.getenv("MINER_FEE_WALLET_NAME") or os.getenv("FEE_WALLET_NAME") or ""
    ).strip()

    if legacy_fee_keyfile:
        console.print("Loading fee coldkey from legacy path override")
        fee_keypair = load_fee_keypair_from_keyfile(legacy_fee_keyfile)
    elif legacy_fee_wallet:
        console.print("Loading fee coldkey from legacy wallet name override")
        fee_keypair = load_fee_keypair_from_wallet(legacy_fee_wallet)
    else:
        # Normal path: use the primary wallet (from --wallet.name / --wallet.path) for fee payment
        fee_keypair = load_fee_keypair_from_wallet(
            api_auth.wallet_name, wallet_path=api_auth.wallet_path
        )

    if fee_keypair is None:
        console.print(_styled_panel("Cancelled", "No fee coldkey provided.", border=f"dim {c(3)}"))
        return

    source_ss58 = fee_keypair.ss58_address
    console.print(f"Fee coldkey loaded: {source_ss58}")

    # Old mnemonic env var is now a hard error
    if os.getenv("MINER_SOURCE_COLDKEY_MNEMONIC"):
        raise click.ClickException(
            "MINER_SOURCE_COLDKEY_MNEMONIC is no longer supported.\n"
            "Configure your wallet with --wallet.name / --wallet.path (or FEE_WALLET_NAME).\n"
            "The miner hotkey is now taken from --wallet.hotkey."
        )

    try:
        submit_solution(
            console,
            milestone_id,
            str(path.resolve()),
            challenges_client=challenges_client,
            miner_hotkey=miner_hotkey,
            source_ss58=source_ss58,
            fee_keypair=fee_keypair,
            network=api_auth.network,
            challenge_id=challenge_id,
            fee_tao=price_tao,
        )
    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(str(e)) from e


def submit_solution(
    console: Console,
    milestone_id: str,
    solution_path: str,
    *,
    challenges_client: "ChallengesClient",
    miner_hotkey: str,
    source_ss58: str,
    fee_keypair: "Keypair",
    network: str,
    challenge_id: str,
    fee_tao: float | None = None,
) -> dict[str, Any]:

    # store solution in the database for the miner_hotkey
    """Request slot, transfer (as batch + remark), upload zip, then persist DB row for the miner."""
    if not challenge_id:
        raise click.ClickException("challenge_id is required to submit a solution.")
    zip_path = Path(solution_path)
    size = zip_path.stat().st_size
    upload_path = "v1/submissions/upload"
    upload_url = f"{_api_cfg.challenges_api_url}/v1/submissions/upload"
    console.print(
        _styled_panel(
            "Upload slot",
            Text.assemble(
                ("Requesting slot from ", f"dim {c(3)}"),
                (upload_url, f"bold {c(2)}"),
            ),
        )
    )

    # The passed client is already authenticated and pointed at the challenges service.
    auth_client = challenges_client

    try:
        challenge_detail = auth_client.get_challenge(challenge_id)
    except Exception as e:
        err_code = getattr(e, "error_code", None)
        if err_code:
            raise click.ClickException(
                f"Failed to fetch challenge details (error_code: {err_code}):\n{e}"
            ) from e
        raise click.ClickException(f"Failed to fetch challenge details: {e}") from e
    milestone_detail = _find_milestone(challenge_detail, milestone_id)
    _assert_milestone_allows_submission(milestone_detail)

    try:
        slot = auth_client.get_submission_upload_slot(
            filename=zip_path.name,
            size=size,
        )
    except Exception as e:
        err_code = getattr(e, "error_code", None)
        if err_code:
            raise click.ClickException(
                f"Upload URL request failed (error_code: {err_code}):\n{e}"
            ) from e
        raise click.ClickException(f"Upload URL request failed: {e}") from e

    # The response contains the presigned upload_url + fields.
    # The actual upload to this URL must be done WITHOUT auth headers.
    upload_url = slot.get("upload_url") or slot.get("url")
    fields = slot.get("fields") or {}

    if not upload_url:
        raise click.ClickException("Upload response did not include an upload_url")

    # The response from the platform (slot info) is what we pass to the upload helper
    spec = slot
    upload_id = spec.get("id")
    if not upload_id:
        # Some implementations return the id at top level, some inside 'data'
        upload_id = spec.get("data", {}).get("id") if isinstance(spec.get("data"), dict) else None

    if not upload_id:
        # Still proceed — the upload helper may not strictly need the id
        bt.logging.warning("Upload slot response did not include an 'id'")

    # Use pre-fetched amount if provided (from the confirmation step), otherwise fetch.
    # This also avoids a second round-trip after the user has already confirmed the amount.
    if fee_tao is not None:
        price_tao = fee_tao
    else:
        price_tao = auth_client.get_milestone_price_tao(challenge_id=challenge_id, milestone_id=milestone_id)

    proof_tx = transfer_tao_for_submission(
        console=console,
        source_ss58=source_ss58,
        keypair=fee_keypair,
        network=network,
        fee_tao=price_tao,
        miner_hotkey=miner_hotkey,
        milestone_id=milestone_id,
        upload_endpoint_id=str(upload_id) if upload_id else "",
    )
    tx_hash = proof_tx.extrinsic_hash
    transfer_block_hash = proof_tx.block_hash
    console.print(
        _styled_panel(
            "Transfer status",
            Text.assemble(
                ("Transfer succeeded", f"bold {c(0)}"),
                (" (tx hash: ", f"dim {c(3)}"),
                (tx_hash, f"bold {c(2)}"),
                (", block: ", f"dim {c(3)}"),
                (transfer_block_hash, f"bold {c(2)}"),
                (")", f"dim {c(3)}"),
            ),
            border=f"bold {c(0)}",
        )
    )

    console.print(Text("Uploading bytes…", style=f"dim {c(3)}"))
    _upload_solution_zip(console, spec, zip_path)

    console.print()
    console.print(
        Align.center(Text("Upload Successful", style=f"bold {c(0)}"))
    )
    console.print(
        _styled_panel(
            "POST …/submissions/upload response",
            Syntax(
                json.dumps(spec, indent=2, default=str),
                "json",
                theme="monokai",
                word_wrap=True,
            ),
            border=f"bold {c(0)}",
        )
    )
    store_solution_in_database(
        console,
        miner_hotkey,
        source_ss58,
        solution_path,
        milestone_id,
        str(upload_id),
        tx_hash,
        challenge_id=challenge_id,
        transfer_block_hash=transfer_block_hash,
        transfer_to_ss58=TRANSFER_DEST_SS58,
        transfer_amount_rao=str(int(bt.Balance.from_tao(price_tao).rao)),
    )

    return spec


def store_solution_in_database(
    console: Console,
    miner_hotkey: str,
    source_ss58: str,
    solution_path: str,
    milestone_id: str,
    upload_id: str,
    tx_hash: str,
    *,
    challenge_id: str,
    transfer_block_hash: str,
    transfer_to_ss58: str,
    transfer_amount_rao: str,
) -> None:
    """Store the solution in the database keyed by miner hotkey."""

    db_connection = DBConnection(database_name_prefix=MINER_DB_TABLE_PREFIX, hotkey=miner_hotkey)
    inserted = db_connection.db_query_miner.insert_miner_submission(
        upload_id=upload_id,
        challenge_milestone_id=milestone_id,
        miner_hotkey=miner_hotkey,
        tx_hash=tx_hash,
        challenge_id=challenge_id,
        transfer_block_hash=transfer_block_hash,
        transfer_from_ss58=source_ss58,
        transfer_to_ss58=transfer_to_ss58,
        transfer_amount_rao=transfer_amount_rao,
    )
    if inserted:
        console.print(
            Text(f"Saved to local DB: {db_connection.DB_PATH}", style=f"dim {c(3)}")
        )

    console.print(
        _styled_panel(
            "Submission data",
            Text.assemble(
                ("Miner hotkey", f"dim {c(3)}"),
                (": ", f"dim {c(3)}"),
                (miner_hotkey, f"bold {c(2)}"),
                ("\n", ""),
                ("Source ss58", f"dim {c(3)}"),
                (": ", f"dim {c(3)}"),
                (source_ss58, f"bold {c(2)}"),
                ("\n", ""),
                ("Solution path", f"dim {c(3)}"),
                (": ", f"dim {c(3)}"),
                (solution_path, f"bold {c(0)}"),
                ("\n", ""),
                ("Milestone id", f"dim {c(3)}"),
                (": ", f"dim {c(3)}"),
                (milestone_id, f"bold {c(2)}"),
                ("\n", ""),
                ("Challenge id", f"dim {c(3)}"),
                (": ", f"dim {c(3)}"),
                (challenge_id, f"bold {c(2)}"),
                ("\n", ""),
                ("Upload id", f"dim {c(3)}"),
                (": ", f"dim {c(3)}"),
                (upload_id, f"bold {c(2)}"),
                ("\n", ""),
                ("Tx hash", f"dim {c(3)}"),
                (": ", f"dim {c(3)}"),
                (tx_hash, f"bold {c(2)}"),
                ("\n", ""),
                ("Transfer block hash", f"dim {c(3)}"),
                (": ", f"dim {c(3)}"),
                (transfer_block_hash, f"bold {c(2)}"),
            ),
            border=f"bold {c(1)}",
        )
    )
    console.print(Text("Submission Data Created!", style=f"bold {c(0)}"))
    _exit_cli_successfully(console)


def _upload_solution_zip(console: Console, spec: dict[str, Any], zip_path: Path) -> None:
    """PUT or POST multipart to the URL / fields returned by the upload API."""
    direct = (
        spec.get("upload_url")
        or spec.get("presigned_url")
        or spec.get("url")
        or spec.get("put_url")
    )
    fields = spec.get("fields")
    if isinstance(direct, str) and direct and not fields:
        headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else {}
        merged = {"Content-Type": "application/zip", **{str(k): str(v) for k, v in headers.items()}}
        with zip_path.open("rb") as fh:
            put_resp = requests.put(direct, data=fh, headers=merged, timeout=600.0)
        try:
            put_resp.raise_for_status()
        except requests.HTTPError as e:
            raise click.ClickException(
                f"Upload PUT failed ({put_resp.status_code}): {put_resp.text[:400]}"
            ) from e
        return
    if isinstance(direct, str) and isinstance(fields, dict):
        with zip_path.open("rb") as fh:
            files = {"file": (zip_path.name, fh, "application/zip")}
            post_resp = requests.post(direct, data=fields, files=files, timeout=600.0)
        try:
            post_resp.raise_for_status()
        except requests.HTTPError as e:
            raise click.ClickException(
                f"Upload POST failed ({post_resp.status_code}): {post_resp.text[:400]}"
            ) from e
        return
    console.print(
        _styled_panel(
            "Upload response",
            "Could not find upload_url/url + fields in the API response; "
            "extend _upload_solution_zip for your server's shape.",
            border=f"bold {c(4)}",
        )
    )
    raise click.ClickException("Unsupported upload response shape from /v1/submissions/upload.")


def query_challenge_data(
    challenge: dict[str, Any],
    *,
    console: Console | None = None,
    api_auth: CliApiAuth,
) -> None:
    """Load challenge detail from the API, then show milestones."""
    cid = challenge.get("id")
    if not cid:
        raise ValueError("Challenge ID is required")

    from qbittensor.utils.services.challenges import ChallengesClient

    client = ChallengesClient(base_url=_api_cfg.challenges_api_url)
    payload = client.get_challenge(cid)
    if not isinstance(payload, dict):
        raise ValueError("Challenge data is not a dictionary")
    out = console or Console()
    format_milestones(out, payload, api_auth=api_auth)


def _milestones_from_detail(detail: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("milestones", "__milestones__"):
        raw = detail.get(key)
        if isinstance(raw, list) and raw:
            return [m for m in raw if isinstance(m, dict)]
    return []


def _find_milestone(challenge_detail: dict[str, Any], milestone_id: str) -> dict[str, Any]:
    for ms in _milestones_from_detail(challenge_detail):
        if str(ms.get("id")) == str(milestone_id):
            return ms
    raise click.ClickException(
        f"Milestone {milestone_id} not found under challenge {challenge_detail.get('id', '?')}."
    )


def _format_milestone_prize(ms: dict[str, Any]) -> str:
    """Format milestone prize/fee for the CLI table from API fields."""
    price_tao = ms.get("priceTao")
    if price_tao is not None:
        try:
            if float(price_tao) > 0:
                return f"{price_tao} TAO"
        except (TypeError, ValueError):
            pass
    prize_usd = ms.get("prizeUsd")
    if prize_usd is not None:
        return f"${prize_usd}"
    prize_alpha = ms.get("prizeAlpha")
    if prize_alpha is not None:
        return f"{prize_alpha} α"
    return "—"


def _milestone_table(
    detail: dict[str, Any], milestones: list[dict[str, Any]], selected: int
) -> Table:
    title = str(detail.get("name", "Milestones"))
    table = Table(
        title=Text(title, style=f"bold {c(1)}"),
        box=box.ROUNDED,
        border_style=f"dim {c(4)}",
        header_style=f"bold {c(1)}",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("", width=3, min_width=3, justify="center", no_wrap=True)
    table.add_column("Name", ratio=3, min_width=10, overflow="ellipsis")
    table.add_column("Prize", width=9, min_width=7, justify="left", no_wrap=True)
    table.add_column("Submissions", width=12, min_width=11, justify="right", no_wrap=True)
    table.add_column("Start", width=23, min_width=23, no_wrap=True, overflow="crop")
    table.add_column("End", width=23, min_width=23, no_wrap=True, overflow="crop")
    table.add_column("Status", width=12, min_width=12, justify="center", no_wrap=True)
    n = len(milestones)
    if n == 0:
        table.add_row("—", f"[dim {c(3)}]No milestones[/dim {c(3)}]", "", "", "", "", "")
        return table
    sel = max(0, min(selected, n - 1))
    for i, ms in enumerate(milestones):
        marker = "▶" if i == sel else " "
        row_style: str | None = "reverse bold" if i == sel else None
        table.add_row(
            marker,
            str(ms.get("name", "—")),
            _format_milestone_prize(ms),
            str(ms.get("submission_count", "—")),
            _format_api_datetime_display(ms.get("start_date")),
            _format_api_datetime_display(ms.get("end_date")),
            _format_milestone_status_display(ms),
            style=row_style,
        )
    return table


def _milestones_frame(
    detail: dict[str, Any], milestones: list[dict[str, Any]], selected: int
) -> Group:
    body = _milestone_table(detail, milestones, selected)
    footer = Align.center(
        Text.assemble(
            ("↑ ↓ ", f"bold {c(2)}"),
            ("Select milestone   ", f"dim {c(3)}"),
            ("Enter ", f"bold {c(0)}"),
            ("Upload zip   ", f"dim {c(3)}"),
            ("q ", f"bold {c(1)}"),
            ("Back to challenges", f"dim {c(3)}"),
        )
    )
    return Group(body, Text(""), footer)


def format_milestones(
    console: Console,
    detail: dict[str, Any],
    *,
    api_auth: CliApiAuth,
) -> None:
    """Show milestones for a challenge detail payload; ↑/↓ to move selection, q to leave."""
    milestones = _milestones_from_detail(detail)
    n = len(milestones)
    selected = 0

    if not _HAVE_TERMIOS or not sys.stdin.isatty():
        console.print(_milestone_table(detail, milestones, 0))
        return

    # Do not call Prompt / line input while Rich Live is active — its refresh
    # corrupts stdin and the prompt. Leave Live before run_milestone_solution_upload.
    while True:
        pending_id: str | None = None
        with Live(
            _milestones_frame(detail, milestones, selected),
            console=console,
            refresh_per_second=12,
            transient=False,
        ) as live:
            while True:
                if n:
                    selected = max(0, min(selected, n - 1))
                live.update(_milestones_frame(detail, milestones, selected))
                key = _read_key_unix()
                if key in (KEY_Q, KEY_Q_UPPER):
                    return
                if key == KEY_UP:
                    if n:
                        selected = (selected - 1) % n
                elif key == KEY_DOWN:
                    if n:
                        selected = (selected + 1) % n
                elif key in (KEY_ENTER, KEY_ENTER_ALT):
                    if n and 0 <= selected < n:
                        mid = milestones[selected].get("id")
                        if mid is not None:
                            pending_id = str(mid)
                            break

        if pending_id is None:
            continue

        selected_milestone = next(
            (ms for ms in milestones if str(ms.get("id")) == pending_id),
            None,
        )
        if selected_milestone is None:
            continue

        try:
            challenge_id = detail.get("id")
            if not challenge_id:
                raise click.ClickException("Challenge detail did not include an id.")
            _assert_milestone_allows_submission(selected_milestone)
            run_milestone_solution_upload(
                console,
                pending_id,
                challenge_id=str(challenge_id),
                api_auth=api_auth,
            )
        except click.exceptions.Exit:
            raise
        except click.ClickException as err:
            console.print(
                _styled_panel(
                    "Submission error",
                    str(err),
                    border=f"bold {c(4)}",
                )
            )


def _read_key_unix() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch += sys.stdin.read(2)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _goodbye_panel() -> Panel:
    body = Group(
        Align.center(Text("Submission complete.", style=f"bold {c(0)}")),
        Text(""),
        Align.center(Text("Goodbye.", style=f"dim {c(3)}")),
    )
    return Panel.fit(
        Padding(body, (1, 6)),
        box=box.ROUNDED,
        border_style=f"bold {c(0)}",
        subtitle=f"[dim {c(3)}]enigma miner[/dim {c(3)}]",
        subtitle_align="center",
    )


def _exit_cli_successfully(console: Console) -> None:
    """Print the goodbye panel, flush output, and terminate the CLI with exit code 0.

    Some bittensor / substrate websocket reader threads are non-daemon, so a plain
    ``sys.exit(0)`` can hang. ``bt.Subtensor`` is now used as a context manager which
    closes the substrate connection on the happy path; the daemonized timer below is a
    belt-and-suspenders fallback so the process never hangs after a successful submission.
    """
    console.print()
    console.print(Align.center(_goodbye_panel()))
    console.print()
    try:
        sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.flush()
    except Exception:
        pass
    fallback = threading.Timer(2.0, lambda: os._exit(0))
    fallback.daemon = True
    fallback.start()
    sys.exit(0)


def _welcome_panel(network: str) -> Panel:
    title = Text.assemble(
        ("Welcome ", f"bold {c(0)}"),
        ("to ", f"bold {c(1)}"),
        ("Enigma!", f"bold {c(2)}"),
    )
    network_text = Text(f"Network: {network}", style=f"dim {c(3)}")
    return Panel.fit(
        Padding(
            Group(
                Align.center(title),
                Text(""),
                Align.center(network_text),
            ),
            (1, 6),
        ),
        box=box.DOUBLE,
        border_style=f"bold {c(1)}",
        subtitle=f"[dim {c(3)}]enigma miner[/dim {c(3)}]",
        subtitle_align="center",
    )


def _challenge_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("challenges") or []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _view_summary(payload: dict[str, Any], selected_row: int) -> RenderableType:
    rows = _challenge_rows(payload)
    table = Table(
        title=Text("Challenges", style=f"bold {c(1)}"),
        box=box.ROUNDED,
        border_style=f"dim {c(4)}",
        header_style=f"bold {c(1)}",
        expand=True,
    )
    table.add_column("", justify="center", width=3)
    table.add_column("Name", ratio=2)
    table.add_column("Completed", justify="center")
    if not rows:
        table.add_row("—", f"[dim {c(3)}]No challenges[/dim {c(3)}]", "")
        return table
    n = len(rows)
    sel = max(0, min(selected_row, n - 1))
    for i, ch in enumerate(rows):
        marker = "▶" if i == sel else " "
        row_style: str | None = "reverse bold" if i == sel else None
        table.add_row(
            marker,
            str(ch.get("name", "—")),
            "yes" if ch.get("completed") else "no",
            style=row_style,
        )
    return table


def _view_json(payload: dict[str, Any]) -> RenderableType:
    try:
        text = json.dumps(payload, indent=2, default=str)
    except TypeError:
        text = str(payload)
    return Panel(
        Syntax(text, "json", theme="monokai", word_wrap=True),
        title=f"[bold {c(2)}]Full response[/bold {c(2)}]",
        border_style=f"dim {c(4)}",
    )


def _view_pagination(payload: dict[str, Any]) -> RenderableType:
    pag = payload.get("pagination")
    if not isinstance(pag, dict):
        pag = {}
    body = Text.assemble(
        ("next_cursor ", f"dim {c(3)}"),
        (repr(pag.get("next_cursor")), f"bold {c(2)}"),
    )
    return Panel(
        body,
        title=f"[bold {c(0)}]Pagination[/bold {c(0)}]",
        border_style=f"bold {c(1)}",
    )


def _build_challenges_frame(
    payload: dict[str, Any],
    view_index: int,
    view_labels: tuple[str, ...],
    selected_row: int,
) -> Group:
    vi = view_index % len(view_labels)
    if vi == 0:
        body: RenderableType = _view_summary(payload, selected_row)
    elif vi == 1:
        body = _view_json(payload)
    else:
        body = _view_pagination(payload)
    label = view_labels[vi]
    footer = Align.center(
        Text.assemble(
            ("↑ ↓ ", f"bold {c(2)}"),
            ("Select challenge   ", f"dim {c(3)}"),
            ("← → ", f"bold {c(2)}"),
            (f"{label}   ", f"dim {c(3)}"),
            ("Enter ", f"bold {c(0)}"),
            ("Confirm   ", f"dim {c(3)}"),
            ("q ", f"bold {c(1)}"),
            ("Quit", f"dim {c(3)}"),
        )
    )
    return Group(body, Text(""), footer)


def query_and_format_challenges(
    console: Console,
    *,
    base_url: str | None = None,
    api_auth: CliApiAuth,
) -> dict[str, Any]:
    """Fetch ``GET /v1/challenges``, then let the user cycle views of the payload (← →, q)."""
    effective_base = base_url or _api_cfg.challenges_api_url
    if not effective_base:
        raise click.ClickException(
            "Challenges API base URL is not configured. "
            "Use --base-url or ensure the Challenges API URL is configured."
        )

    from qbittensor.utils.services.challenges import ChallengesClient

    client = ChallengesClient(base_url=effective_base)
    payload = client.list_challenges()

    view_labels = ("Summary table", "Full JSON", "Pagination")
    view_index = 0
    rows = _challenge_rows(payload)
    n_rows = len(rows)
    selected_row = 0 if n_rows else 0

    if not _HAVE_TERMIOS or not sys.stdin.isatty():
        console.print(
            Panel(
                Text("Not a TTY: printing all views.", style=f"dim {c(3)}"),
                border_style=f"dim {c(4)}",
            )
        )
        console.print(_view_summary(payload, 0))
        console.print(_view_json(payload))
        console.print(_view_pagination(payload))
        return payload

    while True:
        pending: dict[str, Any] | None = None
        with Live(
            _build_challenges_frame(payload, view_index, view_labels, selected_row),
            console=console,
            refresh_per_second=8,
            transient=False,
        ) as live:
            while True:
                if n_rows:
                    selected_row = max(0, min(selected_row, n_rows - 1))
                live.update(
                    _build_challenges_frame(
                        payload, view_index, view_labels, selected_row
                    )
                )
                key = _read_key_unix()
                if key in (KEY_Q, KEY_Q_UPPER):
                    return payload
                if key == KEY_UP:
                    if n_rows:
                        selected_row = (selected_row - 1) % n_rows
                elif key == KEY_DOWN:
                    if n_rows:
                        selected_row = (selected_row + 1) % n_rows
                elif key == KEY_LEFT:
                    view_index = (view_index - 1) % len(view_labels)
                elif key == KEY_RIGHT:
                    view_index = (view_index + 1) % len(view_labels)
                elif key in (KEY_ENTER, KEY_ENTER_ALT):
                    if (
                        view_index % len(view_labels) == 0
                        and n_rows
                        and 0 <= selected_row < n_rows
                    ):
                        pending = rows[selected_row]
                        break

        if pending is None:
            return payload
        query_challenge_data(
            pending,
            console=console,
            api_auth=api_auth,
        )


@click.command()
@click.option(
    "--base-url",
    default=None,
    help="Challenges API base URL (overrides the configured default).",
)
@click.option(
    "--wallet.name",
    "wallet_name",
    default=None,
    help="Wallet name for platform API authentication (JWT). Can be the same as your mining wallet.",
)
@click.option(
    "--wallet.hotkey",
    "wallet_hotkey",
    default=None,
    help="Hotkey for platform API authentication (JWT). Can be the same as your mining hotkey.",
)
@click.option(
    "--wallet.path",
    "wallet_path",
    default=None,
    help="Custom path to the wallets directory.",
)
@click.option(
    "--wallet-name",
    "wallet_name_legacy",
    default=None,
    hidden=True,
    help="(Deprecated) Use --wallet.name instead.",
)
@click.option(
    "--wallet-hotkey",
    "wallet_hotkey_legacy",
    default=None,
    hidden=True,
    help="(Deprecated) Use --wallet.hotkey instead.",
)
@click.option(
    "--network",
    default=None,
    help="Bittensor network label passed to RequestManager (default: NETWORK env or finney).",
)
@click.option(
    "--netuid",
    default=None,
    type=int,
    help="Subnet netuid for TensorAuth JWT claims (default: NETUID env or 63).",
)
@click.version_option(version="0.1.0", prog_name="mine-enigma")
def main(
    base_url: str | None,
    wallet_name: str | None,
    wallet_hotkey: str | None,
    wallet_path: str | None,
    wallet_name_legacy: str | None,
    wallet_hotkey_legacy: str | None,
    network: str | None,
    netuid: int | None,
) -> None:
    """Welcome banner, then browse challenges response views."""
    # Environment is already loaded at module import time via get_api_config()
    # (which calls load_dotenv()). _resolve_cli_api_auth reads directly from os.getenv.
    # Support both new dotted style and old flat style for backward compat
    effective_wallet_name = wallet_name or wallet_name_legacy
    effective_wallet_hotkey = wallet_hotkey or wallet_hotkey_legacy

    api_auth = _resolve_cli_api_auth(
        wallet_name=effective_wallet_name,
        wallet_hotkey=effective_wallet_hotkey,
        wallet_path=wallet_path,
        network=network,
        netuid=netuid,
    )
    console = Console()
    console.print()
    console.print(Align.center(_welcome_panel(api_auth.network)))
    console.print()

    run_miner_main_menu(console, base_url=base_url, api_auth=api_auth)


def run_miner_main_menu(console: Console, base_url: str | None, api_auth: CliApiAuth) -> None:
    """Top-level interactive menu for the miner operator."""
    while True:
        console.print()
        console.print(Panel.fit(
            "[bold]What would you like to do?[/]",
            border_style="blue",
            title="Enigma Miner",
        ))

        options = [
            ("1", "Submit a new solution"),
            ("2", "List my submissions (with status)"),
            ("q", "Quit"),
        ]

        for key, label in options:
            console.print(f"  [{c(2)}]{key}[/]  {label}")

        choice = Prompt.ask(
            "\nChoice",
            choices=["1", "2", "q", "Q"],
            show_choices=False,
            default="1",
        ).strip().lower()

        if choice in ("q", "quit"):
            console.print("Goodbye!")
            break

        if choice == "1":
            try:
                query_and_format_challenges(
                    console,
                    base_url=base_url,
                    api_auth=api_auth,
                )
            except Exception as e:
                console.print(f"[red]Error during submission flow:[/] {e}")
            continue

        if choice == "2":
            _list_my_submissions(console, api_auth)
            continue


def _list_my_submissions(console: Console, api_auth: CliApiAuth) -> None:
    """Show the operator their local submissions and received validator statuses."""
    miner_hotkey = (os.getenv("MINER_HOTKEY_SS58") or "").strip()
    if not miner_hotkey:
        miner_hotkey = Prompt.ask(
            Text.assemble(("Enter your miner hotkey (SS58)", f"dim {c(3)}"))
        ).strip()

    if not miner_hotkey:
        console.print("[yellow]No hotkey provided.[/]")
        return

    db_connection = DBConnection(
        database_name_prefix=MINER_DB_TABLE_PREFIX,
        hotkey=miner_hotkey,
    )

    submissions = db_connection.db_query_miner.list_my_submissions_with_status(limit=100)

    if not submissions:
        console.print("[yellow]No submissions found in your local database.[/]")
        return

    table = Table(title=f"Your Submissions ({miner_hotkey[:8]}...)", box=box.ROUNDED)
    table.add_column("TX Hash (short)", style="dim")
    table.add_column("Milestone", style="cyan")
    table.add_column("Submitted At", style="green")
    table.add_column("Validator Statuses", style="white")

    for sub in submissions:
        tx_short = sub["tx_hash"][:12] + "..." if sub["tx_hash"] else "?"
        submitted = sub["submitted_at"].strftime("%Y-%m-%d %H:%M") if sub["submitted_at"] else "[dim]never[/]"

        status_lines = []
        for vhotkey, info in sub.get("validator_statuses", {}).items():
            status = info.get("status", "?")
            color = "green" if status.lower() in ("success", "offered") else "red"
            status_lines.append(f"[{color}]{status}[/] ({vhotkey[:8]}...)")

        status_display = "\n".join(status_lines) if status_lines else "[dim]No status yet[/]"

        table.add_row(
            tx_short,
            sub["challenge_milestone_id"][:8] + "...",
            submitted,
            status_display,
        )

    console.print(table)
    console.print()
    Prompt.ask("Press Enter to return to menu", default="")


if __name__ == "__main__":
    main()
