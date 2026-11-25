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
-- Collected with (Sarge, 2026-11-02): a customer in a one-off town that is
-- picked up on another town's run (Tourterelle, New Haven, on the Middlebury
-- run). The other town's name as Assign shows it; Assign lists the customer
-- under that town, and My Route and Assign show "New Haven · w/ Middlebury".
-- Set on the customer page; empty for everyone else. The nightly never writes it.
alter table public.customers add column if not exists collected_with_town text;
-- Taking over from (Sarge, 2026-11-02): a place under a new owner gets a new
-- account (Add customer's "Taking over from"), and the old one is marked
-- replaced by it. A replaced customer is inactive for good: the nightly never
-- sets it active again, the 7am route reset puts the new account in its place,
-- and the Ops pages hide it. Its pickups stay its own history. Undo straight
-- after Add deletes the new account, which clears this (on delete set null).
alter table public.customers add column if not exists replaced_by integer
  references public.customers (id) on delete set null;
-- Where `phone` came from and the day it was last read there: Jamaal's Phone
-- field (or its notes), typed on this site, a web lookup (Claude with web
-- search, reviewed by Sarge before loading), or Jim's contacts. Loaded numbers
-- are stored +1XXXXXXXXXX; the page shows (802) 555-0100. Other numbers for a
-- customer go in customer_contacts, never a second copy of this one.
alter table public.customers add column if not exists phone_source     text;
alter table public.customers add column if not exists phone_checked    date;
alter table public.customers drop constraint if exists customers_phone_source_check;
alter table public.customers add constraint customers_phone_source_check
  check (phone_source in ('jamaal', 'site', 'claude_web', 'vcard'));
-- What the source site said at the last weekly sweep (supabase_sync.py
-- --sweep): an address, notes or phone edit there is followed only while ours still
-- matches its old value, so edits made here are never undone. Login-only like
-- `notes` (codes included).
alter table public.customers add column if not exists jamaal_address   text;
alter table public.customers add column if not exists jamaal_notes     text;
alter table public.customers add column if not exists jamaal_phone     text;
-- Edited on the customer page (Sarge, 2026-11-02): a name, town or capacity set
-- here beats the source site's. The nightly keeps writing the source site's
-- values into name, city and capacity; these stand beside them, empty = follow
-- the source site. Saving the source site's own value empties them again.
-- site_town is an official Vermont town ("Barre Town") or "Place, ST". The
-- nightly exports them to customer_edits.csv (ids, names, towns, capacities:
-- nothing private), which the scraper and the projection builder read, so they
-- reach the dashboard's regions, map and projections. Customers added on the
-- site (negative ids) are edited in name, city and capacity directly.
-- An address edited here empties `loc`; the nightly (--place-pins) then moves
-- the pin: the Census geocoder, else the town centre.
alter table public.customers add column if not exists site_name     text;
alter table public.customers add column if not exists site_town     text;
alter table public.customers add column if not exists site_capacity integer check (site_capacity >= 0);
-- Region set on the customer page: a geographic region by name in place of the
-- one the town gives (Jim reassigning a spot off town lines), and the theme
-- regions ("Ski Slopes", "Summer Snack Stops") as an explicit list. Empty /
-- null = the config in oil_scraper.py. Exported in customer_edits.csv.
alter table public.customers add column if not exists site_region   text;
alter table public.customers add column if not exists site_themes   text[];
-- Status set on the customer page (Sarge, 2026-11-02), from site_status_since:
--   inactive       off Assign's lists (grouped last; out of Urgent) and out of
--                  the projections, listed as inactive. Beats the source site's
--                  active flag until marked reopened (cleared) or a pickup after it.
--   closed_season  no projection until the customer's usual season start (its
--                  MODEL_OVERRIDES or detected season), then the oil clock starts there,
--                  so a forgotten "open" costs nothing. No known season: held until
--                  marked open or a pickup.
--   open           marked open for the season: the oil clock starts that day.
-- A pickup after site_status_since ends any of them (the nightly clears it).
alter table public.customers add column if not exists site_status       text;
alter table public.customers add column if not exists site_status_since date;
alter table public.customers drop constraint if exists customers_site_status_check;
alter table public.customers add constraint customers_site_status_check
  check (site_status in ('inactive', 'closed_season', 'open'));
-- Projection type set on the customer page (Sarge, 2026-11-12), in place of
-- the model's own choice and of any MODEL_OVERRIDES entry (the page wins):
--   will_call  listed as will-call, with its estimate (as a detected one)
--   seasonal   a semi-closer open `site_season` ("May-Oct"): held out while
--              closed unless it called recently
--   event      no daily rate (fairs), listed as event-driven
-- Null = automatic. Exported in customer_edits.csv; read by the next nightly.
alter table public.customers add column if not exists site_model  text;
alter table public.customers add column if not exists site_season text;
alter table public.customers drop constraint if exists customers_site_model_check;
alter table public.customers add constraint customers_site_model_check
  check ((site_model is null and site_season is null)
         or (site_model in ('will_call', 'event') and site_season is null)
         or (site_model = 'seasonal' and site_season ~
           '^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$'));

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
  -- 'newbiedoo' is the truck named Cub (renamed 2026-11-02; the key is kept, see route-utils.js)
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

