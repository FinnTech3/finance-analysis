import duckdb
from pathlib import Path


REQUIRED_COLUMNS = {"date", "amount"}

COLUMN_ALIASES = {
    "transaction_date": "date",
    "trans_date": "date",
    "value_date": "date",
    "debit": "amount",
    "credit": "amount",
    "sum": "amount",
    "total": "amount",
    "merchant": "description",
    "memo": "description",
    "narration": "description",
    "details": "description",
    "type": "category",
    "label": "category",
    "bank": "account",
    "bank_account": "account",
}


def import_csv(conn: duckdb.DuckDBPyConnection, csv_path: str) -> dict:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    # Peek at the CSV headers
    peek = conn.execute(
        f"SELECT * FROM read_csv_auto('{path.as_posix()}', header=true) LIMIT 0"
    )
    raw_cols = [desc[0].lower() for desc in peek.description]

    # Build a column mapping: raw → canonical
    col_map = {}
    for raw in raw_cols:
        canonical = COLUMN_ALIASES.get(raw, raw)
        col_map[raw] = canonical

    canonical_cols = set(col_map.values())
    missing = REQUIRED_COLUMNS - canonical_cols
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {missing}. "
            f"Found: {list(raw_cols)}"
        )

    # Build SELECT with renames
    select_parts = []
    seen_canonical = set()
    for raw, canonical in col_map.items():
        if canonical not in seen_canonical:
            select_parts.append(f'"{raw}" AS {canonical}')
            seen_canonical.add(canonical)

    for needed in ["category", "description", "account"]:
        if needed not in seen_canonical:
            select_parts.append(f"NULL AS {needed}")

    source_name = path.name.replace("'", "''")
    select_sql = ", ".join(select_parts)

    insert_sql = f"""
        INSERT INTO transactions (date, amount, category, description, account, source_file)
        SELECT
            TRY_CAST(date AS DATE),
            TRY_CAST(amount AS DECIMAL(10,2)),
            category,
            description,
            account,
            '{source_name}'
        FROM (
            SELECT {select_sql}
            FROM read_csv_auto('{path.as_posix()}', header=true)
        ) sub
        WHERE TRY_CAST(date AS DATE) IS NOT NULL
          AND TRY_CAST(amount AS DECIMAL(10,2)) IS NOT NULL
    """
    conn.execute(insert_sql)

    stats = conn.execute(
        f"SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT category) "
        f"FROM transactions WHERE source_file = '{source_name}'"
    ).fetchone()

    categories = conn.execute(
        f"SELECT DISTINCT category FROM transactions WHERE source_file = '{source_name}' "
        f"AND category IS NOT NULL ORDER BY category"
    ).fetchall()

    return {
        "file": path.name,
        "rows": stats[0],
        "date_min": str(stats[1]),
        "date_max": str(stats[2]),
        "category_count": stats[3],
        "categories": [c[0] for c in categories],
    }
