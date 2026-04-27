create extension if not exists pgcrypto;

create schema if not exists app_private;

revoke all on schema app_private from public;
revoke all on schema app_private from anon;
revoke all on schema app_private from authenticated;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

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

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, full_name, avatar_url, metadata)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name'),
    new.raw_user_meta_data ->> 'avatar_url',
    coalesce(new.raw_user_meta_data, '{}'::jsonb)
  )
  on conflict (id) do update
  set
    email = excluded.email,
    full_name = coalesce(excluded.full_name, public.profiles.full_name),
    avatar_url = coalesce(excluded.avatar_url, public.profiles.avatar_url),
    metadata = coalesce(excluded.metadata, public.profiles.metadata),
    updated_at = timezone('utc', now());

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert or update on auth.users
for each row execute function public.handle_new_user();

create or replace function public.is_org_member(check_org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.memberships m
    where m.organization_id = check_org
      and m.user_id = auth.uid()
      and m.status = 'active'
  );
$$;

create or replace function public.is_org_admin(check_org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.memberships m
    where m.organization_id = check_org
      and m.user_id = auth.uid()
      and m.status = 'active'
      and m.role in ('owner', 'admin')
  );
$$;

create or replace function public.create_organization_with_owner(p_name text, p_slug text default null)
returns public.organizations
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid := auth.uid();
  v_slug text;
  v_org public.organizations;
begin
  if v_user_id is null then
    raise exception 'Authentication required';
  end if;

  v_slug := coalesce(
    nullif(trim(p_slug), ''),
    regexp_replace(lower(trim(p_name)), '[^a-z0-9]+', '-', 'g')
  );
  v_slug := trim(both '-' from v_slug);

  if v_slug = '' then
    raise exception 'Organization slug could not be derived';
  end if;

  insert into public.organizations (name, slug, created_by)
  values (trim(p_name), v_slug, v_user_id)
  returning * into v_org;

  insert into public.memberships (organization_id, user_id, role, status)
  values (v_org.id, v_user_id, 'owner', 'active')
  on conflict (organization_id, user_id) do update
  set role = excluded.role, status = excluded.status, updated_at = timezone('utc', now());

  return v_org;
end;
$$;

grant usage on schema public to authenticated;
grant usage on schema app_private to postgres, service_role;

grant select, insert, update, delete on table public.profiles to authenticated;
grant select, insert, update, delete on table public.organizations to authenticated;
grant select, insert, update, delete on table public.memberships to authenticated;
grant select, insert, update, delete on table public.data_sources to authenticated;
grant select, insert, update, delete on table public.schema_snapshots to authenticated;
grant select, insert, update, delete on table public.query_runs to authenticated;
grant select, insert, update, delete on table public.saved_prompts to authenticated;
grant select, insert on table public.audit_events to authenticated;

grant execute on function public.is_org_member(uuid) to authenticated;
grant execute on function public.is_org_admin(uuid) to authenticated;
grant execute on function public.create_organization_with_owner(text, text) to authenticated;

drop trigger if exists set_updated_at_profiles on public.profiles;
create trigger set_updated_at_profiles
before update on public.profiles
for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at_organizations on public.organizations;
create trigger set_updated_at_organizations
before update on public.organizations
for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at_memberships on public.memberships;
create trigger set_updated_at_memberships
before update on public.memberships
for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at_data_sources on public.data_sources;
create trigger set_updated_at_data_sources
before update on public.data_sources
for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at_secret_refs on app_private.data_source_secret_refs;
create trigger set_updated_at_secret_refs
before update on app_private.data_source_secret_refs
for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at_saved_prompts on public.saved_prompts;
create trigger set_updated_at_saved_prompts
before update on public.saved_prompts
for each row execute function public.set_updated_at();