-- A barrel drop recorded beside the day's stop (Sarge, 2026-11-14): `extra`
-- marks a 3 (barrel delivery) saved with My Route's Barrel drop button or by
-- ticking a Barrel drop task. It sits beside that day's pickup, Empty or theft
-- and never closes the stop; a 3 typed in the gallons box is the stop's own
-- entry and closes it. The nightly files read only the gallons (3 = a code in
-- oil_non_pickups.csv), so this changes nothing downstream.
alter table public.collections add column if not exists extra boolean not null default false;

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

-- Today's note (Jim, 2026-10-29): a note for one customer on one day ("they
-- want the barrel dropped off here", "owner says both barrels full"), beside
-- the permanent `customers.notes`. One per customer and day: a second save that
-- day is an edit, and an emptied note is deleted. Pages show a note the day it
-- is written and all of the next (Sarge, 2026-11-01: Jim writes them the night
-- before a route), then it clears; old days stay as a record.
create table if not exists public.daily_notes (
  customer_id integer not null references public.customers (id),
  day         date not null default ((now() at time zone 'America/New_York')::date),
  note        text not null check (char_length(note) between 1 and 1000),
  updated_at  timestamptz not null default now(),
  updated_by  uuid default auth.uid(),
  primary key (customer_id, day)
);
drop trigger if exists daily_notes_stamp on public.daily_notes;
create trigger daily_notes_stamp before update on public.daily_notes
  for each row execute function public.stamp_collection_update();

-- Route tasks (Sarge, 2026-11-14): checklist rows on a route that are not oil
-- pickups ("Load cones", "Pick up fittings at Shop"), shown at the top of My
-- Route. Their own table: supabase_sync.py --routes rewrites route_stops every
-- morning and never touches this. `day` is the day it was written; an unticked
-- task carries over to later days until ticked or removed, and a ticked one
-- shows until the end of the day it was ticked. Remove only sets `removed_at`
-- (Undo clears it): rows are kept, though nothing shows old ones. A task may
-- name a customer; `kind` 'barrel_drop' (customer required) records a barrel
-- delivery when ticked: the 3 row in collections whose client_id is
-- `drop_client_id`, deleted again on untick. Never counted in a route total.
create table if not exists public.route_tasks (
  id             bigint generated always as identity primary key,
  client_id      uuid not null unique,
  route_id       integer not null references public.routes (id) on delete cascade,
  day            date not null default ((now() at time zone 'America/New_York')::date),
  position       integer not null default 0,
  kind           text not null default 'task' check (kind in ('task', 'barrel_drop')),
  text           text not null default '' check (char_length(text) <= 200),
  customer_id    integer references public.customers (id),
  note           text check (note is null or char_length(note) <= 500),
  done_at        timestamptz,
  done_by        uuid,
  drop_client_id uuid,
  removed_at     timestamptz,
  created_by     uuid default auth.uid(),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz,
  updated_by     uuid,
  check (kind = 'task' and char_length(text) >= 1 or kind = 'barrel_drop' and customer_id is not null)
);
create index if not exists route_tasks_route_idx on public.route_tasks (route_id, position);
drop trigger if exists route_tasks_stamp on public.route_tasks;
create trigger route_tasks_stamp before update on public.route_tasks
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
alter table public.daily_notes  enable row level security;
alter table public.route_tasks  enable row level security;

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
  foreach t in array array['customers', 'routes', 'route_stops', 'route_trucks', 'collections', 'outgoing', 'oil_left', 'daily_notes',
                       'route_tasks'] loop
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
              public.collection_history, public.photos, public.theft_log, public.oil_left,
              public.daily_notes, public.route_tasks from anon;
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
      public.route_trucks, public.collections, public.outgoing, public.oil_left, public.daily_notes,
      public.route_tasks to authenticated;

-- Live updates: Jim sees Sarge's route edits without reloading. Realtime still
-- applies the policies above.
do $$
declare t text;
begin
  foreach t in array array['route_stops', 'route_trucks', 'collections', 'outgoing', 'oil_left', 'daily_notes', 'route_tasks'] loop
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
  kind    text not null check (kind in ('seed', 'nightly', 'routes', 'sweep')),
  summary text not null,
  report  jsonb not null default '{}'
);
alter table public.sync_reports drop constraint if exists sync_reports_kind_check;
alter table public.sync_reports add constraint sync_reports_kind_check
  check (kind in ('seed', 'nightly', 'routes', 'sweep'));
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

