"""DuckDB backend that mimics pymysql's Connection/Cursor API.

When WHERE=local in .env, this module provides a drop-in replacement
for pymysql. Import it in your _common.py / _core.py — caller code
doesn't change.

Usage in your shared modules:
    from running_locally.local_db import Where, get_connection
    where = Where.from_env()
    conn = get_connection(where)
"""

import os
import re
from enum import Enum

import duckdb


class Where(Enum):
    LOCAL = "local"
    SERVER = "server"

    @classmethod
    def from_env(cls) -> "Where":
        val = os.getenv("WHERE", "server").lower()
        return cls(val)


# ── Path to Parquet sample data (relative to your project root) ──────────

_DATA_DIR = "data/sample"


# ── DuckDB cursor (mimics pymysql.cursors.Cursor) ─────────────────────────

class _Cursor:
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn
        self._result = None
        self.description = None

    def execute(self, sql: str, params=None):
        sql = _adapt_sql(sql)
        if params:
            sql = _interpolate(sql, params)
        self._result = self._conn.sql(sql)
        if self._result is not None:
            self.description = [(col,) for col in self._result.columns]
        return self

    def fetchall(self):
        if self._result is None:
            return []
        return [tuple(row) for row in self._result.fetchall()]

    def fetchone(self):
        if self._result is None:
            return None
        rows = self._result.fetchone()
        return tuple(rows) if rows else None

    def executemany(self, sql: str, seq_of_params):
        for params in seq_of_params:
            self.execute(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ── DuckDB connection (mimics pymysql.Connection) ─────────────────────────

class _Connection:
    def __init__(self, db: duckdb.DuckDBPyConnection):
        self._conn = db

    def cursor(self):
        return _Cursor(self._conn)

    def commit(self):
        pass

    def close(self):
        self._conn.close()


# ── Public factory ────────────────────────────────────────────────────────

def get_connection(where: Where, repo_root: str = "."):
    """Return a pymysql Connection (server) or DuckDB _Connection (local)."""
    if where == Where.LOCAL:
        db = _init_duckdb(repo_root)
        return _Connection(db)
    else:
        import pymysql
        from dotenv import load_dotenv
        load_dotenv(f"{repo_root}/.env")
        return pymysql.connect(
            host=os.environ["DATABASE_HOST"],
            port=int(os.environ["DATABASE_PORT"]),
            user=os.environ["DATABASE_USER"],
            password=os.environ["PAU_PASSWORD"],
            database="bsky",
            charset="utf8mb4",
        )


# ── DuckDB initialisation (register Parquet files as tables) ──────────────

def _init_duckdb(repo_root: str) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect()
    data = f"{repo_root}/{_DATA_DIR}"

    db.execute(f"""
        CREATE OR REPLACE VIEW bsky_records AS
        SELECT * FROM read_parquet('{data}/records.parquet')
    """)
    db.execute(f"""
        CREATE OR REPLACE VIEW bsky_posts AS
        SELECT * FROM read_parquet('{data}/posts.parquet')
    """)

    return db


# ── SQL adaptation ────────────────────────────────────────────────────────

def _adapt_sql(sql: str) -> str:
    """Translate StarRocks SQL to DuckDB-compatible SQL."""
    sql = sql.replace("bsky.records", "bsky_records")
    sql = sql.replace("bsky.posts", "bsky_posts")
    sql = sql.replace("pau_db.", "")      # local: no schema prefix
    sql = sql.replace("%s", "?")           # pymysql → duckdb placeholder
    # Strip StarRocks engine clauses (DuckDB doesn't need them)
    sql = re.sub(r"\s*ENGINE\s*=\s*OLAP.*?(?=DISTRIBUTED|PROPERTIES|;|$)",
                 "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*DUPLICATE KEY\([^)]*\)", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*DISTRIBUTED BY HASH\([^)]*\)\s*BUCKETS\s*\d+", "",
                 sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*PROPERTIES\s*\([^)]*\)", "", sql, flags=re.IGNORECASE)
    return sql


def _interpolate(sql: str, params) -> str:
    """Replace ? placeholders with literal values."""
    result = []
    it = iter(params)
    for ch in sql:
        if ch == "?":
            val = next(it)
            result.append(_literal(val))
        else:
            result.append(ch)
    return "".join(result)


def _literal(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"
