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

from pathlib import Path
from unittest.mock import Mock

import pytest

from qbittensor.database.db_connection import (
    DBConnection,
    _first_project_root_walking_up,
    _package_fallback_project_root,
    _resolve_db_dir,
)


class TestResolveDbDir:
    def test_enigma_data_dir_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENIGMA_DATA_DIR", str(tmp_path / "custom_data"))
        monkeypatch.delenv("ENIGMA_REPO_ROOT", raising=False)
        assert _resolve_db_dir() == (tmp_path / "custom_data").resolve()

    def test_data_dir_override_param_takes_precedence(self, tmp_path, monkeypatch):
        """Explicit data_dir arg to _resolve and DBConnection should win over env."""
        monkeypatch.setenv("ENIGMA_DATA_DIR", str(tmp_path / "from_env"))
        override = tmp_path / "from_override"
        assert _resolve_db_dir(data_dir_override=str(override)) == override.resolve()

        # DBConnection should use the passed data_dir for its DB_DIR
        conn = DBConnection("miner_submissions", "5TestHotkey", data_dir=str(override))
        db_path = Path(conn.DB_PATH)
        assert db_path.parent == override.resolve() or db_path.parent.parent == override.resolve() or str(override) in str(db_path)

    def test_enigma_repo_root_override(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("ENIGMA_REPO_ROOT", str(repo))
        monkeypatch.delenv("ENIGMA_DATA_DIR", raising=False)
        assert _resolve_db_dir() == (repo / "data").resolve()

    def test_first_project_root_walking_up(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        (tmp_path / "qbittensor" / "database").mkdir(parents=True)
        assert _first_project_root_walking_up(nested) == tmp_path.resolve()

    def test_package_fallback_exists(self):
        root = _package_fallback_project_root()
        assert (root / "qbittensor" / "database").is_dir()


class TestDBConnection:
    def test_validator_db_creates_challenge_tables(self, validator_db):
        assert validator_db.database_name_prefix == "challenge_solutions"
        assert Path(validator_db.DB_PATH).exists()
        assert hasattr(validator_db, "db_query")

    def test_miner_db_creates_miner_tables(self, miner_db):
        assert miner_db.database_name_prefix == "miner_submissions"
        assert Path(miner_db.DB_PATH).exists()
        assert hasattr(miner_db, "db_query_miner")

    def test_db_filename_uses_hotkey_prefix(self, tmp_data_dir):
        conn = DBConnection("miner_submissions", "5GhLXHotkeyAddress")
        assert conn.DB_PATH.endswith("miner_submissions_5GhLX.db")
