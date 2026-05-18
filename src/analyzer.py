import duckdb
from rich.table import Table
from rich.console import Console

_console = Console()


def execute_query(conn: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[tuple]]:
    result = conn.execute(sql)
    columns = [desc[0] for desc in result.description]
    rows = result.fetchall()
    return columns, rows


def results_to_table_string(columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "No results returned."

    col_widths = [len(c) for c in columns]
    str_rows = []
    for row in rows:
        str_row = [str(v) if v is not None else "NULL" for v in row]
        for i, cell in enumerate(str_row):
            col_widths[i] = max(col_widths[i], len(cell))
        str_rows.append(str_row)

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header = "| " + " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(columns)) + " |"
    lines = [sep, header, sep]
    for str_row in str_rows:
        lines.append("| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(str_row)) + " |")
    lines.append(sep)
    lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


def print_rich_table(columns: list[str], rows: list[tuple]) -> None:
    table = Table(show_header=True, header_style="bold cyan")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(v) if v is not None else "NULL" for v in row])
    _console.print(table)
