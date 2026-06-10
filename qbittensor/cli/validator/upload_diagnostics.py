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
Utility: Zip + upload validator diagnostics (local SQLite DB + pm2 logs) via the
challenges platform "verify/upload" endpoint (JWT authenticated with your validator hotkey).

The script is interactive and will try to default to common pm2 locations for the
"enigma-validator" (or similarly named) process.

It emits the resulting upload_id on success. You can share that ID with support
so they can retrieve the bundle from platform storage.

Examples:
    python -m qbittensor.cli.validator.upload_diagnostics \\
        --wallet.name validator --wallet.hotkey hotkeyname

    # Non-interactive with explicit paths
    python -m qbittensor.cli.validator.upload_diagnostics \\
        --wallet.name validator --wallet.hotkey hotkeyname \\
        --db-path /path/to/challenge_solutions_xxxxx.db \\
        --log-path ~/.pm2/logs/enigma-validator-out.log \\
        --log-path ~/.pm2/logs/enigma-validator-error.log \\
        --yes
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import click
import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from qbittensor.database.db_connection import _resolve_db_dir
from qbittensor.dto.challenge import ChallengeSubmissionVerifyUploadAddressResponse
from qbittensor.utils.env import get_api_config
from qbittensor.utils.services.challenges import ChallengesClient


_api_cfg = get_api_config()


def _discover_pm2_logs(hints: Iterable[str] = ("enigma", "validator", "sn63")) -> list[Path]:
    """Best-effort discovery of pm2 log files for the validator process."""
    pm2 = shutil.which("pm2")
    found: list[Path] = []

    if pm2:
        try:
            out = subprocess.check_output([pm2, "jlist"], text=True, timeout=8, stderr=subprocess.DEVNULL)
            procs = json.loads(out)
            for p in procs or []:
                name = str(p.get("name") or "").lower()
                if any(h in name for h in hints):
                    env = p.get("pm2_env") or {}
                    for key in ("pm_out_log_path", "pm_err_log_path", "pm_log_path", "out_log_path", "err_log_path"):
                        val = env.get(key)
                        if val:
                            pth = Path(val).expanduser()
                            if pth.exists() and pth not in found:
                                found.append(pth)
        except Exception:
            pass  # fall through to filesystem heuristics

    # Common pm2 default locations
    home = Path.home()
    log_dir = home / ".pm2" / "logs"
    if log_dir.is_dir():
        for pattern in ("*enigma*", "*validator*", "*sn63*"):
            for p in sorted(log_dir.glob(pattern)):
                if p.is_file() and p not in found:
                    found.append(p)

    return found


def _find_default_db(hotkey_ss58: str) -> Path | None:
    """Try to locate the validator's challenge_solutions DB using the same resolution as the neuron."""
    if not hotkey_ss58:
        return None
    prefix = hotkey_ss58[:5]
    try:
        base = _resolve_db_dir()
        candidates = sorted(base.glob(f"challenge_solutions_{prefix}*.db"))
        if candidates:
            # Prefer exact 5-char prefix match if present
            exact = [c for c in candidates if c.name == f"challenge_solutions_{prefix}.db"]
            return (exact or candidates)[0]
    except Exception:
        pass
    return None


def _collect_db_related_files(main_db: Path | None) -> list[Path]:
    """Given the primary .db path, return it + any existing SQLite WAL/SHM sidecars.

    This is required for a consistent diagnostic snapshot because the validator
    runs with journal_mode=WAL (see db_connection._enable_sqlite_wal_mode).
    """
    if not main_db:
        return []
    files: list[Path] = []
    main = main_db.expanduser().resolve()

    # If a sidecar was passed (e.g. user gave --db-path to the .db-wal), normalize to the main DB first
    if main.name.endswith(("-wal", "-shm")):
        main = main.with_name(main.name.rsplit("-", 1)[0])

    if main.exists():
        files.append(main)

    # Standard SQLite WAL files (now using the normalized main)
    for suffix in ("-wal", "-shm"):
        sidecar = main.with_name(main.name + suffix)
        if sidecar.exists():
            files.append(sidecar)

    # Catch anything else the user might have (e.g. other .db-journal etc.)
    try:
        for p in main.parent.glob(main.name + "*"):
            if p.is_file() and p not in files:
                files.append(p)
    except Exception:
        pass
    return sorted(set(files))


