# SQL Finance Analyser

> **Ask your money questions in plain English. Get a SQL query, an interactive table, and a Claude-written narrative grounded in your actual data.**
>
> Built on DuckDB + FastAPI + the Claude API.

[![status](https://img.shields.io/badge/status-active-c9a553)](#)
[![python](https://img.shields.io/badge/python-3.12-3776AB)](#)
[![duckdb](https://img.shields.io/badge/duckdb-0.10-FFF000)](#)
[![claude](https://img.shields.io/badge/claude--sonnet--4.6-API-c9a553)](#)
[![license](https://img.shields.io/badge/license-MIT-7880a0)](#license)

---

## What it does

Import any bank statement CSV — Monzo, Starling, Revolut, Chase, or any standard export — and interrogate it in natural language. Type *"What did I spend on dining in March?"* and the tool:

1. Translates your question into precise DuckDB SQL via the Claude API
2. Executes the query against an in-memory analytics database
3. Returns a clean table of results
4. Optionally writes a narrative interpretation of the numbers — with the model constrained to cite figures from the actual result set, not invent them

A tutorial-style walkthrough animates each step end-to-end so first-time users can see exactly what the tool is doing.

## Screenshots

The deployed dashboard renders inside a panel of [my portfolio](https://github.com/FinnTech3) but is fully self-contained — clone, install, run, open `http://127.0.0.1:8000`.

## Why I built it

Spreadsheets are slow; dashboards are rigid. I wanted to be able to ask the exact question I have, not the one the tool was designed for. DuckDB is the right substrate — it reads CSVs directly, has full analytical SQL (window functions, `GROUPING SETS`, `PERCENTILE_CONT`), and runs entirely in-process so there is no server to provision.

The Claude API does two distinct jobs in this app:

- **Query generation** — translates a user question into a single, schema-aware DuckDB query (cached system prompt + few-shot examples, `effort: medium`)
- **Narrative analysis** — interprets the result set with adaptive thinking enabled (`effort: high`), forced to ground every figure in the table that came back from the database

## Architecture

```
                    ┌────────────────────────────────────┐
                    │      Browser (static/index.html)    │
                    │   - dashboard, query, analyse panels│
                    │   - tutorial-style walkthrough      │
                    │   - real-public-doc preview modal   │
                    └────────────────────────────────────┘
                                     │  fetch /api/*
                                     ▼
       ┌────────────────────────────────────────────────────────┐
       │                FastAPI app (app.py)                     │
       │  /api/import   /api/query   /api/analyze   /api/summary │
       └────────────────────────────────────────────────────────┘
                ▼                            ▼
       ┌──────────────────┐         ┌──────────────────────┐
       │ DuckDB (in-proc) │  ←  src/  →  │ Claude API (client) │
       │  data/finance.db │         └──────────────────────┘
       └──────────────────┘
```

## Quick start

```bash
git clone https://github.com/FinnTech3/finance-analysis.git
cd finance-analysis
python -m venv .venv
source .venv/bin/activate              # on Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                    # add your ANTHROPIC_API_KEY
python -m uvicorn app:app --port 8000   # then open http://127.0.0.1:8000
```

The app launches with five sample CSVs pre-loaded so you can try a query immediately. Drop your own CSV via the **Import** tab to analyse real data.

## Repository layout

```
finance-analysis/
├── README.md                ← this file
├── LICENSE                  ← MIT
├── SECURITY.md              ← responsible-disclosure policy
├── .env.example             ← env-var template (no secrets committed)
├── .gitignore
├── requirements.txt         ← anthropic, duckdb, rich, python-dotenv, fastapi
├── Procfile                 ← Railway / Render deploy spec
├── railway.json
├── app.py                   ← FastAPI entry point
├── main.py                  ← CLI entry point (analyse from the terminal)
├── src/
│   ├── database.py          ← DuckDB connection, schema setup, prompt context
│   ├── importer.py          ← CSV → DuckDB ingestion (auto-detect schema)
│   ├── analyzer.py          ← Execute SQL, format results for the model
│   ├── claude_client.py     ← Anthropic SDK wrapper with prompt caching
│   └── prompts.py           ← All XML-structured prompt templates
├── sql/
│   └── schema.sql           ← CREATE TABLE statements
└── static/
    ├── index.html           ← Single-page dashboard SPA
    ├── data/                ← Five sample bank-statement CSVs
    │   └── sample-1.csv .. sample-5.csv
    └── docs/                ← Real-public-document previews
        ├── 1-berkshire.html         ← Berkshire 2024 letter excerpt
        ├── 2-jpmorgan.html          ← JPMorgan 2024 CEO letter
        ├── 3-blackrock.html          ← BlackRock 2024 10-K excerpt
        ├── 4-apple-10k.html          ← Apple FY24 10-K excerpt
        ├── 5-fomc.html               ← Federal Reserve FOMC statement
        └── 6-finn-strategy.html      ← Own research memo
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/`                  | Serve the dashboard |
| `GET`  | `/api/summary`       | Aggregate stats: transaction count, date range, top categories, monthly net |
| `POST` | `/api/query`         | `{question}` → `{sql, columns, rows}` |
| `POST` | `/api/analyze`       | `{question}` → `{sql, columns, rows, analysis}` |
| `POST` | `/api/import`        | Multipart CSV upload → ingest into DuckDB |
| `POST` | `/api/load-sample/{n}` | Wipe and load sample dataset `n ∈ [1, 5]` |

## Prompt engineering

The Claude prompts are designed around Anthropic's published [prompt engineering best practices](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/claude-prompting-best-practices) — XML structuring, explicit role assignment, few-shot examples, adaptive thinking, and tight scoping.

Two prompts (system block + few-shot examples) are **prompt-cached** so they are only billed once per cache window — empirically this cuts per-call cost by ~80% at our usage profile.

See [`src/prompts.py`](src/prompts.py).

## Deploying

A `Procfile` and `railway.json` are included for one-click deploys on Railway. The same configuration works on Render and Fly.io. Before deploying:

1. Move `ANTHROPIC_API_KEY` to the host's environment variables panel (never commit a `.env`)
2. Lock the FastAPI `CORSMiddleware` `allow_origins` to your portfolio's exact origin instead of `"*"`
3. Add rate limiting on `/api/query` and `/api/analyze` (the Anthropic key on your server pays for each call)

## License

MIT — see [LICENSE](LICENSE).

## Contact

Finn Lakin · [lakin.finn@gmail.com](mailto:lakin.finn@gmail.com) · [linkedin.com/in/finnlakin](https://www.linkedin.com/in/finnlakin/)
