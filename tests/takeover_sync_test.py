#!/usr/bin/env python3
"""
Taking over from, in the nightly sync (supabase_sync.py), on made-up rows, no
network. Run from the repo root:

    python3 tests/takeover_sync_test.py

A customer taken over by a new account on the site (customers.replaced_by)
stays inactive whatever the source site says, and the 7am route reset puts
the new account in its place.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

import supabase_sync as ss  # noqa: E402

fails = 0


def ok(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


class Db:
    def __init__(self, customers, broken=False):
        self.customers, self.broken = customers, broken

    def select(self, table, query="select=*"):
        if self.broken:
            raise ss.SyncError("customers: HTTP 400 column customers.replaced_by does not exist")
        return [dict(r) for r in self.customers]


db = Db([{"id": 10, "replaced_by": -5}, {"id": -5, "replaced_by": -6}, {"id": -6}, {"id": 11}])
replaced = ss.replaced_map(db)
ok(replaced == {10: -5, -5: -6}, "replaced_map lists only customers with a successor")
ok(ss.replaced_map(Db([], broken=True)) == {}, "before the column exists the sync carries on with none")
ok(ss.successor(10, replaced) == -6 and ss.successor(11, replaced) == 11,
   "a chain of takeovers ends at the newest account; others are themselves")
ok(ss.successor(1, {1: 2, 2: 1}) in (1, 2), "a loop cannot hang the sync")

stops = [
    {"route_id": 1, "customer_id": 11, "position": 1, "must_get": False, "jamaal_assignment_id": 900},
    {"route_id": 1, "customer_id": 10, "position": 2, "must_get": True, "jamaal_assignment_id": 901},
    {"route_id": 2, "customer_id": -6, "position": 1, "must_get": False, "jamaal_assignment_id": None},
    {"route_id": 2, "customer_id": 10, "position": 2, "must_get": False, "jamaal_assignment_id": 902},
]
out = ss.take_over_stops(stops, replaced)
ok([(s["route_id"], s["customer_id"]) for s in out] == [(1, 11), (1, -6), (2, -6)],
   "the 7am reset puts the new account in the old one's place, once per route")
ok(out[1]["must_get"] and out[1]["jamaal_assignment_id"] is None and out[0]["jamaal_assignment_id"] == 900,
   "the swapped stop keeps Must Get and drops the source site's assignment id")

snap = {"routes": [{"route_id": 1, "name": "R", "stops": [{"customer_id": 10, "name": "Old"}]}]}
taken = ss.snap_taken_over(snap, replaced)
ok(taken["routes"][0]["stops"][0]["customer_id"] == -6 and snap["routes"][0]["stops"][0]["customer_id"] == 10,
   "routes are compared as taken over, without changing the snapshot")
ok(ss.snap_taken_over(snap, {}) is snap, "no takeovers: the snapshot as it is")

# Renames: the source site keeps no name history, so a takeover on the old
# account shows up only as a new name. Listed, never decided.
existing = {208: {"id": 208, "name": "Blue Stone-Waitsfield"}, 209: {"id": 209, "name": "Zen  Garden"},
            210: {"id": 210, "name": None}}
wanted = {208: {"name": "Mad Moose/Blue Stone-Waitsfield"}, 209: {"name": "zen garden"},
          210: {"name": "New Spot"}, 211: {"name": "Brand New"}}
ren = ss.renamed_customers(existing, wanted)
ok(ren == [{"id": 208, "was": "Blue Stone-Waitsfield", "now": "Mad Moose/Blue Stone-Waitsfield"}],
   "a changed name is a possible handover; spacing or case alone, a blank old name or a new customer is not")
ok("1 customer(s) renamed" in ss.summary_text([], {"only_site": [], "only_jamaal": [], "gallons_differ": []}, ren),
   "the nightly summary says so")
ok("renamed" not in ss.summary_text([], {"only_site": [], "only_jamaal": [], "gallons_differ": []}),
   "and stays quiet without one")

print(f"\n{fails} FAILURE(S)" if fails else "\nALL CHECKS PASSED")
sys.exit(1 if fails else 0)