def _create_diagnostic_zip(
    db_files: list[Path],
    log_paths: list[Path],
    hotkey_prefix: str,
    console: Console,
) -> Path:
    """Create a temp zip containing the selected files + a small manifest.

    db_files should already contain the primary .db plus any -wal / -shm sidecars.
    """
    fd, zip_path = tempfile.mkstemp(prefix=f"validator-diagnostics-{hotkey_prefix}-", suffix=".zip")
    os.close(fd)
    zip_path = Path(zip_path)

    manifest_lines = [
        f"generated_at: {datetime.now(timezone.utc).isoformat()}",
        f"hotkey_prefix: {hotkey_prefix}",
        f"host: {os.uname().nodename if hasattr(os, 'uname') else 'unknown'}",
        "",
        "included_files:",
    ]

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Manifest first
            for f in db_files:
                manifest_lines.append(f"  - db: {f}")
            for lp in log_paths:
                manifest_lines.append(f"  - log: {lp}")

            # Add actual files under sensible prefixes inside the archive
            for f in db_files:
                if f.exists():
                    zf.write(f, arcname=f"db/{f.name}")

            for lp in log_paths:
                if lp.exists():
                    # Put logs under logs/ with their original basename (pm2 names are already descriptive)
                    zf.write(lp, arcname=f"logs/{lp.name}")

            # Write manifest
            zf.writestr("manifest.txt", "\n".join(manifest_lines) + "\n")

        size = zip_path.stat().st_size
        console.print(f"[green]✅ Created diagnostic bundle[/green] {zip_path} ({size // 1024} KiB)")
        return zip_path
    except Exception:
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _upload_bundle(
    zip_path: Path,
    challenges_client: ChallengesClient,
    console: Console,
) -> str | None:
    """Request an upload slot and PUT the zip. Returns the upload_id on success."""
    console.print("📢 Requesting upload slot from platform (v1/challenges/submissions/verify/upload)...")
    slot: ChallengeSubmissionVerifyUploadAddressResponse | None = (
        challenges_client.create_verification_upload_url()
    )
    if not slot or not slot.url:
        console.print("[red]❌ Failed to obtain upload slot from the challenges API.[/red]")
        return None

    console.print(f"✅ Got upload slot. id=[bold]{slot.id}[/bold]")
    console.print("📤 Uploading bundle to storage (presigned URL, direct PUT)...")

    try:
        with open(zip_path, "rb") as f:
            resp = requests.put(
                slot.url,
                data=f,
                headers={"Content-Type": "application/zip"},
                timeout=120,
            )
        if not (200 <= resp.status_code < 300):
            console.print(
                f"[red]❌ Upload failed[/red] (HTTP {resp.status_code}): {resp.text[:500]}"
            )
            return None

        console.print(f"[green]✅ Upload complete[/green] for id={slot.id}")
        return slot.id
    except Exception as e:
        console.print(f"[red]❌ Exception during upload: {e}[/red]")
        return None
    finally:
        # Best effort cleanup of the bundle we created
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass


def _load_validator_keypair(console: Console, name: str | None, hotkey: str | None, path: str | None):
    """Load the hotkey keypair used for JWT signing (same pattern as other CLIs)."""
    import bittensor as bt  # lazy to avoid polluting --help with bt global logging options
    try:
        wallet = bt.Wallet(
            name=name or "validator",
            hotkey=hotkey or "default",
            path=path,
        )
        return wallet.hotkey, wallet.hotkey.ss58_address
    except Exception as e:
        console.print(
            Panel.fit(
                f"Failed to load validator hotkey wallet.\n\n{e}\n\n"
                "Provide --wallet.name and --wallet.hotkey (or set the corresponding env vars that bittensor respects).",
                title="Wallet Error",
                border_style="red",
            )
        )
        raise click.Abort() from e


