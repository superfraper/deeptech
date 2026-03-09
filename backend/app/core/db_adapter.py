import os
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row


def is_postgres_enabled() -> bool:
    return os.getenv("DB_BACKEND", "sqlite").lower() == "postgres"


def get_pg_dsn(default_db: str | None = None) -> str:
    user = os.getenv("POSTGRES_USER", "esf")
    password = os.getenv("POSTGRES_PASSWORD", "esfpass")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = default_db or os.getenv("POSTGRES_DB", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _derive_db_name(db_name_or_path: str) -> str:
    # If given a path ending with .db, strip directories and extension
    if db_name_or_path.endswith(".db"):
        base = os.path.basename(db_name_or_path)
        return base[:-3]
    # If it looks like an absolute path, take basename
    if os.path.sep in db_name_or_path:
        return os.path.basename(db_name_or_path)
    return db_name_or_path


@contextmanager
def connect(db_name_or_path: str):
    """
    Context manager yielding a DB-API compatible connection.
    - If DB_BACKEND=postgres: db_name_or_path is interpreted as a Postgres database name.
    - Otherwise: treated as SQLite file path.
    """
    if is_postgres_enabled():
        # Interpret argument as a Postgres database name; derive if a path-like was provided
        dbname = _derive_db_name(db_name_or_path)
        dsn = get_pg_dsn(default_db=dbname)
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            # Enable automatic adaptation close to sqlite behavior
            yield conn
    else:
        with sqlite3.connect(db_name_or_path) as conn:
            # For sqlite return rows as tuples by default; callers use explicit indices
            conn.row_factory = sqlite3.Row
            yield conn


def _adapt_query(query: str) -> str:
    if is_postgres_enabled():
        # Replace SQLite backticks with Postgres double quotes and '?' with '%s'
        return query.replace("`", '"').replace("?", "%s")
    return query


def execute(conn, query: str, params: Iterable[Any] | None = None):
    cur = conn.cursor()
    cur.execute(_adapt_query(query), params or [])
    return cur
