# Cutover from Jamaal

The switch that makes this site the only place pickups are entered, and the
nightly build the dashboard and projections from the site's own records.
Prepared 2026-11-15 on the branch `cutover`, held until the day.

## Ready when

- [ ] **14 days in a row of MATCH** in the takeover dry run (Actions >
      Takeover Dry Run > latest run > summary): every pickup, code and task
      entered on the site the same day as in Jamaal, weekends included.
- [ ] Every driver signs in on their own phone, and someone besides Sarge can
      add a user and reset a password (Supabase > Authentication > Users).
- [ ] Field tests on real phones: offline save (airplane mode, 3 pickups,
      close the browser, reopen with signal: all 3 arrive); two phones on one
      stop; two phones editing one route; a pickup after 8pm lands on the right
      day; what a driver's 0 means.
- [ ] The task feature is in use, so to-dos no longer live as "BBB-..." customers.
- [ ] Who enters the shop pump-out (BBB- Pump oil @ Shop, 1078).
- [ ] Supabase usage checked (Project Settings > Usage): database under 500 MB,
      photo storage under 1 GB on the free plan.
- [ ] The nightly, the backup and the dry run all green the week before.

## On the day

Pick a morning when the dry run reads MATCH through yesterday. The steps take
about 30 minutes; nothing changes for drivers until step 6.

1. **Last check.** The latest Takeover Dry Run reads MATCH.
2. **Merge the `cutover` branch** (Claude: bring it up to date with `main`,
   re-run the tests, PR, merge). This changes the nightly, removes the 7am
   route workflow and the dry run, and exports site-added customers'
   capacities.
3. **Turn off the 7am reset in the database.** Paste ALL of `db/schema.sql`
   into Supabase's SQL editor and run it. Then check, in the same editor:
   `select jobname from cron.job;` must not list `morning-routes`.
4. **Retire its token.** Supabase > Project Settings > Vault: delete
   `github_dispatch_token`. GitHub > Settings > Developer settings >
   Fine-grained tokens: revoke the one the morning reset used.
5. **Run the nightly by hand** (Actions > Nightly Oil Data Update > Run
   workflow) and check:
   - every step green, and "Report failed steps" says no failures;
   - "Export site collections" says `Site collections since 2026-10-26: N pickup(s)`;
   - the dashboard and My Route show yesterday's pickups.
6. **Tell Jim and the drivers:** enter everything on the site only. Keep the
   Jamaal login, read-only, for at least a month (see Going back).

## The morning after

- No failure email from the nightly; the run is green.
- The backup repo has a new "Backup YYYY-MM-DD" commit.
- Routes planned on the site yesterday are still there at 7:30 (no reset).
- Yesterday's pickups are on the dashboard, and My Route's projections are
  dated today.
- Two or three customers' Collections tabs show yesterday's entries in blue.

## What stays frozen

- `jamaal_routes.json`: the shadow pages' starting roster. New customers come
  from Add customer; leaving customers are marked Inactive on their page. A
  customer already inactive in Jamaal at the cutover is not on the roster: add
  them again with Add customer.
- `outgoing.json`: Jamaal's outgoing history; new runs are entered on the site.
- `capacity_cache.csv`: Jamaal's last capacities; a capacity changed on a
  customer page wins.
- `planner.html` (unlisted beta) keeps showing Jamaal's last routes.
- The `jamaal_*.py` scripts and the source-site parts of `supabase_sync.py`
  stay in the repo, unused, for going back.

## Going back

Only if the site cannot be used for entry, or the nightly cannot build from it
and a fix is not quick. The backup repo (`blackbear-backup`) holds every
night's database.

1. Revert the cutover merge on `main` (Claude: `git revert -m 1 <merge>`,
   PR, merge). The nightly scrapes Jamaal again from the next run.
2. Restore the 7am reset: paste the `db/schema.sql` from before the cutover
   (git history), create a new dispatch token and store it
   (`select vault.create_secret('<token>', 'github_dispatch_token');`).
3. Enter in Jamaal every pickup made on the site since the cutover (Claude
   can list them from the database).
4. Drivers go back to entering in Jamaal (and on the site, to keep the dry
   run meaningful for the next attempt).
