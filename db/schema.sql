-- Black Bear ops: shared database for the shadow site (Supabase / Postgres).
--
-- Paste the whole file into Supabase > SQL Editor > New query > Run.
-- Safe to run more than once: it only creates what is missing.
--
-- Access rule, everywhere: only signed-in people listed in `people` can read or
-- write anything. Nothing is open to the public key on its own. New tables are
-- not exposed automatically (project setting), so each grant below is explicit.
--
-- Jamaal stays a parallel source (Sarge, 2026-10-21): routes are seeded from it
-- once, then edited here; gallons are entered in both systems. Projections and
-- history keep coming from the Jamaal scrape, so nothing here is counted twice.

-- People allowed in. Rows are added by hand in the SQL editor (see the end of
-- this file), never through the website.
create table if not exists public.people (
  user_id uuid primary key references auth.users (id) on delete cascade,
  name    text not null,
  role    text not null default 'office' check (role in ('office', 'driver')),
  added_at timestamptz not null default now()
);

-- True when the signed-in user is listed in `people`. SECURITY DEFINER so the
-- policies below can check `people` without tripping over its own policy.
create or replace function public.is_member()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.people where user_id = (select auth.uid()));
$$;
revoke all on function public.is_member() from public, anon;
grant execute on function public.is_member() to authenticated;

-- Customers: Jamaal's ids; customers created on this site get negative ids.
-- `notes` holds the FULL notes, door codes included: this table is login-only.
create table if not exists public.customers (
  id         integer primary key,
  name       text not null,
  city       text,
  address    text,
  lat        double precision,
  lon        double precision,
  loc        text check (loc in ('site', 'census', 'town')),
  capacity   integer check (capacity >= 0),
  notes      text,
  active     boolean not null default true,
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid()
);
-- Add customer's fields (Track B, bd9827c: the source site's Add Customer,
-- less zip code). `phone` is login-only like `notes`. `shared_with` is the
-- customer whose barrel this one shares; each member lists the whole barrel's
-- capacity (CLAUDE.md, shared barrels).
alter table public.customers add column if not exists phone            text;
alter table public.customers add column if not exists periodicity_days integer check (periodicity_days >= 0);
alter table public.customers add column if not exists shared_with      integer references public.customers (id);
alter table public.customers add column if not exists created_on       date default ((now() at time zone 'America/New_York')::date);
alter table public.customers add column if not exists created_by       uuid default auth.uid();

-- Routes: Jamaal's route ids. Jamaal shows some routes under different names on
-- different pages, kept in `other_names`.
create table if not exists public.routes (
  id          integer primary key,
  name        text not null,
  other_names text[] not null default '{}',
  sort        integer not null default 0
);

-- Stops on a route, in order. A customer is on a route at most once.
create table if not exists public.route_stops (
  id                   bigint generated always as identity primary key,
  route_id             integer not null references public.routes (id) on delete cascade,
  customer_id          integer not null references public.customers (id),
  position             integer not null,
  must_get             boolean not null default false,
  jamaal_assignment_id integer,
  updated_at           timestamptz not null default now(),
  updated_by           uuid default auth.uid(),
  unique (route_id, customer_id)
);
create index if not exists route_stops_route_idx on public.route_stops (route_id, position);

-- Which truck runs a route on a given day.
create table if not exists public.route_trucks (
  route_id integer not null references public.routes (id) on delete cascade,
  day      date not null,
  truck    text not null check (truck in ('mammabear', 'mackdaddy', 'babybird', 'newbiedoo')),
  set_by   uuid default auth.uid(),
  set_at   timestamptz not null default now(),
  primary key (route_id, day)
);

-- Gallons entered on this site. 0-3 are codes (0/1 empty, 2 sign-up call,
-- 3 barrel delivery), stored as typed; totals leave them out, as everywhere.
-- `picked_on` defaults to today in Vermont, not UTC. `client_id` is set by the
-- phone so a save retried after a dropped signal is stored once.
create table if not exists public.collections (
  id          bigint generated always as identity primary key,
  customer_id integer not null references public.customers (id),
  picked_on   date not null default ((now() at time zone 'America/New_York')::date),
  gallons     integer not null check (gallons >= 0),
  route_id    integer references public.routes (id) on delete set null,
  client_id   uuid unique,
  entered_by  uuid default auth.uid(),
  entered_at  timestamptz not null default now()
);
create index if not exists collections_customer_idx on public.collections (customer_id, picked_on);
create index if not exists collections_day_idx on public.collections (picked_on);

