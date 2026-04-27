create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $function$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$function$;

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
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
$function$;

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
as $function$
  select exists (
    select 1
    from public.memberships m
    where m.organization_id = check_org
      and m.user_id = auth.uid()
      and m.status = 'active'
  );
$function$;

create or replace function public.is_org_admin(check_org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $function$
  select exists (
    select 1
    from public.memberships m
    where m.organization_id = check_org
      and m.user_id = auth.uid()
      and m.status = 'active'
      and m.role in ('owner', 'admin')
  );
$function$;

create or replace function public.create_organization_with_owner(p_name text, p_slug text default null)
returns public.organizations
language plpgsql
security definer
set search_path = public
as $function$
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
$function$;

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