@click.command("upload-diagnostics")
@click.option("--wallet.name", "wallet_name", default="validator", show_default=True, help="Bittensor wallet name.")
@click.option("--wallet.hotkey", "wallet_hotkey", default="default", show_default=True, help="Bittensor hotkey name (or the SS58).")
@click.option("--wallet.path", "wallet_path", default=None, help="Optional custom wallets directory.")
@click.option("--netuid", type=int, default=63, show_default=True, help="Subnet netuid (used for JWT claims).")
@click.option("--db-path", "db_path", default=None, type=click.Path(exists=False, dir_okay=False), help="Path to your challenge_solutions_*.db (auto-discovered if omitted).")
@click.option("--log-path", "log_paths", multiple=True, help="Path to a pm2 (or other) log file to include. Repeat for multiple files.")
@click.option("--yes", "-y", "assume_yes", is_flag=True, default=False, help="Assume yes to prompts (non-interactive).")
def main(
    wallet_name: str,
    wallet_hotkey: str,
    wallet_path: str | None,
    netuid: int,
    db_path: str | None,
    log_paths: tuple[str, ...],
    assume_yes: bool,
):
    """Interactive (or scripted) uploader for validator DB + pm2 logs.

    Uses the same authenticated upload mechanism the validator uses for solution
    logs (create_verification_upload_url + direct PUT of a zip). The emitted
    upload id can be shared with support.
    """
    console = Console()

    keypair, hotkey_ss58 = _load_validator_keypair(console, wallet_name, wallet_hotkey, wallet_path)
    prefix = hotkey_ss58[:5]

    console.print(
        Panel.fit(
            f"Validator hotkey: ...{prefix} (netuid={netuid})\n"
            f"Challenges API: {_api_cfg.challenges_api_url}",
            title="Enigma Validator Diagnostics Uploader",
            border_style="blue",
        )
    )

    # --- DB location ---
    resolved_db: Path | None = None
    if db_path:
        p = Path(db_path).expanduser().resolve()
        # If user pointed at a sidecar, normalize to the main .db
        if p.name.endswith(("-wal", "-shm")):
            p = p.with_name(p.name.rsplit("-", 1)[0])
        resolved_db = p
        if not resolved_db.exists():
            console.print(f"[yellow]Warning:[/yellow] {resolved_db} does not exist (will still look for sidecars).")
    else:
        resolved_db = _find_default_db(hotkey_ss58)
        if resolved_db:
            console.print(f"Auto-detected DB: {resolved_db}")
        else:
            # Last resort: look under resolved data dir for any challenge_solutions db
            try:
                base = _resolve_db_dir()
                any_dbs = sorted(base.glob("challenge_solutions_*.db"))
                if any_dbs:
                    resolved_db = any_dbs[0]
                    console.print(f"Found DB under data dir: {resolved_db}")
            except Exception:
                pass

    db_files: list[Path] = _collect_db_related_files(resolved_db)

    if not db_files:
        default_guess = str(_resolve_db_dir() / f"challenge_solutions_{prefix}.db")
        answer = Prompt.ask(
            "Path to validator SQLite DB (challenge_solutions_*.db). "
            "Related -wal / -shm files will be included automatically if present.",
            default=default_guess,
            console=console,
        )
        main = Path(answer).expanduser().resolve()
        db_files = _collect_db_related_files(main)
        if not db_files:
            console.print(f"[red]No DB files found at or near {main}[/red]")
            if not assume_yes and not Confirm.ask("Continue without any DB files?", default=False):
                raise click.Abort()

    # --- Log files (interactive + pm2 discovery) ---
    selected_logs: list[Path] = [Path(p).expanduser().resolve() for p in log_paths if p]

    if not selected_logs:
        pm2_found = _discover_pm2_logs()
        if pm2_found:
            console.print("\n[bold]pm2 log files discovered:[/bold]")
            for i, p in enumerate(pm2_found, 1):
                console.print(f"  {i}. {p}")
            if not assume_yes:
                use_them = Confirm.ask("Include the discovered pm2 log(s) above?", default=True)
                if use_them:
                    selected_logs.extend(pm2_found)

    # Always allow the user to add/edit more paths interactively unless --yes + explicit were given
    if not assume_yes:
        while True:
            extra = Prompt.ask(
                "Additional log file to include (blank to finish)",
                default="",
                console=console,
            ).strip()
            if not extra:
                break
            p = Path(extra).expanduser().resolve()
            if p.exists():
                if p not in selected_logs:
                    selected_logs.append(p)
            else:
                console.print(f"[yellow]Note:[/yellow] {p} does not exist (will be skipped if missing).")
                if Confirm.ask("Add it anyway?", default=False):
                    selected_logs.append(p)

    # Filter to those that actually exist
    selected_logs = [p for p in selected_logs if p.exists()]

    if not db_files and not selected_logs:
        console.print("[red]Nothing to upload (no DB files and no log files).[/red]")
        raise click.Abort()

    # Summary
    table = Table(title="Files to be included in the diagnostic bundle")
    table.add_column("Type")
    table.add_column("Path")
    for i, f in enumerate(db_files):
        label = "DB (primary)" if i == 0 else "DB (WAL/SHM)"
        table.add_row(label, str(f))
    for lp in selected_logs:
        table.add_row("log", str(lp))
    console.print(table)

    if not assume_yes:
        if not Confirm.ask("Proceed to zip + upload these files now?", default=True):
            console.print("Aborted by user.")
            return

    # Build client (authenticated)
    client = ChallengesClient(
        keypair=keypair,
        base_url=_api_cfg.challenges_api_url,
        tensorauth_url=_api_cfg.tensorauth_url,
        netuid=netuid,
    )

    # Create zip (we clean it up inside _upload_bundle)
    zip_path = _create_diagnostic_zip(db_files, selected_logs, prefix, console)

    # Upload
    upload_id = _upload_bundle(zip_path, client, console)

    if upload_id:
        console.print(
            Panel.fit(
                f"[bold green]SUCCESS[/bold green]\n\n"
                f"Upload ID: [bold]{upload_id}[/bold]\n\n"
                "Share the Upload ID with support / the team.\n"
                "They will be able to retrieve the bundle (DB + logs) from platform storage.",
                title="Diagnostics Upload Complete",
                border_style="green",
            )
        )
        # Emit just the id on its own line for easy copy / scripting
        print(upload_id)
    else:
        console.print("[red]Upload did not complete successfully. See logs above.[/red]")
        raise click.Abort()


if __name__ == "__main__":
    main()
