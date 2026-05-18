#!/usr/bin/env python3
"""
finance-analysis — SQL-powered personal finance trend tool.

Usage:
  python main.py import <csv_file>
  python main.py query "<natural language question>"
  python main.py analyze "<natural language question>"
  python main.py sql "<raw sql>"
  python main.py budget <category> <monthly_limit>
  python main.py summary
"""

import argparse
import sys
import io
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

# Force UTF-8 output on Windows to handle Unicode characters
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console()


def cmd_import(args):
    from src.database import get_connection
    from src.importer import import_csv
    from src.claude_client import summarize_import

    conn = get_connection()
    try:
        with console.status(f"[bold cyan]Importing {args.file}…"):
            stats = import_csv(conn, args.file)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)

    summary_text = (
        f"File: {stats['file']}\n"
        f"Rows imported: {stats['rows']}\n"
        f"Date range: {stats['date_min']} to {stats['date_max']}\n"
        f"Categories ({stats['category_count']}): {', '.join(stats['categories'] or ['(none)'])}"
    )

    if args.no_ai:
        console.print(Panel(summary_text, title="Import Complete", border_style="green"))
    else:
        with console.status("[bold cyan]Asking Claude to summarize…"):
            narrative = summarize_import(summary_text)
        console.print(Panel(narrative, title="[green]Import Complete[/green]", border_style="green"))
        console.print(f"[dim]{summary_text}[/dim]")


def cmd_query(args):
    from src.database import get_connection, get_schema_description
    from src.claude_client import generate_sql
    from src.analyzer import execute_query, results_to_table_string, print_rich_table

    conn = get_connection()
    schema = get_schema_description(conn)

    with console.status("[bold cyan]Generating SQL…"):
        sql = generate_sql(args.question, schema)

    console.print(Panel(sql, title="[yellow]Generated SQL[/yellow]", border_style="yellow"))

    try:
        columns, rows = execute_query(conn, sql)
    except Exception as e:
        console.print(f"[bold red]SQL Error:[/bold red] {e}")
        sys.exit(1)

    print_rich_table(columns, rows)


def cmd_analyze(args):
    from src.database import get_connection, get_schema_description
    from src.claude_client import generate_sql, analyze_results
    from src.analyzer import execute_query, results_to_table_string, print_rich_table

    conn = get_connection()
    schema = get_schema_description(conn)

    with console.status("[bold cyan]Generating SQL…"):
        sql = generate_sql(args.question, schema)

    console.print(Panel(sql, title="[yellow]Generated SQL[/yellow]", border_style="yellow"))

    try:
        columns, rows = execute_query(conn, sql)
    except Exception as e:
        console.print(f"[bold red]SQL Error:[/bold red] {e}")
        sys.exit(1)

    print_rich_table(columns, rows)

    table_str = results_to_table_string(columns, rows)
    with console.status("[bold cyan]Claude is analyzing your data…"):
        analysis = analyze_results(args.question, table_str)

    console.print(Panel(analysis, title="[green]Claude's Analysis[/green]", border_style="green"))


def cmd_sql(args):
    from src.database import get_connection
    from src.analyzer import execute_query, print_rich_table

    conn = get_connection()
    try:
        columns, rows = execute_query(conn, args.query)
    except Exception as e:
        console.print(f"[bold red]SQL Error:[/bold red] {e}")
        sys.exit(1)

    print_rich_table(columns, rows)


def cmd_budget(args):
    from src.database import get_connection

    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO budgets (category, monthly_limit) VALUES (?, ?)",
        [args.category, float(args.limit)],
    )
    console.print(f"[green]Budget set:[/green] {args.category} -> {args.limit}/month")


def cmd_summary(args):
    from src.database import get_connection

    conn = get_connection()

    total_rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM transactions").fetchone()
    top_cats = conn.execute(
        "SELECT category, ABS(SUM(amount)) AS total FROM transactions "
        "WHERE amount < 0 AND category IS NOT NULL "
        "GROUP BY category ORDER BY total DESC LIMIT 5"
    ).fetchall()

    lines = [
        f"Total transactions: {total_rows}",
        f"Date range: {date_range[0]} -> {date_range[1]}",
        "",
        "Top 5 spending categories:",
    ]
    for cat, total in top_cats:
        lines.append(f"  {cat or '(uncategorized)'}: {total:,.2f}")

    console.print(Panel("\n".join(lines), title="[cyan]Database Summary[/cyan]", border_style="cyan"))


def main():
    parser = argparse.ArgumentParser(
        prog="finance",
        description="SQL Finance Analysis — powered by DuckDB + Claude",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # import
    p_import = sub.add_parser("import", help="Import a CSV file of transactions")
    p_import.add_argument("file", help="Path to the CSV file")
    p_import.add_argument("--no-ai", action="store_true", help="Skip Claude summary")
    p_import.set_defaults(func=cmd_import)

    # query
    p_query = sub.add_parser("query", help="Ask a question; Claude generates SQL and shows results")
    p_query.add_argument("question", help="Natural language question")
    p_query.set_defaults(func=cmd_query)

    # analyze
    p_analyze = sub.add_parser("analyze", help="Ask a question; Claude generates SQL + analyzes results")
    p_analyze.add_argument("question", help="Natural language question")
    p_analyze.set_defaults(func=cmd_analyze)

    # sql
    p_sql = sub.add_parser("sql", help="Run raw SQL directly")
    p_sql.add_argument("query", help="SQL query string")
    p_sql.set_defaults(func=cmd_sql)

    # budget
    p_budget = sub.add_parser("budget", help="Set a monthly budget for a category")
    p_budget.add_argument("category", help="Category name")
    p_budget.add_argument("limit", help="Monthly spending limit (numeric)")
    p_budget.set_defaults(func=cmd_budget)

    # summary
    p_summary = sub.add_parser("summary", help="Show database overview")
    p_summary.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
