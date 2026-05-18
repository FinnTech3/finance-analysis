import os
import duckdb
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "finance.duckdb"
SCHEMA_PATH = BASE_DIR / "sql" / "schema.sql"


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    _ensure_schema(conn)
    return conn


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