-- A customer's other numbers and emails (a manager's cell, the kitchen line),
-- beside the main `customers.phone`. Empty until Jim's contacts are checked
-- against the web lookup's secondary numbers. Members only, like customers.
create table if not exists public.customer_contacts (
  id          bigint generated always as identity primary key,
  customer_id integer not null references public.customers (id) on delete cascade,
  kind        text not null check (kind in ('phone', 'email')),
  value       text not null,   -- phone +1XXXXXXXXXX, email lower-cased
  label       text,            -- "kitchen", "owner's cell"
  person      text,
  source      text not null check (source in ('jamaal', 'site', 'claude_web', 'vcard')),
  added_at    timestamptz not null default now(),
  added_by    uuid default auth.uid(),
  unique (customer_id, kind, value)
);
alter table public.customer_contacts enable row level security;
drop policy if exists "members only" on public.customer_contacts;
create policy "members only" on public.customer_contacts for all to authenticated
  using ((select public.is_member())) with check ((select public.is_member()));
revoke all on public.customer_contacts from anon;
grant select, insert, update, delete on public.customer_contacts to authenticated;

-- The office's own numbers, for My Route's "Call ▾" and the Ops search's People
-- (Jim, Stacey, Sarge, Patrick): one row per
-- person a driver may need to ring from the truck, in menu order. Added by hand
-- in the SQL editor, never through the website or the repo (the site is public;
-- these numbers live only here, behind the login). `name` is what the menu shows
-- ("Call Jim"); a member signed in under the same name on `people` does not see
-- their own. For example:
--   insert into public.office_phones (name, phone, position, role) values
--     ('Jim', '+18025550100', 1, 'Owner'), ('Stacey', '+18025550101', 2, 'Manager')
--   on conflict (name) do update set phone = excluded.phone, position = excluded.position,
--     role = excluded.role;
-- `role` (Sarge, 2026-11-25) shows under the name in search: Owner, Manager, Ops & data, Driver.
create table if not exists public.office_phones (
  name     text primary key check (char_length(name) between 1 and 40),
  phone    text not null check (phone ~ '^\+?[0-9]{7,15}$'),
  position integer not null default 0
);
alter table public.office_phones add column if not exists role text check (char_length(role) <= 40);
alter table public.office_phones enable row level security;
drop policy if exists "members read" on public.office_phones;
create policy "members read" on public.office_phones for select to authenticated
  using ((select public.is_member()));
revoke all on public.office_phones from anon, authenticated;
grant select on public.office_phones to authenticated;

-- Who changed what (Sarge, 2026-11-16): a private log for settling duplicates
-- and disagreements in the SQL editor, never shown on the site. Every insert,
-- update and delete a signed-in person makes on these tables, with the row
-- before and after. No policy and no grant: the site cannot read it (RLS on,
-- nothing allowed); the SQL editor and the backup's secret key can. Writes
-- with no person behind them (the nightly, the 7am route reset, the SQL
-- editor) are not logged: they would fill the free plan's space nightly. An
-- update that changes nothing is skipped. For example, Oil Left by name:
--   select a.changed_at at time zone 'America/New_York' as at, p.name, a.op, a.old_row, a.new_row
--   from public.activity_log a left join public.people p on p.user_id = a.changed_by
--   where a.table_name = 'oil_left' order by a.changed_at desc;
create table if not exists public.activity_log (
  id         bigint generated always as identity primary key,
  table_name text not null,
  op         text not null check (op in ('insert', 'update', 'delete')),
  old_row    jsonb,
  new_row    jsonb,
  changed_by uuid not null,
  changed_at timestamptz not null default now()
);
create index if not exists activity_log_idx on public.activity_log (table_name, changed_at);
alter table public.activity_log enable row level security;
revoke all on public.activity_log from anon, authenticated;

-- SECURITY DEFINER: writes the log, which the site cannot.
create or replace function public.log_activity()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  who uuid := auth.uid();
  o jsonb;
  n jsonb;
begin
  if who is null then return null; end if;
  if tg_op <> 'INSERT' then o := to_jsonb(old); end if;
  if tg_op <> 'DELETE' then n := to_jsonb(new); end if;
  -- Ignore the update stamps when deciding whether anything changed.
  if tg_op = 'UPDATE' and (o - 'updated_at' - 'updated_by') = (n - 'updated_at' - 'updated_by') then
    return null;
  end if;
  insert into public.activity_log (table_name, op, old_row, new_row, changed_by)
  values (tg_table_name, lower(tg_op), o, n, who);
  return null;
end $$;
revoke all on function public.log_activity() from public, anon, authenticated;

do $$
declare t text;
begin
  foreach t in array array['collections', 'oil_left', 'daily_notes', 'route_stops', 'customers',
                           'route_tasks', 'route_trucks'] loop
    execute format('drop trigger if exists %I on public.%I', t || '_activity', t);
    execute format('create trigger %I after insert or update or delete on public.%I
                    for each row execute function public.log_activity()', t || '_activity', t);
  end loop;
end $$;

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