-- Each pickup is its own record with a time, as on the source site (its Edit
-- Collection form: gallons, "2025-07-01 05:58:40", Update, Delete).
-- `entered_at` is the first save -- at the stop, so the pickup time from now
-- on. Edits stamp `updated_at`/`updated_by`; a delete removes the row, and
-- collection_history keeps every version either way.
--
-- A theft is a collection with `theft` set (Sarge, 2026-10-23): the gallons are
-- what was collected (a 1 = nothing left, x = x left and pumped), so totals
-- need no special case. `projected_gal` is what the site was projecting for
-- the stop at the first save, frozen like the live test; the theft log's
-- stolen estimate is built from it. `jamaal_collection_id` is the source
-- site's id for the same pickup, when known, for the nightly comparison.
alter table public.collections add column if not exists theft         boolean not null default false;
alter table public.collections add column if not exists theft_note    text;
alter table public.collections add column if not exists projected_gal numeric check (projected_gal >= 0);
alter table public.collections add column if not exists updated_at    timestamptz;
alter table public.collections add column if not exists updated_by    uuid;
alter table public.collections add column if not exists jamaal_collection_id integer unique;
create index if not exists collections_theft_idx on public.collections (picked_on) where theft;

-- The source site's whole history lives here too (Sarge, 2026-10-24): every
-- pickup since 2015 and every 0-3 code, copied by supabase_sync.py from the
-- nightly scrape, with `source` = 'jamaal'. Entries made on this site are
-- 'site'. The two are never added together: they record the same pickups
-- (gallons are entered in both systems), the pages and the nightly comparison
-- read only 'site' rows, and the site can neither change nor delete a 'jamaal'
-- row (policies below). `jamaal_key` is customer:date:n (n = 0, 1, ... for the
-- same customer and day, ordered by gallons): the source site has no pickup ids
-- or times, so `entered_at` is empty on those rows.
alter table public.collections add column if not exists source text not null default 'site';
alter table public.collections drop constraint if exists collections_source_check;
alter table public.collections add constraint collections_source_check check (source in ('site', 'jamaal'));
alter table public.collections add column if not exists jamaal_key text unique;
alter table public.collections alter column entered_at drop not null;
create index if not exists collections_source_day_idx on public.collections (source, picked_on);

-- Every insert, update and delete of a collection, appended by trigger. The
-- site can read it but never write it, so the first account of a theft
-- survives later edits and a deleted pickup is still on record. No foreign
-- key: the history outlives a deleted collection.
create table if not exists public.collection_history (
  id            bigint generated always as identity primary key,
  collection_id bigint not null,
  op            text not null check (op in ('insert', 'update', 'delete')),
  customer_id   integer not null,
  picked_on     date not null,
  gallons       integer not null,
  theft         boolean not null,
  theft_note    text,
  changed_by    uuid,
  changed_at    timestamptz not null default now()
);
create index if not exists collection_history_idx on public.collection_history (collection_id, changed_at);

create or replace function public.stamp_collection_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  new.updated_by := auth.uid();
  return new;
end $$;

-- SECURITY DEFINER: writes the history table, which the site cannot.
create or replace function public.log_collection()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare r public.collections;
begin
  if tg_op = 'DELETE' then r := old; else r := new; end if;
  -- Copies of the source site's history are not this site's actions.
  if r.source = 'jamaal' then return null; end if;
  insert into public.collection_history
    (collection_id, op, customer_id, picked_on, gallons, theft, theft_note, changed_by)
  values (r.id, lower(tg_op), r.customer_id, r.picked_on, r.gallons, r.theft, r.theft_note,
          auth.uid());
  return null;
end $$;
revoke all on function public.log_collection() from public, anon, authenticated;

drop trigger if exists collections_stamp on public.collections;
create trigger collections_stamp before update on public.collections
  for each row execute function public.stamp_collection_update();
drop trigger if exists collections_log on public.collections;
create trigger collections_log after insert or update or delete on public.collections
  for each row execute function public.log_collection();

