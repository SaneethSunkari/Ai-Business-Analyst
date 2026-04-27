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

alter table public.profiles enable row level security;
alter table public.organizations enable row level security;
alter table public.memberships enable row level security;
alter table public.data_sources enable row level security;
alter table public.schema_snapshots enable row level security;
alter table public.query_runs enable row level security;
alter table public.saved_prompts enable row level security;
alter table public.audit_events enable row level security;

drop policy if exists profiles_select_self on public.profiles;
create policy profiles_select_self
on public.profiles
for select
to authenticated
using (id = auth.uid());

drop policy if exists profiles_update_self on public.profiles;
create policy profiles_update_self
on public.profiles
for update
to authenticated
using (id = auth.uid())
with check (id = auth.uid());

drop policy if exists organizations_select_member on public.organizations;
create policy organizations_select_member
on public.organizations
for select
to authenticated
using (public.is_org_member(id));

drop policy if exists organizations_update_admin on public.organizations;
create policy organizations_update_admin
on public.organizations
for update
to authenticated
using (public.is_org_admin(id))
with check (public.is_org_admin(id));

drop policy if exists organizations_delete_admin on public.organizations;
create policy organizations_delete_admin
on public.organizations
for delete
to authenticated
using (public.is_org_admin(id));

drop policy if exists memberships_select_member on public.memberships;
create policy memberships_select_member
on public.memberships
for select
to authenticated
using (public.is_org_member(organization_id));

drop policy if exists memberships_insert_admin on public.memberships;
create policy memberships_insert_admin
on public.memberships
for insert
to authenticated
with check (public.is_org_admin(organization_id));

drop policy if exists memberships_update_admin on public.memberships;
create policy memberships_update_admin
on public.memberships
for update
to authenticated
using (public.is_org_admin(organization_id))
with check (public.is_org_admin(organization_id));

drop policy if exists memberships_delete_admin on public.memberships;
create policy memberships_delete_admin
on public.memberships
for delete
to authenticated
using (public.is_org_admin(organization_id));

drop policy if exists data_sources_select_member on public.data_sources;
create policy data_sources_select_member
on public.data_sources
for select
to authenticated
using (public.is_org_member(organization_id));

drop policy if exists data_sources_insert_member on public.data_sources;
create policy data_sources_insert_member
on public.data_sources
for insert
to authenticated
with check (public.is_org_member(organization_id) and created_by = auth.uid());

drop policy if exists data_sources_update_owner_or_admin on public.data_sources;
create policy data_sources_update_owner_or_admin
on public.data_sources
for update
to authenticated
using (created_by = auth.uid() or public.is_org_admin(organization_id))
with check (created_by = auth.uid() or public.is_org_admin(organization_id));

drop policy if exists data_sources_delete_owner_or_admin on public.data_sources;
create policy data_sources_delete_owner_or_admin
on public.data_sources
for delete
to authenticated
using (created_by = auth.uid() or public.is_org_admin(organization_id));

drop policy if exists schema_snapshots_select_member on public.schema_snapshots;
create policy schema_snapshots_select_member
on public.schema_snapshots
for select
to authenticated
using (
  exists (
    select 1
    from public.data_sources ds
    where ds.id = connection_id
      and public.is_org_member(ds.organization_id)
  )
);

drop policy if exists schema_snapshots_insert_member on public.schema_snapshots;
create policy schema_snapshots_insert_member
on public.schema_snapshots
for insert
to authenticated
with check (
  exists (
    select 1
    from public.data_sources ds
    where ds.id = connection_id
      and public.is_org_member(ds.organization_id)
  )
);

drop policy if exists query_runs_select_member on public.query_runs;
create policy query_runs_select_member
on public.query_runs
for select
to authenticated
using (public.is_org_member(organization_id));

drop policy if exists query_runs_insert_member on public.query_runs;
create policy query_runs_insert_member
on public.query_runs
for insert
to authenticated
with check (public.is_org_member(organization_id) and user_id = auth.uid());

drop policy if exists saved_prompts_select_member on public.saved_prompts;
create policy saved_prompts_select_member
on public.saved_prompts
for select
to authenticated
using (public.is_org_member(organization_id));

drop policy if exists saved_prompts_insert_member on public.saved_prompts;
create policy saved_prompts_insert_member
on public.saved_prompts
for insert
to authenticated
with check (public.is_org_member(organization_id) and created_by = auth.uid());

drop policy if exists saved_prompts_update_owner_or_admin on public.saved_prompts;
create policy saved_prompts_update_owner_or_admin
on public.saved_prompts
for update
to authenticated
using (created_by = auth.uid() or public.is_org_admin(organization_id))
with check (created_by = auth.uid() or public.is_org_admin(organization_id));

drop policy if exists saved_prompts_delete_owner_or_admin on public.saved_prompts;
create policy saved_prompts_delete_owner_or_admin
on public.saved_prompts
for delete
to authenticated
using (created_by = auth.uid() or public.is_org_admin(organization_id));

drop policy if exists audit_events_select_admin on public.audit_events;
create policy audit_events_select_admin
on public.audit_events
for select
to authenticated
using (organization_id is null or public.is_org_admin(organization_id));

drop policy if exists audit_events_insert_member on public.audit_events;
create policy audit_events_insert_member
on public.audit_events
for insert
to authenticated
with check (
  actor_user_id = auth.uid()
  and (organization_id is null or public.is_org_member(organization_id))
);

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
