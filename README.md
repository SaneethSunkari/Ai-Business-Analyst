# AI Data Middleware

> **Middleware that connects any database to any AI agent.**  
> Ask questions in plain English. Get SQL-backed answers instantly.

![Python](https://img.shields.io/badge/Python-3.12%2B-3776ab?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4.1--mini-412991?style=flat-square&logo=openai&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ed?style=flat-square&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

---

## Demo

🎥 Watch the system in action (2 min):  
👉 https://youtu.be/hMyuesQavrM

This demo shows:
- connecting to a live database
- asking natural language questions
- SQL generation and execution
- real results returned from the database

---


## What it does

Most AI projects fail because they can't reliably access enterprise data. This middleware solves that:

```
Your Question (plain English)
        │
        ▼
┌───────────────────────┐
│   Schema Introspection │  ← reads live table structure
│   AI SQL Generation    │  ← GPT-4.1-mini, dialect-aware
│   SQL Validation       │  ← read-only enforced
│   Query Execution      │  ← rows returned as JSON
└───────────────────────┘
        │
        ▼
  Clean tabular results
```

Any AI agent (OpenAI, Claude, LangChain, AutoGen) can plug into this middleware via the **OpenAI function-calling manifest** at `GET /tools/manifest` — no custom integration code needed.

---

## Features

- **Natural language → SQL** — ask questions in plain English; GPT-4.1-mini generates dialect-aware SQL against your live schema
- **Multi-database** — PostgreSQL, MySQL, SQLite (one `db_type` field)
- **Saved connections** — register credentials once, reuse a `connection_id` across all calls
- **Agent-compatible API** — `GET /tools/manifest` returns 5 tools in OpenAI function-calling format; any LLM agent can invoke them via `POST /tools/invoke`
- **Read-only enforced** — SQL validator blocks INSERT, UPDATE, DELETE, DROP, and 7 other destructive keywords
- **Schema introspection** — automatic foreign-key + inferred relationship discovery
- **Built-in UI** — dark-mode single-page app at `/ui` with Ask AI, SQL Runner, Schema viewer, Agent Tools, and History tabs
- **Query audit log** — every query appended to `logs/query_logs.jsonl`

---

## Quick Start

### 1 — Clone & configure

```bash
git clone https://github.com/YOUR_USERNAME/de-10-ai-data-middleware.git
cd de-10-ai-data-middleware

cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...
```

### 2 — Start the demo database (Docker)

```bash
docker compose up -d
```

This starts PostgreSQL 16 on port **5433** with 12 pre-loaded healthcare CSV tables.

> **No Docker?** Point the app at any existing PostgreSQL, MySQL, or SQLite database — skip this step.

### 3 — Install & run the API

```bash
cd backend
pip install -r requirements.txt

# Load .env and start with hot-reload
env $(cat ../.env | xargs) uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4 — Open the UI

```
http://localhost:8000/ui
```

| Also available | URL |
|---|---|
| Swagger API docs | `http://localhost:8000/docs` |
| ReDoc | `http://localhost:8000/redoc` |

---

## Demo database

The Docker PostgreSQL instance ships with **12 CSV-backed healthcare tables**:

| Table | Description |
|---|---|
| `patients` | Patient demographics |
| `encounters` | Clinical visits |
| `conditions` | Diagnoses |
| `medications` | Prescriptions + costs |
| `procedures` | Performed procedures |
| `observations` | Vitals and lab results |
| `providers` | Healthcare providers |
| `organizations` | Care organizations |
| `careplans` | Care plan records |
| `allergies` | Allergy records |
| `immunizations` | Vaccination history |
| `imaging_studies` | Imaging records |

**Starter questions to try:**
- `Show the first 5 patients`
- `List all medications and their total cost`
- `How many patients are there?`
- `Show provider names and organization names`
- `Top 10 most expensive encounters`

---

## API Reference

### Connections

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/connections/test` | Test a connection (inline creds or `connection_id`) |
| `POST` | `/connections/register` | Save credentials → returns `connection_id` |
| `GET` | `/connections/` | List all saved connections |
| `DELETE` | `/connections/{id}` | Delete a saved connection |

### Query

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/query/ask` | Natural language → SQL → results |
| `POST` | `/query/run` | Execute a raw read-only SELECT |

### Schema

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/schema/scan` | Return tables, columns, and relationships |

### Agent Tools

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/tools/manifest` | OpenAI function-calling tool definitions |
| `POST` | `/tools/invoke` | Execute any tool by name |

---

## Saved Connections

Register credentials once and reuse them across all endpoints:

```bash
# 1. Register
curl -s -X POST http://localhost:8000/connections/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Demo DB",
    "db_type": "postgresql",
    "host": "localhost",
    "port": 5433,
    "database": "demo_db",
    "username": "postgres",
    "password": "postgres"
  }'
# → { "connection_id": "abc-123-...", "name": "Demo DB" }

# 2. Reuse everywhere
curl -s -X POST http://localhost:8000/query/ask \
  -H "Content-Type: application/json" \
  -d '{ "connection_id": "abc-123-...", "question": "How many patients?" }'
```

---

## Agent Integration

Any AI agent can discover and call this middleware's tools automatically.

### Step 1 — Fetch the manifest

```bash
curl http://localhost:8000/tools/manifest
```

Returns 5 tools in OpenAI function-calling format:

| Tool | Description |
|---|---|
| `test_connection` | Verify database reachability |
| `register_connection` | Save credentials, get `connection_id` |
| `inspect_schema` | Return full table/column/relationship map |
| `query_database` | Natural language → SQL → rows |
| `run_sql` | Execute a raw SELECT |

### Step 2 — Give the tools to your agent

```python
import openai, requests

tools = requests.get("http://localhost:8000/tools/manifest").json()["tools"]

response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "How many patients are in the database?"}],
    tools=tools,
    tool_choice="auto",
)
```

### Step 3 — Route tool calls to `/tools/invoke`

```python
tool_call = response.choices[0].message.tool_calls[0]

result = requests.post("http://localhost:8000/tools/invoke", json={
    "tool": tool_call.function.name,
    "arguments": json.loads(tool_call.function.arguments),
}).json()
```

---

## Multi-Database Support

Pass `db_type` in any request to switch engines:

```json
{ "db_type": "postgresql", "host": "...", "port": 5432, ... }
{ "db_type": "mysql",      "host": "...", "port": 3306, ... }
{ "db_type": "sqlite",     "database": "/path/to/file.db" }
```

The SQL generator is dialect-aware — it tells the LLM which syntax to use.

---

## Project Structure

```
de-10-ai-data-middleware/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── router.py
│   │   │   └── routes/
│   │   │       ├── connections.py   # test / register / list / delete
│   │   │       ├── health.py
│   │   │       ├── query.py         # /ask and /run
│   │   │       ├── schema.py        # /scan
│   │   │       └── tools.py         # agent manifest + invoke
│   │   ├── schemas/
│   │   │   ├── ai_query.py
│   │   │   ├── connection.py
│   │   │   ├── query.py
│   │   │   └── responses.py
│   │   ├── services/
│   │   │   ├── connection_registry.py  # in-memory saved connections
│   │   │   ├── connection_service.py
│   │   │   ├── db_url.py               # multi-DB URL builder
│   │   │   ├── llm_service.py          # OpenAI SQL generation
│   │   │   ├── log_service.py
│   │   │   ├── query_service.py
│   │   │   ├── schema_service.py
│   │   │   └── sql_validator.py
│   │   ├── static/
│   │   │   └── index.html              # built-in dark-mode UI
│   │   └── main.py
│   ├── scripts/
│   │   └── load_csvs.py
│   └── requirements.txt
├── demo_db/
│   └── init.sql                        # PostgreSQL schema + CSV loader
├── logs/
│   └── query_logs.jsonl                # append-only query audit log
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Security

- **Read-only enforcement** — only `SELECT` queries are allowed; `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, `REVOKE`, `COPY` are all blocked by regex
- **Single-statement only** — multiple statements in one request are rejected
- **No credentials in logs** — passwords are never written to `query_logs.jsonl`
- **CORS** — open by default for local development; restrict `allow_origins` in `main.py` before deploying to production

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key for SQL generation |

Copy `.env.example` → `.env` and fill in your key.

---

## License

MIT — see [LICENSE](LICENSE)
