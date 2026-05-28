#!/usr/bin/env python3
"""
FastAPI web server for the finance analysis tool.
Run: python app.py   (or: uvicorn app:app --reload)
"""

import io
import sys
import logging
import tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from src.database import rw_connection, ro_connection, get_schema_description, get_connection
from src.importer import import_csv
from src.analyzer import execute_query, results_to_table_string
from src.claude_client import generate_sql, analyze_results, summarize_import
from src.security import validate_read_only_sql, UnsafeQueryError, enforce_rate_limit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("finance-analyser")

# ── Limits ───────────────────────────────────────────────────────────────
MAX_UPLOAD_BYTES = 5 * 1024 * 1024          # 5 MB — caps memory use on the free tier
ALLOWED_UPLOAD_SUFFIXES = {".csv", ".txt"}

# ── Who may embed this app in an <iframe>, and call it cross-origin ────────
ALLOWED_ORIGINS = [
    "https://finn-lakin-portfolio.netlify.app",
    "https://finnlakin.com",
    "https://www.finnlakin.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
FRAME_ANCESTORS = (
    "'self' https://finn-lakin-portfolio.netlify.app https://*.netlify.app "
    "https://finnlakin.com https://www.finnlakin.com"
)

app = FastAPI(title="Finance Analyser", docs_url=None, redoc_url=None, openapi_url=None)


# ── Security headers (incl. clickjacking control via frame-ancestors) ──────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            f"frame-ancestors {FRAME_ANCESTORS}"
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# CORS — restricted to the portfolio origins. The iframe embed itself is
# same-origin (the SPA calls its own /api/*), so this only governs any
# deliberate cross-origin use.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    max_age=600,
)

# Serve the dashboard SPA
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Cold-start bootstrap ────────────────────────────────────────────────
# Render's free tier filesystem is ephemeral. Each time the instance spins up,
# the DuckDB file is gone — so on first request we auto-load sample-1 to make
# the demo immediately populated. The connection is closed straight away so it
# does not hold the file lock that the read-only query path needs.
def _bootstrap_sample_data() -> None:
    try:
        with rw_connection() as conn:
            existing = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            if existing == 0:
                sample_path = Path(__file__).resolve().parent / "static" / "data" / "sample-1.csv"
                if sample_path.exists():
                    import_csv(conn, str(sample_path))
                    log.info("Auto-loaded sample-1.csv into empty database")
                else:
                    log.info("No sample data found at %s", sample_path)
            else:
                log.info("Database already has %s transactions — skipping bootstrap", existing)
    except Exception as e:
        log.warning("Bootstrap failed (non-fatal): %s", e)


_bootstrap_sample_data()


@app.get("/")
def root():
    return FileResponse("static/index.html")


# ── Models ──────────────────────────────────────────────────────────────

class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


# ── Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/summary")
def api_summary():
    # Unauthenticated, no Claude call, and used as the Render health check —
    # intentionally left unthrottled.
    with ro_connection() as conn:
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
def api_query(req: QuestionRequest, request: Request):
    enforce_rate_limit(request, "query", counts_against_daily=True)
    try:
        with ro_connection() as conn:
            schema = get_schema_description(conn)
            sql = generate_sql(req.question, schema)
            sql = validate_read_only_sql(sql)
            columns, rows = execute_query(conn, sql)
    except UnsafeQueryError as e:
        log.warning("Blocked unsafe query: %s", e)
        raise HTTPException(status_code=400, detail="That question produced a query that was blocked for safety. Try rephrasing it.")
    except Exception as e:
        log.exception("Query failed")
        raise HTTPException(status_code=400, detail="Could not run that query. Try rephrasing your question.")

    return {
        "sql": sql,
        "columns": columns,
        "rows": [[str(v) if v is not None else None for v in row] for row in rows],
    }


@app.post("/api/analyze")
def api_analyze(req: QuestionRequest, request: Request):
    enforce_rate_limit(request, "analyze", counts_against_daily=True)
    try:
        with ro_connection() as conn:
            schema = get_schema_description(conn)
            sql = generate_sql(req.question, schema)
            sql = validate_read_only_sql(sql)
            columns, rows = execute_query(conn, sql)
            table_str = results_to_table_string(columns, rows)
        analysis = analyze_results(req.question, table_str)
    except UnsafeQueryError as e:
        log.warning("Blocked unsafe query: %s", e)
        raise HTTPException(status_code=400, detail="That question produced a query that was blocked for safety. Try rephrasing it.")
    except Exception as e:
        log.exception("Analyze failed")
        raise HTTPException(status_code=400, detail="Could not analyse that question. Try rephrasing it.")

    return {
        "sql": sql,
        "columns": columns,
        "rows": [[str(v) if v is not None else None for v in row] for row in rows],
        "analysis": analysis,
    }


@app.post("/api/import")
async def api_import(request: Request, file: UploadFile = File(...)):
    enforce_rate_limit(request, "import", counts_against_daily=True)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    # Stream to disk with a hard size cap so a large upload cannot exhaust memory.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp_path = tmp.name
            read_total = 0
            while True:
                chunk = await file.read(1024 * 256)
                if not chunk:
                    break
                read_total += len(chunk)
                if read_total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File is too large (5 MB limit).")
                tmp.write(chunk)

        with rw_connection() as conn:
            stats = import_csv(conn, tmp_path)
            narrative = summarize_import(
                f"File: {stats['file']}\n"
                f"Rows imported: {stats['rows']}\n"
                f"Date range: {stats['date_min']} to {stats['date_max']}\n"
                f"Categories ({stats['category_count']}): {', '.join(stats['categories'] or ['(none)'])}"
            )
    except HTTPException:
        raise
    except (FileNotFoundError, ValueError) as e:
        # These carry user-actionable, non-sensitive messages (e.g. missing columns).
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        log.exception("Import failed")
        raise HTTPException(status_code=400, detail="Could not import that file.")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    return {"stats": stats, "narrative": narrative}


@app.post("/api/load-sample/{n}")
def api_load_sample(n: int, request: Request):
    enforce_rate_limit(request, "sample", counts_against_daily=False)
    if n not in range(1, 6):
        raise HTTPException(status_code=404, detail="Sample not found")
    csv_path = Path(__file__).resolve().parent / "static" / "data" / f"sample-{n}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="Sample not found")
    try:
        with rw_connection() as conn:
            conn.execute("DELETE FROM transactions")
            stats = import_csv(conn, str(csv_path))
    except Exception:
        log.exception("Sample load failed")
        raise HTTPException(status_code=400, detail="Could not load that sample.")
    return {"loaded": True, "rows": stats["rows"], "sample": n}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