-- The theft calculator's log: current thefts, first report from the history.
-- Stolen (est.) = the frozen projection, capped at capacity, less what was
-- collected (0 for a code 0-3): an upper bound. Region totals are made on the
-- page from the exported regions, which overlap and are not in this database.
create or replace view public.theft_log
with (security_invoker = true)
as
select c.id, c.customer_id, cu.name, c.picked_on as found_on, c.route_id,
       c.gallons as collected, c.theft_note, c.projected_gal, cu.capacity,
       greatest(least(c.projected_gal, coalesce(cu.capacity, c.projected_gal))
                - case when c.gallons <= 3 then 0 else c.gallons end, 0) as stolen_estimate,
       first_report.changed_at as first_reported_at,
       first_report.changed_by as first_reported_by,
       first_report.theft_note as first_note
from public.collections c
join public.customers cu on cu.id = c.customer_id
left join lateral (
  select h.changed_at, h.changed_by, h.theft_note
  from public.collection_history h
  where h.collection_id = c.id and h.theft
  order by h.changed_at, h.id
  limit 1
) first_report on true
where c.theft;

-- Oil Left (Sarge, 2026-10-24): gallons a driver saw left in a barrel and did
-- not collect -- after a part pickup when the truck is filling, or a look at a
-- stop the full truck has to skip. Never a collection: it is in no total and
-- no history, and the source site has no such field. The nightly exports it
-- (supabase_sync.py --export-oil-left, oil_left.csv: id, day, gallons) for the
-- projections, which start the barrel's level from it. One per customer and
-- day: a second save that day is an edit.
create table if not exists public.oil_left (
  id           bigint generated always as identity primary key,
  customer_id  integer not null references public.customers (id),
  checked_on   date not null default ((now() at time zone 'America/New_York')::date),
  gallons_left integer not null check (gallons_left >= 0),
  route_id     integer references public.routes (id) on delete set null,
  entered_by   uuid default auth.uid(),
  entered_at   timestamptz not null default now(),
  updated_at   timestamptz,
  updated_by   uuid,
  unique (customer_id, checked_on)
);
drop trigger if exists oil_left_stamp on public.oil_left;
create trigger oil_left_stamp before update on public.oil_left
  for each row execute function public.stamp_collection_update();

-- Photos: files in the private Storage bucket `photos`, one row each here. A
-- photo belongs to a customer (the barrel, the spot) and optionally to one
-- collection (a theft, a spill). Members may add and view, edit a caption and
-- Remove; nobody deletes through the site, since a theft photo is evidence:
-- Remove only sets `removed_at`, the pages skip those rows, and Undo clears it.
-- The phone uploads two JPEGs per photo, `<customer>/<uuid>.jpg` (1600 px) and
-- `<customer>/<uuid>_t.jpg` (400 px thumbnail; no resizing on the free plan).
create table if not exists public.photos (
  id            bigint generated always as identity primary key,
  customer_id   integer not null references public.customers (id),
  collection_id bigint references public.collections (id) on delete set null,
  storage_path  text not null unique,
  caption       text,
  taken_at      timestamptz,
  uploaded_by   uuid default auth.uid(),
  uploaded_at   timestamptz not null default now()
);
create index if not exists photos_customer_idx on public.photos (customer_id, uploaded_at);
alter table public.photos add column if not exists removed_at timestamptz;
alter table public.photos add column if not exists removed_by uuid;
-- A photo copied from the source site's uploads/ folder (jamaal_photos.py,
-- nightly): its file name there, so each is copied once and one removed on the
-- site is never copied back. Empty for photos added on the site.
alter table public.photos add column if not exists jamaal_file text unique;
alter table public.photos drop constraint if exists photos_caption_len;
alter table public.photos add constraint photos_caption_len
  check (caption is null or char_length(caption) <= 200);

insert into storage.buckets (id, name, public)
values ('photos', 'photos', false)
on conflict (id) do nothing;
-- The phone downscales before upload; the bucket refuses anything else.
update storage.buckets
   set file_size_limit = 2097152, allowed_mime_types = array['image/jpeg']
 where id = 'photos';

drop policy if exists "members read photos" on storage.objects;
create policy "members read photos" on storage.objects
  for select to authenticated using (bucket_id = 'photos' and (select public.is_member()));
drop policy if exists "members add photos" on storage.objects;
create policy "members add photos" on storage.objects
  for insert to authenticated with check (bucket_id = 'photos' and (select public.is_member()));

