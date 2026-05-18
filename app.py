#!/usr/bin/env python3
"""
FastAPI web server for the finance analysis tool.
Run: python app.py   (or: uvicorn app:app --reload)
"""

import io
import sys
import tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.database import get_connection, get_schema_description
from src.importer import import_csv
from src.analyzer import execute_query, results_to_table_string
from src.claude_client import generate_sql, analyze_results, summarize_import

app = FastAPI(title="Finance Analyser")

# CORS — allow the portfolio site (and any origin during dev) to embed the app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to portfolio domain once deployed
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Serve the dashboard SPA
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return FileResponse("static/index.html")


# ── Models ──────────────────────────────────────────────────────────────

class QuestionRequest(BaseModel):
    question: str


# ── Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/summary")
def api_summary():
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM transactions").fetchone()
    top_cats = conn.execute(
        "SELECT category, ABS(SUM(amount)) AS total FROM transactions "
        "WHERE amount < 0 AND category IS NOT NULL "
        "GROUP BY category ORDER BY total DESC LIMIT 6"
    ).fetchall()
    monthly = conn.execute(
        "SELECT strftime(date, '%Y-%m') AS month, SUM(amount) AS net "
        "FROM transactions GROUP BY month ORDER BY month DESC LIMIT 12"
    ).fetchall()

    return {
        "total_transactions": total,
        "date_min": str(date_range[0]) if date_range[0] else None,
        "date_max": str(date_range[1]) if date_range[1] else None,
        "top_categories": [{"category": r[0] or "(uncategorized)", "total": round(float(r[1]), 2)} for r in top_cats],
        "monthly_net": [{"month": r[0], "net": round(float(r[1]), 2)} for r in monthly],
    }


@app.post("/api/query")
def api_query(req: QuestionRequest):
    conn = get_connection()
    schema = get_schema_description(conn)
    try:
        sql = generate_sql(req.question, schema)
        columns, rows = execute_query(conn, sql)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "sql": sql,
        "columns": columns,
        "rows": [[str(v) if v is not None else None for v in row] for row in rows],
    }


@app.post("/api/analyze")
def api_analyze(req: QuestionRequest):
    conn = get_connection()
    schema = get_schema_description(conn)
    try:
        sql = generate_sql(req.question, schema)
        columns, rows = execute_query(conn, sql)
        table_str = results_to_table_string(columns, rows)
        analysis = analyze_results(req.question, table_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "sql": sql,
        "columns": columns,
        "rows": [[str(v) if v is not None else None for v in row] for row in rows],
        "analysis": analysis,
    }


@app.post("/api/import")
async def api_import(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    conn = get_connection()
    try:
        stats = import_csv(conn, tmp_path)
        narrative = summarize_import(
            f"File: {stats['file']}\n"
            f"Rows imported: {stats['rows']}\n"
            f"Date range: {stats['date_min']} to {stats['date_max']}\n"
            f"Categories ({stats['category_count']}): {', '.join(stats['categories'] or ['(none)'])}"
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {"stats": stats, "narrative": narrative}


@app.post("/api/load-sample/{n}")
def api_load_sample(n: int):
    if n not in range(1, 6):
        raise HTTPException(status_code=404, detail="Sample not found")
    csv_path = Path(__file__).resolve().parent / "static" / "data" / f"sample-{n}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="Sample CSV not found")
    conn = get_connection()
    conn.execute("DELETE FROM transactions")
    try:
        stats = import_csv(conn, str(csv_path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"loaded": True, "rows": stats["rows"], "sample": n}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
