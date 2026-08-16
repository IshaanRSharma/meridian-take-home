-- Roles Supabase provisions and a plain Postgres container does not.
--
-- 0001_initial.sql grants select on `events` to `anon`, because the browser
-- subscribes to Realtime with the anon key. A local container has no such role,
-- so the migration fails there while passing on Supabase — the exact drift that
-- testing against local Postgres is supposed to catch.
--
-- Creating them here keeps ONE set of migrations valid in both places. Applied
-- by `make db` before migrating, and safe to re-run.

do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if not exists (select from pg_roles where rolname = r) then
      execute format('create role %I nologin noinherit', r);
    end if;
    execute format('grant usage on schema public to %I', r);
  end loop;
end $$;
