create extension if not exists pgcrypto;

create schema if not exists app_private;

revoke all on schema app_private from public;
revoke all on schema app_private from anon;
revoke all on schema app_private from authenticated;

create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  plan text not null default 'free' check (plan in ('free', 'pro', 'team', 'enterprise')),
  created_by uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  full_name text,
  avatar_url text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.memberships (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'member' check (role in ('owner', 'admin', 'member', 'viewer')),
  status text not null default 'active' check (status in ('active', 'invited', 'disabled')),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (organization_id, user_id)
);

create table if not exists public.data_sources (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  source_kind text not null,
  engine_key text not null,
  secret_backend text not null default 'supabase_vault',
  secret_locator text,
  host text,
  port integer,
  database_name text,
  storage_account text,
  bucket_or_container text,
  path_prefix text,
  auth_mode text,
  options_json jsonb not null default '{}'::jsonb,
  status text not null default 'draft' check (status in ('draft', 'active', 'error', 'archived')),
  last_tested_at timestamptz,
  created_by uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (organization_id, name)
);

create table if not exists app_private.data_source_secret_refs (
  id uuid primary key default gen_random_uuid(),
  data_source_id uuid not null unique references public.data_sources(id) on delete cascade,
  secret_backend text not null default 'supabase_vault',
  secret_locator text not null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.schema_snapshots (
  id uuid primary key default gen_random_uuid(),
  connection_id uuid not null references public.data_sources(id) on delete cascade,
  tables_json jsonb not null default '{}'::jsonb,
  relationships_json jsonb not null default '[]'::jsonb,
  hash text not null,
  created_at timestamptz not null default timezone('utc', now()),
  unique (connection_id, hash)
);

create table if not exists public.query_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  connection_id uuid references public.data_sources(id) on delete set null,
  user_id uuid not null references auth.users(id) on delete cascade,
  question text,
  generated_sql text,
  success boolean not null default false,
  row_count integer,
  latency_ms integer,
  error text,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.saved_prompts (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  prompt_text text not null,
  created_by uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (organization_id, name)
);

create table if not exists public.audit_events (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references public.organizations(id) on delete cascade,
  actor_user_id uuid references auth.users(id) on delete set null,
  event_type text not null,
  target_type text,
  target_id uuid,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_profiles_email on public.profiles(email);
create index if not exists idx_memberships_user_id on public.memberships(user_id);
create index if not exists idx_memberships_org_id on public.memberships(organization_id);
create index if not exists idx_data_sources_org_id on public.data_sources(organization_id);
create index if not exists idx_data_sources_engine_key on public.data_sources(engine_key);
create index if not exists idx_schema_snapshots_connection_id on public.schema_snapshots(connection_id);
create index if not exists idx_query_runs_org_id_created_at on public.query_runs(organization_id, created_at desc);
create index if not exists idx_query_runs_connection_id on public.query_runs(connection_id);
create index if not exists idx_saved_prompts_org_id on public.saved_prompts(organization_id);
create index if not exists idx_audit_events_org_id_created_at on public.audit_events(organization_id, created_at desc);
