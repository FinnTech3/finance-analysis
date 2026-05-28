import os
import time
import duckdb
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "finance.duckdb"
SCHEMA_PATH = BASE_DIR / "sql" / "schema.sql"


def get_connection() -> duckdb.DuckDBPyConnection:
    """Read-write connection with the schema ensured. Used by the CLI and by the
    privileged web paths (import / sample load / summary). Callers in the web app
    should prefer the `rw_connection()` context manager so the file lock is
    released promptly — a lingering read-write handle would block the hardened
    read-only connection used for user queries."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    _ensure_schema(conn)
    return conn


@contextmanager
def rw_connection():
    """Context-managed read-write connection that always closes."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def ro_connection(retries: int = 4, backoff_s: float = 0.15):
    """
    Hardened, read-only connection for executing user-influenced SQL.

    Two protections are baked in at connect time and cannot be undone from SQL:
      • read_only=True            → no INSERT/UPDATE/DELETE/DROP/COPY-TO/ATTACH-write
      • enable_external_access=0  → no read_csv/read_text/httpfs/INSTALL/LOAD,
                                    so the filesystem and network are unreachable
                                    (blocks .env exfiltration and SSRF). DuckDB
                                    treats this flag as a one-way latch.

    A brief retry absorbs the rare lock contention with a concurrent writer.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Make sure the database file exists and has a schema before a read-only open.
    if not DB_PATH.exists():
        get_connection().close()

    last_err = None
    for attempt in range(retries):
        try:
            conn = duckdb.connect(
                str(DB_PATH),
                read_only=True,
                config={"enable_external_access": "false"},
            )
            break
        except Exception as e:  # pragma: no cover - lock contention path
            last_err = e
            time.sleep(backoff_s * (attempt + 1))
    else:
        raise last_err

    try:
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    schema_sql = SCHEMA_PATH.read_text()
    conn.execute(schema_sql)


def get_schema_description(conn: duckdb.DuckDBPyConnection) -> str:
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()

    parts = []
    for (table,) in tables:
        cols = conn.execute(
            f"SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name = '{table}' ORDER BY ordinal_position"
        ).fetchall()
        col_lines = "\n".join(f"  {name} {dtype}" for name, dtype in cols)
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        parts.append(f"Table: {table} ({row_count} rows)\n{col_lines}")

    return "\n\n".join(parts) if parts else "No tables found."
