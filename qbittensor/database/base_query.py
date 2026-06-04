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
Base class providing common SQLAlchemy session management helpers
for the DBQuery classes.

This is the foundation for Phase 1 of the DB query layer cleanup:
eliminating the massive repetitive try/except/finally session boilerplate
that was making nearly every method artificially long.
"""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session


class BaseDBQuery:
    """
    Provides a reusable, safe session scope context manager.

    Subclasses (DBQuery, DBQueryMiner) should pass a session factory
    (typically `DBConnection.get_db_session`) to __init__.
    """

    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def session(self):
        """Return a new session from the factory (same API as before)."""
        return self._session_factory

    @contextmanager
    def _managed_session(self, read_only: bool = False) -> Iterator[Session]:
        """
        Context manager that handles the full session lifecycle safely.

        Usage:

            with self._managed_session() as session:
                # do writes
                ...

            with self._managed_session(read_only=True) as session:
                # queries only
                result = session.query(...).first()
                return result

        - Automatically commits on successful exit (unless read_only=True)
        - Rolls back on any exception
        - Always closes the session
        """
        session: Session = self._session_factory()
        try:
            yield session
            if not read_only:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
