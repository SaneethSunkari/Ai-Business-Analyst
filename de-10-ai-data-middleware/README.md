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
## 🚀 Demo

🎥 Watch the system in action (2 min):  
👉 https://youtu.be/hMyuesQavrM

This demo shows:
- connecting to a live database
- asking natural language questions
- SQL generation and execution
- real results returned from the database

---

## Features

- **Natural language → SQL** — ask questions in plain English; GPT-4.1-mini generates dialect-aware SQL against your live schema
- **19-source connector catalog** — databases, warehouses, object stores, SaaS apps, NoSQL, and stream adapters are exposed from one source model
- **Real product entry flow** — landing page at `/` with login/signup, protected workspace access, and authenticated profile/logout UX
- **Supabase-backed control plane** — persistent users, organizations, saved sources, schema snapshots, and query history
- **Saved connections** — register credentials once, reuse a `connection_id` across all calls
- **Agent-compatible API** — `GET /tools/manifest` returns OpenAI function-calling tools; agents can invoke them through `POST /tools/invoke`
- **Read-only enforced** — SQL validator blocks destructive SQL and multi-statement execution
- **Schema introspection** — automatic foreign-key + inferred relationship discovery
- **File-backed virtual tables** — S3 and Azure Blob files can be scanned and queried through DuckDB-backed temporary views
- **Built-in UI** — homepage at `/` plus protected workspace at `/ui` with Ask AI, SQL Runner, Schema viewer, Agent Tools, Ops, and History tabs
- **Query audit log** — local JSONL logging plus Supabase query history persistence
- **Ops visibility** — `/ops/status` exposes runtime, cost-control, onboarding, and recent activity status for the current workspace

---

## Product Blueprint

This repo now contains both the working prototype and the architecture for the production-grade version:

- [End Product Blueprint](docs/END_PRODUCT_BLUEPRINT.md) — multi-user SaaS design, tenant model, security controls, S3/Azure Blob expansion path, deployment shape, and roadmap
- [Universal Connector Strategy](docs/UNIVERSAL_CONNECTOR_STRATEGY.md) — how this becomes one product surface across databases, warehouse platforms, object stores, SaaS apps, NoSQL, and files

---

## Quick Start

### 1 — Clone & configure

```bash
git clone https://github.com/YOUR_USERNAME/de-10-ai-data-middleware.git
cd de-10-ai-data-middleware

cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...
```

TokenFirewall routing for AI query generation is built into this repo and no longer depends on a machine-specific external path.

For local Docker usage:

```bash
docker compose up --build
```

Compose starts three services: Postgres, the FastAPI backend, and TokenFirewall. The backend still uses the OpenAI SDK, but `OPENAI_BASE_URL` points to `http://tokenfirewall:8787/v1`, so natural-language SQL generation goes through TokenFirewall first for cache, budget, routing, and usage metrics. Website users do not run TokenFirewall manually.

The demo database seed path is also portable now. Put the 12 CSV files under `./demo_db/csv`, or override:

```bash
CSV_SEED_DIR=/absolute/path/to/csv docker compose up --build
```

For single-service deployments such as Railway, set:

```bash
ENABLE_TOKENFIREWALL=1
```

When no external `OPENAI_BASE_URL` or `TOKENFIREWALL_BASE_URL` is configured, the backend starts an embedded local TokenFirewall gateway automatically and points OpenAI-compatible calls through `http://127.0.0.1:8787/v1`. That keeps the cost-control path active even when the website is deployed as a single app service.

For non-Docker local development, you can still run TokenFirewall separately:

```bash
cd backend/vendor/tokenfirewall
python -m tokenfirewall server --host 127.0.0.1 --port 8787

# In this project's .env for host-based uvicorn:
TOKENFIREWALL_BASE_URL=http://127.0.0.1:8787
# or OPENAI_BASE_URL=http://127.0.0.1:8787/v1
```

### 2 — Start the demo database (Docker)

```bash
docker compose up -d
```

This starts PostgreSQL 16 on port **5433** with 12 pre-loaded healthcare CSV tables.

> **No Docker?** Point the app at any existing PostgreSQL, MySQL, or SQLite database — skip this step.

### 3 — Install & run the API

```bash
python3 -m venv .venv
source .venv/bin/activate

cd backend
pip install -r requirements.txt

# Load .env and start with hot-reload
env $(cat ../.env | xargs) uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4 — Open the app

```
http://localhost:8000/
```

| Also available | URL |
|---|---|
| Homepage | `http://localhost:8000/` |
| Workspace | `http://localhost:8000/ui` |
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

