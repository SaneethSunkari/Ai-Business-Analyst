# AI Data Middleware

> Agent-compatible middleware that lets AI systems query live databases through schema introspection, read-only SQL validation, saved connections, and audit logs.

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![OpenAI](https://img.shields.io/badge/OpenAI-tools-412991?style=flat-square&logo=openai&logoColor=white)](https://platform.openai.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ed?style=flat-square&logo=docker&logoColor=white)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](de-10-ai-data-middleware/LICENSE)

Website: [ai-business-analyst-production-8fa6.up.railway.app](https://ai-business-analyst-production-8fa6.up.railway.app/)

Demo: [2-minute system walkthrough](https://youtu.be/hMyuesQavrM)

The source application lives in [`de-10-ai-data-middleware/`](de-10-ai-data-middleware/). This root README is written as the portfolio entrypoint.

## What It Does

AI agents are most useful when they can work with real data, but direct database access is risky. This middleware creates a safer control layer:

```text
Natural-language question
        -> live schema introspection
        -> dialect-aware SQL generation
        -> read-only SQL validation
        -> query execution
        -> logged, tabular result
```

Any agent that understands OpenAI-style function tools can fetch `GET /tools/manifest` and invoke database actions through `POST /tools/invoke`.

## Why It Matters

Most text-to-SQL demos stop after generating a query. This project keeps the operational parts visible:

- saved database connections instead of one-off credentials
- schema introspection before generation
- read-only validation before execution
- query audit logs
- a browser UI for human review
- a tools manifest for agent integration
- a roadmap toward tenants, source catalogs, object storage, and cost controls

## Project Evidence

- [Changelog](CHANGELOG.md) - iteration history from core prototype to control-plane direction
- [Roadmap](ROADMAP.md) - production-readiness gaps and next steps
- [Validation and boundaries](docs/VALIDATION.md) - what is proven and what still needs hardening
- [End Product Blueprint](de-10-ai-data-middleware/docs/END_PRODUCT_BLUEPRINT.md) - SaaS product direction
- [Universal Connector Strategy](de-10-ai-data-middleware/docs/UNIVERSAL_CONNECTOR_STRATEGY.md) - expansion beyond relational databases

## Features

- Natural-language SQL over live schemas
- PostgreSQL, MySQL, and SQLite support in the core demo
- Saved connections with reusable `connection_id` values
- OpenAI-compatible tools manifest at `/tools/manifest`
- Tool execution endpoint at `/tools/invoke`
- SQL validator that blocks destructive and multi-statement patterns
- Schema viewer, SQL runner, Ask AI flow, tools tab, ops status, and history surfaces
- Local query logging plus Supabase-backed control-plane direction
- TokenFirewall integration path for routing, budget, cache, and usage controls

## Quick Start

```bash
git clone https://github.com/SaneethSunkari/Ai-Business-Analyst.git
cd Ai-Business-Analyst/de-10-ai-data-middleware

cp .env.example .env
# set OPENAI_API_KEY in .env
```

Start the demo stack:

```bash
docker compose up --build
```

Open:

| Surface | URL |
| --- | --- |
| Homepage | `http://localhost:8000/` |
| Workspace | `http://localhost:8000/ui` |
| Swagger docs | `http://localhost:8000/docs` |
| ReDoc | `http://localhost:8000/redoc` |

For non-Docker development:

```bash
cd backend
pip install -r requirements.txt
env $(cat ../.env | xargs) uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Demo Database

The local Docker stack includes a PostgreSQL demo database with healthcare-style tables:

- `patients`
- `encounters`
- `conditions`
- `medications`
- `procedures`
- `observations`
- `providers`
- `organizations`
- `careplans`
- `allergies`
- `immunizations`
- `imaging_studies`

Starter questions:

- `How many patients are in the database?`
- `Show provider names and organization names`
- `List all medications and their total cost`
- `Top 10 most expensive encounters`

## API Overview

| Area | Endpoints |
| --- | --- |
| Auth | `POST /auth/signup`, `POST /auth/login`, `GET /auth/me`, `POST /auth/logout` |
| Connections | `POST /connections/test`, `POST /connections/register`, `GET /connections/`, `DELETE /connections/{id}` |
| Query | `POST /query/ask`, `POST /query/run` |
| Schema | `POST /schema/scan` |
| Agent tools | `GET /tools/manifest`, `POST /tools/invoke` |
| Ops | `GET /ops/status` |

## Repository Tour

```text
Ai-Business-Analyst/
|-- README.md
|-- CHANGELOG.md
|-- ROADMAP.md
|-- docs/
|   `-- VALIDATION.md
`-- de-10-ai-data-middleware/
    |-- backend/app/
    |-- backend/tokenfirewall/
    |-- demo_db/
    |-- docs/
    |-- supabase/migrations/
    |-- Dockerfile
    |-- docker-compose.yml
    `-- README.md
```

## Security Posture

This is a portfolio-grade product demo with a serious safety model, not a production security certification. The strongest current controls are read-only SQL validation, connection scoping, explicit tool invocation, and query logging. The roadmap calls out the remaining production work: parser-backed SQL validation per dialect, stronger auth, tenant isolation, secrets handling, policy tests, monitoring, and incident-response docs.

## License

MIT. See [`de-10-ai-data-middleware/LICENSE`](de-10-ai-data-middleware/LICENSE).
