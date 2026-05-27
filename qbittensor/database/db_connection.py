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

import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .base import Base
import bittensor as bt

from .validator.db_query import DBQuery
from .miner.db_query import DBQueryMiner


def _package_fallback_project_root() -> Path:
    """Fallback using the location of this module inside the installed qbittensor package."""
    return Path(__file__).resolve().parents[2]


def _first_project_root_walking_up(start: Path) -> Path | None:
    """First ancestor of ``start`` (including ``start``) that contains ``qbittensor/database/``.

    This is used by the DB directory resolution heuristics to find the correct
    source checkout when a developer has multiple editable installs or runs
    neurons/CLIs from a different tree than the one that was ``pip install -e``'d.
    """
    here = start.resolve()
    for base in (here, *here.parents):
        if (base / "qbittensor" / "database").is_dir():
            return base
    return None


def _project_root_from_argv0() -> Path | None:
    """Resolve repo root from the invoked entrypoint (works when :func:`os.getcwd` is wrong).

    When you run ``python path/to/mine_enigma.py`` (or a neuron script), ``sys.argv[0]``
    ties the DB location to that checkout even if another editable install of qbittensor
    is on PYTHONPATH.

    Falls back to ``__main__.__file__`` (when available) to handle certain launcher /
    runpy / multiprocessing scenarios where argv[0] is not reliable but the actual
    executed source file is still known.
    """
    if not sys.argv:
        return None
    raw = Path(sys.argv[0])
    try:
        resolved = raw.resolve()
    except (OSError, RuntimeError):
        resolved = None
    if resolved and resolved.exists():
        start = resolved if resolved.is_dir() else resolved.parent
        root = _first_project_root_walking_up(start)
        if root is not None:
            return root

    # Fallback: use the __main__ module's __file__ when argv[0] didn't help.
    # This covers some exotic launch paths while still preferring the explicit
    # entrypoint recorded in sys.argv[0].
    try:
        import __main__

        if hasattr(__main__, "__file__") and __main__.__file__:
            main_path = Path(__main__.__file__)
            if main_path.exists():
                start = main_path if main_path.is_dir() else main_path.parent
                root = _first_project_root_walking_up(start)
                if root is not None:
                    return root
    except Exception:
        pass

    return None


def _project_root_from_cwd() -> Path | None:
    """Closest ancestor of :func:`Path.cwd` containing ``qbittensor/database/`` (innermost match).

    Using the **first** hit when walking cwd → parents avoids picking a higher-level
    folder that also happens to ship a qbittensor tree (e.g. a subnets umbrella).
    """
    return _first_project_root_walking_up(Path.cwd())


def _resolve_db_dir() -> Path:
    """``<project-root>/data`` where project root is detected structurally (name-agnostic)."""
    data_dir = os.environ.get("ENIGMA_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().resolve()

    repo_override = os.environ.get("ENIGMA_REPO_ROOT")
    if repo_override:
        return Path(repo_override).expanduser().resolve() / "data"

    root = _project_root_from_argv0() or _project_root_from_cwd()
    if root is not None:
        return root / "data"

    return _package_fallback_project_root() / "data"


def _enable_sqlite_foreign_keys(engine) -> None:
    """SQLite ignores FOREIGN KEY unless this pragma is set on each connection."""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _enable_sqlite_wal_mode(engine) -> None:
    """
    Enable Write-Ahead Logging (WAL) mode for better concurrency.

    This allows readers (like check-validation CLI) to work while the
    validator or miner is writing, greatly reducing "database is locked" errors.
    """

    @event.listens_for(engine, "connect")
    def _set_wal_pragma(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


class DBConnection:

    # `database_name` is either "challenge_solutions" or "miner_submissions"

    def __init__(self, database_name_prefix: str, hotkey: str):
        self.database_name_prefix = database_name_prefix
        DB_DIR = _resolve_db_dir()
        DB_NAME = f'{database_name_prefix}_{hotkey[0:5]}.db'
        os.makedirs(DB_DIR, exist_ok=True)

        self.DB_PATH = str(DB_DIR / DB_NAME)
        self.DATABASE_URL = f"sqlite:///{self.DB_PATH}"
        self.create_database()

        if database_name_prefix == "challenge_solutions":
            self.db_query = DBQuery(self.get_db_session)
        elif database_name_prefix == "miner_submissions":
            self.db_query_miner = DBQueryMiner(self.get_db_session)
        else:
            bt.logging.error(
                f"Invalid database name: {database_name_prefix}. Must be one of "
                "'challenge_solutions' or 'miner_submissions'."
            )

        # self.db_query = DBQuery(self.get_db_session)

    def create_database(self):
        """Create the database file and tables."""
        bt.logging.info(f"📂 Creating database at '{self.DB_PATH}'")
        DATABASE_URL = f"sqlite:///{self.DB_PATH}"
        engine = create_engine(DATABASE_URL, echo=False)
        _enable_sqlite_foreign_keys(engine)
        _enable_sqlite_wal_mode(engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        try:
            # Only create tables for this DB — shared Base.metadata includes every
            # imported model (miner + challenge), which would otherwise create all of them.
            if self.database_name_prefix == "miner_submissions":
                from .miner.db_models import MinerSubmission, MinerSubmissionStatus

                tables = [
                    MinerSubmission.__table__,
                    MinerSubmissionStatus.__table__,
                ]
            elif self.database_name_prefix == "challenge_solutions":
                from .validator.db_models import ChallengeSolution, MinerMaintenanceIncentive

                tables = [
                    ChallengeSolution.__table__,
                    MinerMaintenanceIncentive.__table__,
                ]
            else:
                tables = list(Base.metadata.tables.values())

            Base.metadata.create_all(bind=engine, tables=tables)
            bt.logging.info(f"✅ Database created successfully at '{self.DB_PATH}'")
            return True
        except Exception as e:
            bt.logging.error(f" ❌ Error creating database: {e}")
            return False

    def get_db_session(self):
        """Get a SQLAlchemy database session."""
        return self.SessionLocal()
