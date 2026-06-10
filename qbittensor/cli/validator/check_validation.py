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

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from qbittensor.database.db_connection import DBConnection


@click.command("check-validation")
@click.option(
    "--submission",
    "submission_id",
    required=True,
    help="Submission ID to look up in your local validator DB.",
)
@click.option(
    "--hotkey",
    default=None,
    help="Your validator hotkey SS58 (optional). If omitted, we try to auto-detect the DB.",
)
def main(submission_id: str, hotkey: str | None) -> None:
    """Check validation status of a submission using your local validator database.

    This is useful before manually voting on treasury proposals.
    """
    console = Console()

    db_path = _find_validator_db(hotkey, console)
    if not db_path:
        raise click.Abort()

    # Use the prefix from the filename to connect to the DB
    prefix = db_path.name.split("_")[-1].replace(".db", "")
    # We pad the hotkey so DBConnection doesn't complain (it only uses [:5])
    fake_hotkey = prefix + "x" * 48

    console.print(
        Panel.fit(
            f"[bold]Using DB:[/bold] {db_path}",
            title="Validator Local DB",
            border_style="cyan",
        )
    )

    db = DBConnection(database_name_prefix="challenge_solutions", hotkey=fake_hotkey)

    # Retry a few times in case the validator is currently writing
    solution = None
    for attempt in range(5):
        try:
            solution = db.db_query.get_solution_by_submission_id(submission_id)
            break
        except Exception as e:
            if "database is locked" in str(e).lower() and attempt < 4:
                import time
                time.sleep(0.3 * (attempt + 1))
                continue
            raise

    if not solution:
        console.print(f"[yellow]No submission found with ID:[/yellow] {submission_id}")
        return

    table = Table(title=f"Submission: {submission_id}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    table.add_row("Milestone ID", solution.challenge_milestone_id or "-")
    table.add_row("Status", solution.solution_status or "-")
    table.add_row("Tx Hash", solution.tx_hash or "-")
    table.add_row("Miner Hotkey", solution.miner_hotkey or "-")
    table.add_row("Local Path", str(solution.absolute_path_to_solution) or "-")
    table.add_row("Created At", str(solution.created_at) if solution.created_at else "-")
    table.add_row("Last Updated", str(solution.updated_at) if solution.updated_at else "-")

    console.print(table)


def _find_validator_db(hotkey: str | None, console: Console) -> Path | None:
    """Auto-discover the validator's local DB file.

    Respects ENIGMA_DATA_DIR (set via --neuron.data_dir on the validator, or env)
    so DBs in a configurable base directory are discoverable. Falls back to the
    source-tree ./data for legacy/default runs.
    """
    search_dirs: list[Path] = []

    env_data = os.environ.get("ENIGMA_DATA_DIR")
    if env_data:
        search_dirs.append(Path(env_data).expanduser().resolve())

    # Legacy location relative to the package (when running from source tree with default data dir)
    legacy_dir = Path(__file__).parent.parent.parent.parent / "data"
    if not any(str(legacy_dir) == str(d) for d in search_dirs):
        search_dirs.append(legacy_dir)

    candidates: list[Path] = []
    for d in search_dirs:
        if d.exists():
            candidates.extend(sorted(d.glob("challenge_solutions_*.db")))

    # Dedup while preserving order
    seen: set[Path] = set()
    unique_candidates: list[Path] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)
    candidates = unique_candidates

    if not candidates:
        searched = ", ".join(str(d) for d in search_dirs)
        console.print(f"[red]Error:[/red] No validator DB found in {searched}")
        console.print("Run the validator at least once (with your chosen --neuron.data_dir / ENIGMA_DATA_DIR) so it can create its database.")
        return None

    if hotkey:
        prefix = hotkey[:5]
        matches = [p for p in candidates if p.name == f"challenge_solutions_{prefix}.db"]
        if matches:
            return matches[0]
        console.print(f"[red]Error:[/red] No DB found for hotkey prefix '{prefix}'")
        console.print("Available DBs (searched configured data dir + legacy):")
        for p in candidates:
            console.print(f"  - {p.name}")
        return None

    # No hotkey provided → try auto-detection
    if len(candidates) == 1:
        return candidates[0]

    # Multiple DBs found
    console.print("[yellow]Multiple validator databases found:[/yellow]")
    for p in candidates:
        console.print(f"  - {p.name}")

    console.print("\nPlease specify one using [bold]--hotkey <your_full_hotkey>[/bold]")
    return None


if __name__ == "__main__":
    main()