### Auth

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/signup` | Create a user account and start a session |
| `POST` | `/auth/login` | Sign in and set session cookies |
| `GET` | `/auth/me` | Return the current signed-in user |
| `POST` | `/auth/logout` | End the current session |

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

## Source Support

Pass `source_kind` and `engine_key` in any request to switch engines:

```json
{ "source_kind": "database",     "engine_key": "postgresql", "host": "...", "port": 5432, ... }
{ "source_kind": "database",     "engine_key": "mysql",      "host": "...", "port": 3306, ... }
{ "source_kind": "database",     "engine_key": "sqlserver",  "host": "...", "port": 1433, ... }
{ "source_kind": "database",     "engine_key": "sqlite",     "database": "/path/to/file.db" }
{ "source_kind": "database",     "engine_key": "oracle",     "host": "...", "port": 1521, ... }
{ "source_kind": "warehouse",    "engine_key": "snowflake",  "host": "myorg-account123", "database": "...", "options": { "warehouse": "COMPUTE_WH" } }
{ "source_kind": "warehouse",    "engine_key": "bigquery",   "database": "dataset_name", "options": { "project": "my-project" } }
{ "source_kind": "warehouse",    "engine_key": "redshift",   "host": "...", "port": 5439, ... }
{ "source_kind": "object_store", "engine_key": "s3",         "host": "my-bucket", "database": "folder/path/", "username": "...", "password": "...", "options": { "region": "us-east-1" } }
{ "source_kind": "object_store", "engine_key": "azure_blob", "host": "https://myaccount.blob.core.windows.net", "database": "my-container", "options": { "prefix": "folder/path/", "sas_token": "..." } }
```

For backwards compatibility, the older `db_type` field still works for SQL engines.

Current connector catalog:

- **Databases** — PostgreSQL, MySQL, SQL Server, SQLite, Oracle
- **Warehouses** — Snowflake, BigQuery, Redshift, Databricks SQL, Athena, Synapse, Fabric, Trino, Dremio
- **Object stores** — Amazon S3, Azure Blob
- **SaaS / NoSQL / streams** — Salesforce, MongoDB, Kafka

Important:

- PostgreSQL is the most complete local demo path in this repo.
- Warehouses and cloud connectors require real credentials and reachable external accounts.
- Some non-SQL adapters are preview-style connector paths and should be validated against your target platform before production use.

---

## Project Structure

```
de-10-ai-data-middleware/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── auth.py          # signup / login / logout / me
│   │   │       ├── connections.py   # test / register / list / delete
│   │   │       ├── health.py
│   │   │       ├── query.py         # /ask and /run
│   │   │       ├── schema.py        # /scan
│   │   │       └── tools.py         # agent manifest + invoke
│   │   ├── schemas/
│   │   │   ├── auth.py
│   │   │   ├── ai_query.py
│   │   │   ├── connection.py
│   │   │   ├── query.py
│   │   │   └── responses.py
│   │   ├── services/
│   │   │   ├── auth_service.py
│   │   │   ├── connection_registry.py  # Supabase-backed saved sources
│   │   │   ├── connection_service.py
│   │   │   ├── control_plane_service.py
│   │   │   ├── database_catalog.py
│   │   │   ├── db_url.py               # multi-DB URL builder
│   │   │   ├── error_service.py
│   │   │   ├── extended_source_service.py
│   │   │   ├── llm_service.py          # OpenAI SQL generation
│   │   │   ├── log_service.py
│   │   │   ├── object_store_service.py # S3 / Azure Blob virtual-table execution
│   │   │   ├── query_service.py
│   │   │   ├── schema_service.py
│   │   │   └── sql_validator.py
│   │   ├── static/
│   │   │   ├── auth.js
│   │   │   ├── home.html               # landing page + login/signup
│   │   │   └── index.html              # protected workspace UI
│   │   └── main.py
│   ├── scripts/
│   │   ├── apply_supabase_schema.py
│   │   ├── bootstrap_supabase_control_plane.py
│   │   └── load_csvs.py
│   └── requirements.txt
├── demo_db/
│   └── init.sql                        # PostgreSQL schema + CSV loader
├── logs/
│   └── query_logs.jsonl                # append-only query audit log
├── docs/
│   ├── END_PRODUCT_BLUEPRINT.md
│   ├── SUPABASE_SETUP.md
│   └── UNIVERSAL_CONNECTOR_STRATEGY.md
├── supabase/
│   └── migrations/                     # control-plane schema SQL
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Security

- **Read-only enforcement** — only read-safe SQL flows are allowed; destructive SQL is blocked before execution
- **Single-statement only** — multiple statements in one request are rejected
- **Protected workspace** — unauthenticated `/ui` requests redirect to `/`
- **Control-plane persistence** — saved source metadata and query history are stored in Supabase, not only in local memory
- **No credentials in logs** — passwords are never written to `query_logs.jsonl`
- **CORS** — open by default for local development; restrict `allow_origins` in `main.py` before deploying to production

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key for SQL generation |
| `OPENAI_MODEL` | Optional | OpenAI-compatible model for SQL generation, defaults to `gpt-4.1-mini` |
| `ENABLE_TOKENFIREWALL` | Optional | `1` enables cost-control routing; embedded mode is used when no external gateway URL is set |
| `TOKENFIREWALL_PORT` | Optional | Port used by the embedded TokenFirewall gateway, defaults to `8787` |
| `TOKENFIREWALL_BASE_URL` | Optional | Local TokenFirewall gateway URL, for example `http://127.0.0.1:8787` |
| `OPENAI_BASE_URL` | Optional | Direct OpenAI-compatible base URL, for example `http://127.0.0.1:8787/v1` |
| `CSV_SEED_DIR` | Optional | Directory containing the 12 demo CSV files for Docker Postgres initialization |
| `SUPABASE_URL` | For auth/control plane | Supabase project URL |
| `SUPABASE_ANON_KEY` | For browser auth | Public browser-safe Supabase key |
| `SUPABASE_SERVICE_ROLE_KEY` | For backend control plane | Server-side Supabase admin key |
| `CONTROL_PLANE_ENCRYPTION_KEY` | For persistent saved secrets | Encrypts stored source secrets |
| `CONTROL_PLANE_ORGANIZATION_ID` | Optional runtime default | Default workspace/org to bind saved sources to |
| `CONTROL_PLANE_ACTOR_USER_ID` | Optional runtime default | Default actor user for backend-side writes |

Copy `.env.example` → `.env` and fill in the values you need.

For the full Supabase setup flow, see [docs/SUPABASE_SETUP.md](docs/SUPABASE_SETUP.md).

---

## License

MIT — see [LICENSE](LICENSE)
