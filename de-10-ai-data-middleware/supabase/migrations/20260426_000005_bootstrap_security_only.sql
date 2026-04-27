alter table public.profiles enable row level security;
alter table public.organizations enable row level security;
alter table public.memberships enable row level security;
alter table public.data_sources enable row level security;
alter table public.schema_snapshots enable row level security;
alter table public.query_runs enable row level security;
alter table public.saved_prompts enable row level security;
alter table public.audit_events enable row level security;

drop policy if exists profiles_select_self on public.profiles;
create policy profiles_select_self on public.profiles
for select to authenticated
using (id = auth.uid());

drop policy if exists profiles_update_self on public.profiles;
create policy profiles_update_self on public.profiles
for update to authenticated
using (id = auth.uid())
with check (id = auth.uid());

drop policy if exists organizations_select_member on public.organizations;
create policy organizations_select_member on public.organizations
for select to authenticated
using (public.is_org_member(id));

drop policy if exists organizations_update_admin on public.organizations;
create policy organizations_update_admin on public.organizations
for update to authenticated
using (public.is_org_admin(id))
with check (public.is_org_admin(id));

drop policy if exists organizations_delete_admin on public.organizations;
create policy organizations_delete_admin on public.organizations
for delete to authenticated
using (public.is_org_admin(id));

drop policy if exists memberships_select_member on public.memberships;
create policy memberships_select_member on public.memberships
for select to authenticated
using (public.is_org_member(organization_id));

drop policy if exists memberships_insert_admin on public.memberships;
create policy memberships_insert_admin on public.memberships
for insert to authenticated
with check (public.is_org_admin(organization_id));

drop policy if exists memberships_update_admin on public.memberships;
create policy memberships_update_admin on public.memberships
for update to authenticated
using (public.is_org_admin(organization_id))
with check (public.is_org_admin(organization_id));

drop policy if exists memberships_delete_admin on public.memberships;
create policy memberships_delete_admin on public.memberships
for delete to authenticated
using (public.is_org_admin(organization_id));

drop policy if exists data_sources_select_member on public.data_sources;
create policy data_sources_select_member on public.data_sources
for select to authenticated
using (public.is_org_member(organization_id));

drop policy if exists data_sources_insert_member on public.data_sources;
create policy data_sources_insert_member on public.data_sources
for insert to authenticated
with check (public.is_org_member(organization_id) and created_by = auth.uid());

drop policy if exists data_sources_update_owner_or_admin on public.data_sources;
create policy data_sources_update_owner_or_admin on public.data_sources
for update to authenticated
using (created_by = auth.uid() or public.is_org_admin(organization_id))
with check (created_by = auth.uid() or public.is_org_admin(organization_id));

drop policy if exists data_sources_delete_owner_or_admin on public.data_sources;
create policy data_sources_delete_owner_or_admin on public.data_sources
for delete to authenticated
using (created_by = auth.uid() or public.is_org_admin(organization_id));

drop policy if exists schema_snapshots_select_member on public.schema_snapshots;
create policy schema_snapshots_select_member on public.schema_snapshots
for select to authenticated
using (
  exists (
    select 1
    from public.data_sources ds
    where ds.id = connection_id
      and public.is_org_member(ds.organization_id)
  )
);

drop policy if exists schema_snapshots_insert_member on public.schema_snapshots;
create policy schema_snapshots_insert_member on public.schema_snapshots
for insert to authenticated
with check (
  exists (
    select 1
    from public.data_sources ds
    where ds.id = connection_id
      and public.is_org_member(ds.organization_id)
  )
);

drop policy if exists query_runs_select_member on public.query_runs;
create policy query_runs_select_member on public.query_runs
for select to authenticated
using (public.is_org_member(organization_id));

drop policy if exists query_runs_insert_member on public.query_runs;
create policy query_runs_insert_member on public.query_runs
for insert to authenticated
with check (public.is_org_member(organization_id) and user_id = auth.uid());

drop policy if exists saved_prompts_select_member on public.saved_prompts;
create policy saved_prompts_select_member on public.saved_prompts
for select to authenticated
using (public.is_org_member(organization_id));

drop policy if exists saved_prompts_insert_member on public.saved_prompts;
create policy saved_prompts_insert_member on public.saved_prompts
for insert to authenticated
with check (public.is_org_member(organization_id) and created_by = auth.uid());

drop policy if exists saved_prompts_update_owner_or_admin on public.saved_prompts;
create policy saved_prompts_update_owner_or_admin on public.saved_prompts
for update to authenticated
using (created_by = auth.uid() or public.is_org_admin(organization_id))
with check (created_by = auth.uid() or public.is_org_admin(organization_id));

drop policy if exists saved_prompts_delete_owner_or_admin on public.saved_prompts;
create policy saved_prompts_delete_owner_or_admin on public.saved_prompts
for delete to authenticated
using (created_by = auth.uid() or public.is_org_admin(organization_id));

drop policy if exists audit_events_select_admin on public.audit_events;
create policy audit_events_select_admin on public.audit_events
for select to authenticated
using (organization_id is null or public.is_org_admin(organization_id));

drop policy if exists audit_events_insert_member on public.audit_events;
create policy audit_events_insert_member on public.audit_events
for insert to authenticated
with check (
  actor_user_id = auth.uid()
  and (organization_id is null or public.is_org_member(organization_id))
);