-- Outgoing: a stillage run by a truck, or a Schmutz volume (Shop -> Gebbie).
create table if not exists public.outgoing (
  id         bigint generated always as identity primary key,
  day        date not null default ((now() at time zone 'America/New_York')::date),
  kind       text not null check (kind in ('stillage', 'schmutz')),
  truck      text check (truck in ('mammabear', 'mackdaddy', 'babybird', 'newbiedoo')),
  gallons    integer check (gallons >= 0),
  client_id  uuid unique,
  entered_by uuid default auth.uid(),
  entered_at timestamptz not null default now(),
  check ((kind = 'stillage' and truck is not null) or (kind = 'schmutz' and gallons is not null))
);
-- The source site's outgoing log, as for collections: `source` = 'jamaal', one
-- row per stillage run or Schmutz volume, `jamaal_key` = day:kind[:truck]:n.
alter table public.outgoing add column if not exists source text not null default 'site';
alter table public.outgoing drop constraint if exists outgoing_source_check;
alter table public.outgoing add constraint outgoing_source_check check (source in ('site', 'jamaal'));
alter table public.outgoing add column if not exists jamaal_key text unique;
alter table public.outgoing alter column entered_at drop not null;

-- Row-level security: on for every table, one "members only" policy each.
alter table public.people       enable row level security;
alter table public.customers    enable row level security;
alter table public.routes       enable row level security;
alter table public.route_stops  enable row level security;
alter table public.route_trucks enable row level security;
alter table public.collections  enable row level security;
alter table public.outgoing     enable row level security;
alter table public.collection_history enable row level security;
alter table public.photos       enable row level security;
alter table public.oil_left     enable row level security;

drop policy if exists "members read history" on public.collection_history;
create policy "members read history" on public.collection_history
  for select to authenticated using ((select public.is_member()));
drop policy if exists "members read photos" on public.photos;
create policy "members read photos" on public.photos
  for select to authenticated using ((select public.is_member()));
drop policy if exists "members add photos" on public.photos;
create policy "members add photos" on public.photos
  for insert to authenticated with check ((select public.is_member()));
drop policy if exists "members change photos" on public.photos;
create policy "members change photos" on public.photos
  for update to authenticated using ((select public.is_member())) with check ((select public.is_member()));

drop policy if exists "members read people" on public.people;
create policy "members read people" on public.people
  for select to authenticated using ((select public.is_member()));

