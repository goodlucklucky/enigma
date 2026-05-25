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

import pytest

from qbittensor.database.database_manager import DatabaseManager


@pytest.fixture
def db_manager(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return DatabaseManager("test_db")


class TestDatabaseManager:
    def test_create_table_and_query(self, db_manager):
        db_manager.query_and_commit(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"
        )
        db_manager.query_and_commit_with_values(
            "INSERT INTO items (name) VALUES (?)", ("alpha",)
        )
        rows = db_manager.query("SELECT name FROM items")
        assert rows == [("alpha",)]

    def test_query_with_values(self, db_manager):
        db_manager.query_and_commit(
            "CREATE TABLE nums (val INTEGER)"
        )
        db_manager.query_and_commit_many(
            "INSERT INTO nums (val) VALUES (?)", [(1,), (2,), (3,)]
        )
        rows = db_manager.query_with_values("SELECT val FROM nums WHERE val > ?", (1,))
        assert rows == [(2,), (3,)]

    def test_row_exists(self, db_manager):
        db_manager.query_and_commit(
            "CREATE TABLE t (id INTEGER PRIMARY KEY, x TEXT)"
        )
        db_manager.query_and_commit_with_values(
            "INSERT INTO t (x) VALUES (?)", ("found",)
        )
        assert db_manager.row_exists("t", "x = ?", ("found",)) is True
        assert db_manager.row_exists("t", "x = ?", ("missing",)) is False

    def test_table_exists_and_size(self, db_manager):
        db_manager.query_and_commit(
            "CREATE TABLE sized (id INTEGER)"
        )
        db_manager.query_and_commit("INSERT INTO sized DEFAULT VALUES")
        assert db_manager.table_exists("sized") is True
        assert db_manager.get_size_of_table("sized") == 1

    def test_query_one_with_values(self, db_manager):
        db_manager.query_and_commit(
            "CREATE TABLE one (id INTEGER PRIMARY KEY, v TEXT)"
        )
        db_manager.query_and_commit_with_values(
            "INSERT INTO one (v) VALUES (?)", ("solo",)
        )
        row = db_manager.query_one_with_values("SELECT v FROM one WHERE v = ?", ("solo",))
        assert row == ("solo",)
