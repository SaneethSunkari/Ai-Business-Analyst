# Supabase Setup

This project can use Supabase as the control-plane backend for:

- authentication
- organizations and memberships
- saved data-source metadata
- schema snapshots
- query history
- prompt library
- audit events

It does **not** store customer source data in Supabase. It stores app metadata there.

## What Credentials Are Needed

To create the control-plane tables, use one of these admin paths:

1. `SUPABASE_DB_URL`
2. `SUPABASE_PROJECT_REF` + `SUPABASE_MANAGEMENT_PAT`

Important:

- `SUPABASE_SERVICE_ROLE_KEY` is useful for backend app logic.
- `SUPABASE_SERVICE_ROLE_KEY` is **not enough** to run DDL and create tables.
- `SUPABASE_MANAGEMENT_PAT` is a Supabase personal access token, not the anon key and not the service role key.

## Files

- Migration: [20260426_000001_control_plane.sql](/Users/saneethsunkari/Desktop/plan/project1/de-10-ai-data-middleware/supabase/migrations/20260426_000001_control_plane.sql)
- Apply script: [apply_supabase_schema.py](/Users/saneethsunkari/Desktop/plan/project1/de-10-ai-data-middleware/backend/scripts/apply_supabase_schema.py)
- Runtime bootstrap: [bootstrap_supabase_control_plane.py](/Users/saneethsunkari/Desktop/plan/project1/de-10-ai-data-middleware/backend/scripts/bootstrap_supabase_control_plane.py)

## Apply Via Direct Postgres URL

```bash
SUPABASE_DB_URL='postgresql://postgres:YOUR_PASSWORD@db.<project-ref>.supabase.co:5432/postgres' \
python3 backend/scripts/apply_supabase_schema.py
```

## Apply Via Supabase Management API

```bash
SUPABASE_PROJECT_REF='<project-ref>' \
SUPABASE_MANAGEMENT_PAT='sbp_...' \
python3 backend/scripts/apply_supabase_schema.py
```

## What Gets Created

Public tables:

- `profiles`
- `organizations`
- `memberships`
- `data_sources`
- `schema_snapshots`
- `query_runs`
- `saved_prompts`
- `audit_events`

Private table:

- `app_private.data_source_secret_refs`

Helpers:

- `handle_new_user()`
- `is_org_member(uuid)`
- `is_org_admin(uuid)`
- `create_organization_with_owner(text, text)`
- `set_updated_at()`

Security:

- Row Level Security enabled on all public control-plane tables
- organization-scoped access policies
- self-profile policies
- admin/owner mutation policies

## Supabase Auth Model

This schema assumes:

- user identities live in `auth.users`
- `public.profiles.id = auth.users.id`
- new users sync into `public.profiles` automatically through the trigger

## Current Limitation

If you only have:

- project URL
- anon key
- service role key

then the schema can be prepared in the repo, but it cannot be applied automatically yet. For automatic application, you still need either:

- the real Postgres password in `SUPABASE_DB_URL`, or
- a `SUPABASE_MANAGEMENT_PAT`

## Runtime Integration

After the schema exists, the FastAPI app can use Supabase as its persistent control plane.

Required backend env vars:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `CONTROL_PLANE_ENCRYPTION_KEY`

Recommended runtime context:

- `CONTROL_PLANE_ORGANIZATION_ID`
- `CONTROL_PLANE_ACTOR_USER_ID`

If you do not already have an owner user and workspace, bootstrap them with:

```bash
python3 backend/scripts/bootstrap_supabase_control_plane.py \
  --email control-plane-owner@ai-data-middleware.local \
  --password 'Middleware@2026!' \
  --full-name 'AI Data Middleware Owner' \
  --org-name 'AI Data Middleware Workspace' \
  --org-slug ai-data-middleware
```

That script prints the exact `CONTROL_PLANE_*` IDs to place in `backend/.env`.

## What Is Persistent Now

With the runtime env configured:

- `POST /connections/register` saves into Supabase `data_sources`
- `GET /connections/` reads saved sources from Supabase
- `DELETE /connections/{id}` removes saved sources from Supabase
- `POST /query/ask` mirrors query history into Supabase `query_runs`

The app still writes the local JSONL log file too.
