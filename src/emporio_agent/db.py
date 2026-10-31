"""Tiny read-only helper shared by the modules that query the SQLite database."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def query(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    """Run a parameterized read-only query and return its rows (columns by name).

    Args:
        db_path: SQLite file built by ``data_loader.build_database``.
        sql: Statement with ``?`` placeholders; values are never formatted into the text.
        params: Values for the placeholders.

    Returns:
        All result rows.
    """
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(sql, params).fetchall()