do $$
declare t text;
begin
  foreach t in array array['customers', 'routes', 'route_stops', 'route_trucks', 'collections', 'outgoing', 'oil_left'] loop
    execute format('drop policy if exists "members only" on public.%I', t);
    execute format('create policy "members only" on public.%I for all to authenticated
                    using ((select public.is_member())) with check ((select public.is_member()))', t);
  end loop;
end $$;

-- The source site's rows are read-only to the site: members may add, change
-- and delete only 'site' rows. (Restrictive policies narrow "members only".)
do $$
declare t text;
begin
  foreach t in array array['collections', 'outgoing'] loop
    execute format('drop policy if exists "site rows only: add" on public.%I', t);
    execute format('create policy "site rows only: add" on public.%I as restrictive for insert to authenticated
                    with check (source = ''site'')', t);
    execute format('drop policy if exists "site rows only: change" on public.%I', t);
    execute format('create policy "site rows only: change" on public.%I as restrictive for update to authenticated
                    using (source = ''site'') with check (source = ''site'')', t);
    execute format('drop policy if exists "site rows only: delete" on public.%I', t);
    execute format('create policy "site rows only: delete" on public.%I as restrictive for delete to authenticated
                    using (source = ''site'')', t);
  end loop;
end $$;

-- Grants: signed-in users only, and `people` is read-only through the site.
-- The public key alone (role anon) gets nothing.
revoke all on public.people, public.customers, public.routes, public.route_stops,
              public.route_trucks, public.collections, public.outgoing,
              public.collection_history, public.photos, public.theft_log, public.oil_left from anon;
grant select on public.people to authenticated;
-- History is read-only to the site (the trigger writes it). Photos: add, view,
-- and change only the caption and the Remove mark -- after the revoke, which
-- would otherwise take the column grant back.
revoke insert, update, delete on public.collection_history from authenticated;
grant select on public.collection_history, public.theft_log to authenticated;
revoke update, delete on public.photos from authenticated;
grant select, insert on public.photos to authenticated;
grant update (caption, removed_at, removed_by) on public.photos to authenticated;
grant select, insert, update, delete on public.customers, public.routes, public.route_stops,
      public.route_trucks, public.collections, public.outgoing, public.oil_left to authenticated;

-- Live updates: Jim sees Sarge's route edits without reloading. Realtime still
-- applies the policies above.
do $$
declare t text;
begin
  foreach t in array array['route_stops', 'route_trucks', 'collections', 'outgoing', 'oil_left'] loop
    if not exists (select 1 from pg_publication_tables
                   where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;

-- Route names change when someone makes or renames a route on the site.
do $$
begin
  if not exists (select 1 from pg_publication_tables
                 where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'routes') then
    alter publication supabase_realtime add table public.routes;
  end if;
end $$;

-- What the nightly sync found (supabase_sync.py): where the site and the source
-- site disagree on routes, and pickups entered in one but not the other.
-- 'routes' rows are the 7am reset (below): what the site had that it replaced.
-- Written by the sync (service key) only; members read it.
create table if not exists public.sync_reports (
  id      bigint generated always as identity primary key,
  ran_at  timestamptz not null default now(),
  kind    text not null check (kind in ('seed', 'nightly', 'routes')),
  summary text not null,
  report  jsonb not null default '{}'
);
alter table public.sync_reports drop constraint if exists sync_reports_kind_check;
alter table public.sync_reports add constraint sync_reports_kind_check
  check (kind in ('seed', 'nightly', 'routes'));
alter table public.sync_reports enable row level security;
drop policy if exists "members read sync reports" on public.sync_reports;
create policy "members read sync reports" on public.sync_reports
  for select to authenticated using ((select public.is_member()));
revoke all on public.sync_reports from anon, authenticated;
grant select on public.sync_reports to authenticated;

-- The nightly sync signs in with the secret key (role service_role). It skips
-- the row rules but still needs table rights, and new tables are not exposed
-- automatically on this project.
grant usage on schema public to service_role;
grant select, insert, update, delete on all tables in schema public to service_role;

-- 7am routes (Sarge, 2026-10-26): every morning at 7:00 Vermont time the site's
-- routes are set to the source site's Today's Routes, before the drivers leave
-- at 7:30-8:30. This starts the GitHub workflow refresh-routes.yml, which reads
-- the source site and runs supabase_sync.py --routes. GitHub's own schedules
-- ran hours late, so the database keeps the clock. pg_cron counts in UTC: it
-- fires at 11:00 and 12:00 UTC and only the one that is 7am in Vermont calls
-- GitHub, so daylight saving needs no change.
-- Needs a GitHub token in the vault, created once by hand and never in this
-- file (fine-grained, this repository only, Actions: read and write):
--   select vault.create_secret('<the token>', 'github_dispatch_token');
-- To replace it later:
--   select vault.update_secret((select id from vault.secrets where name = 'github_dispatch_token'), '<new token>');
-- To start the workflow now (a test): select public.dispatch_morning_routes();
-- then look at Actions on GitHub, and at the reply:
--   select status_code, left(content, 200) from net._http_response order by created desc limit 1;
-- (204 = started; 401 = bad or expired token.)
create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.dispatch_morning_routes()
returns bigint
language sql
security definer
set search_path = ''
as $$
  select net.http_post(
    url := 'https://api.github.com/repos/burnssargent-eng/blackbear_dashboard/actions/workflows/refresh-routes.yml/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets
                                      where name = 'github_dispatch_token'),
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'blackbear-morning-routes'),
    body := '{"ref": "main"}'::jsonb);
$$;
revoke all on function public.dispatch_morning_routes() from public, anon, authenticated;

-- Scheduling under an existing name replaces that job, so a re-run is safe.
select cron.schedule('morning-routes', '0 11,12 * * *', $cron$
  select public.dispatch_morning_routes()
  where extract(hour from now() at time zone 'America/New_York') = 7
$cron$);

-- ---------------------------------------------------------------------------
-- AFTER inviting yourself and Jim (Authentication > Users > Invite user) and
-- each of you accepting the email, run this separately with your real emails:
--
--   insert into public.people (user_id, name, role)
--   select id, 'Sarge', 'office' from auth.users where email = 'YOUR-EMAIL'
--   union all
--   select id, 'Jim', 'office' from auth.users where email = 'JIMS-EMAIL'
--   on conflict (user_id) do nothing;
--
--   select p.name, u.email from public.people p join auth.users u on u.id = p.user_id;
--   -- should list both of you
-- ---------------------------------------------------------------------------
