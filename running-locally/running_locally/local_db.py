"""DuckDB backend that mimics pymysql's Connection/Cursor API.

When WHERE=local in .env, this module provides a drop-in replacement
for pymysql. SQL is written in StarRocks dialect (canonical) and
transpiled to DuckDB via sqlglot when running locally. On server,
SQL passes through unchanged.

Schema names match the StarRocks databases (bsky, pau_db) so no
name mapping is needed — ``bsky.records`` works in both environments.

Usage:
    from running_locally.local_db import Where, get_connection
    where = Where.from_env()
    conn = get_connection(where)
"""

import logging
import os
from enum import Enum

import duckdb
import sqlglot

# Suppress sqlglot's "Unsupported property" warnings for StarRocks DDL
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class Where(Enum):
    LOCAL = "local"
    SERVER = "server"

    @classmethod
    def from_env(cls) -> "Where":
        val = os.getenv("WHERE", "server").lower()
        return cls(val)


# ── Schema ↔ parquet layout ──────────────────────────────────────────────
# Each subdirectory of DATA_DIR is a schema, each .parquet file a table.
# e.g. data/tables/bsky/records.parquet → bsky.records (view)

_DATA_DIR = "data/tables"


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


# ── Unified connection wrapper (works with DuckDB and pymysql) ────────────

class _Connection:
    def __init__(self, db, backend: str = "duckdb"):
        self._conn = db
        self._backend = backend

    def cursor(self):
        if self._backend == "duckdb":
            return _Cursor(self._conn)
        else:
            return self._conn.cursor()

    def commit(self):
        if self._backend == "mysql":
            self._conn.commit()

    def close(self):
        self._conn.close()

    def query(self, sql: str):
        with self.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    def execute(self, sql: str, label: str = ""):
        if label:
            print(f"  {label}...", end=" ", flush=True)
        with self.cursor() as cur:
            cur.execute(sql)
        if self._backend == "mysql":
            self._conn.commit()
        if label:
            print("done.")


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
        db = pymysql.connect(
            host=os.environ["DATABASE_HOST"],
            port=int(os.environ["DATABASE_PORT"]),
            user=os.environ["DATABASE_USER"],
            password=os.environ["PAU_PASSWORD"],
            database="bsky",
            charset="utf8mb4",
        )
        return _Connection(db, backend="mysql")


# ── DuckDB initialisation ─────────────────────────────────────────────────

def _init_duckdb(repo_root: str) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect(f"{repo_root}/data/local.duckdb")
    data = f"{repo_root}/{_DATA_DIR}"

    # Create schemas matching StarRocks databases
    db.execute("CREATE SCHEMA IF NOT EXISTS bsky")
    db.execute("CREATE SCHEMA IF NOT EXISTS pau_db")

    # Register parquet files as views inside the bsky schema.
    # Table name = basename of the parquet file (strips .parquet).
    for schema_name, table_name in _discover_tables(data):
        path = f"{data}/{schema_name}/{table_name}.parquet"
        db.execute(f"""
            CREATE OR REPLACE VIEW {schema_name}.{table_name} AS
            SELECT * FROM read_parquet('{path}')
        """)

    return db


def _discover_tables(data_dir: str) -> list[tuple[str, str]]:
    """Yield (schema, table_name) for each .parquet file under data_dir."""
    import glob
    tables = []
    for path in sorted(glob.glob(f"{data_dir}/*/*.parquet")):
        parts = path[len(data_dir) + 1:].split("/")  # e.g. "bsky/records.parquet"
        schema = parts[0]
        table = parts[1].removesuffix(".parquet")
        tables.append((schema, table))
    return tables


# ── SQL adaptation ────────────────────────────────────────────────────────

def _adapt_sql(sql: str) -> str:
    """Transpile StarRocks SQL → DuckDB SQL.

    DuckDB schemas mirror StarRocks database names (bsky, pau_db), so
    ``bsky.records`` works in both. The ``pau_db.`` prefix is stripped
    because local table-creation scripts don't use it (tables land in the
    default schema instead of the pau_db schema).
    """
    # %s → ? first: sqlglot parses ? as a parameter, chokes on %s (modulo)
    sql = sql.replace("%s", "?")
    sql = sqlglot.transpile(sql, read="starrocks", write="duckdb")[0]
    # ponytail: pau_db schema exists but table creation doesn't use it;
    # strip the prefix so queries resolve against default schema.
    # Remove when table-creation scripts prefix with pau_db.
    sql = sql.replace("pau_db.", "")
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
