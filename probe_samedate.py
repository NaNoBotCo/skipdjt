#!/usr/bin/env python3
"""Feasibility probe: can we compare DJT vs FLL/MIA on the SAME departure date?

The site currently compares each airport's cheapest fare on ANY date, which
inflates savings — DJT on Nov 20 vs Fort Lauderdale on Aug 14 is a season
comparison, not an airport one. Same-date matching fixes that.

The trick is that it costs no extra API calls. Each query already returns up to
30 offers, every one stamped with its own departure_at. So we intersect the date
sets instead of taking a global minimum.

The risk is coverage: DJT is thinly cached, so matching dates may barely exist.
This measures that BEFORE any of it goes on the page. If matches are rare, the
approach fails and we say so rather than shipping a prettier version of the
same bias.

Usage:  python3 probe_samedate.py
"""
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
AVOID = ["PBI", "DJT"]
ALTS = ["FLL", "MIA"]
DESTINATIONS = [
    "JFK", "LGA", "EWR", "BOS", "DCA", "IAD", "PHL", "CLT", "ATL", "ORD",
    "DTW", "DEN", "DFW", "IAH", "LAX", "SFO", "SEA", "LAS", "PHX", "NAS", "LHR",
]
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def token():
    t = os.environ.get("TP_TOKEN", "").strip()
    if t:
        return t
    p = os.path.join(HERE, ".tp_token")
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    sys.exit("No token found.")


def offers(tok, origin, dest):
    """All cached offers -> {date: cheapest_price} for that origin/destination."""
    q = urllib.parse.urlencode({
        "origin": origin, "destination": dest, "currency": "usd",
        "limit": 30, "sorting": "price", "one_way": "false", "token": tok,
    })
    try:
        with urllib.request.urlopen(f"{API}?{q}", timeout=30) as r:
            data = json.load(r).get("data") or []
    except Exception:  # noqa: BLE001
        return {}
    by_date = {}
    for o in data:
        d = (o.get("departure_at") or "")[:10]
        p = o.get("price")
        if d and p and (d not in by_date or p < by_date[d]):
            by_date[d] = p
    return by_date


def main():
    tok = token()
    print(f"\n{'=' * 76}")
    print(f"  {BOLD}SAME-DATE FEASIBILITY{RESET}   can we compare like for like?")
    print(f"{'=' * 76}\n")
    print(f"  {'DEST':<6}{'DJT dates':>11}{'alt dates':>11}{'MATCHED':>9}"
          f"{'same-date median (range)':>22}{'any-date':>10}")
    print("  " + "-" * 72)

    same, anyd, matched_routes, total_matches = [], [], 0, 0
    all_pairs = []

    for dest in DESTINATIONS:
        djt = {}
        for c in AVOID:
            for d, p in offers(tok, c, dest).items():
                if d not in djt or p < djt[d]:
                    djt[d] = p
            time.sleep(0.3)

        alt = {}
        for c in ALTS:
            for d, p in offers(tok, c, dest).items():
                if d not in alt or p < alt[d]:
                    alt[d] = p
            time.sleep(0.3)

        shared = sorted(set(djt) & set(alt))
        total_matches += len(shared)

        if shared:
            matched_routes += 1
            # MEDIAN across shared dates, not the max. Taking the best date
            # would cherry-pick the most flattering comparison on every route
            # and manufacture exactly the overstatement we're trying to remove.
            per_date = [djt[d] - alt[d] for d in shared]
            all_pairs.extend(per_date)
            s = statistics.median(per_date)
            same.append(s)
            lo, hi = min(per_date), max(per_date)
            s_txt = f"${s:+.0f} ({lo:+d}..{hi:+d})"
        else:
            s_txt = "—"

        if djt and alt:
            a = min(djt.values()) - min(alt.values())
            anyd.append(a)
            a_txt = f"${a:+d}"
        else:
            a_txt = "—"

        print(f"  {dest:<6}{len(djt):>11}{len(alt):>11}{len(shared):>9}"
              f"{s_txt:>22}{a_txt:>10}")

    print("  " + "-" * 72)
    print(f"\n  {BOLD}VERDICT{RESET}")
    print(f"  Routes with at least one shared date: {BOLD}{matched_routes}{RESET} "
          f"of {len(DESTINATIONS)}   (total matched dates: {total_matches})")

    if same:
        cheaper = len([x for x in same if x > 0])
        pair_cheaper = len([x for x in all_pairs if x > 0])
        print(f"  Same-date, per ROUTE:      cheaper on {BOLD}{cheaper}/{len(same)}{RESET}, "
              f"median of route-medians {BOLD}${statistics.median(same):.0f}{RESET}")
        print(f"  Same-date, per DATE PAIR:  cheaper on {BOLD}{pair_cheaper}/{len(all_pairs)}{RESET}, "
              f"median {BOLD}${statistics.median(all_pairs):.0f}{RESET}, "
              f"range ${min(all_pairs)} to ${max(all_pairs)}")
    if anyd:
        print(f"  Any-date (what the site claims now): median "
              f"${statistics.median(anyd):.0f}, max ${max(anyd)}")
    if same and anyd:
        delta = statistics.median(same) - statistics.median(anyd)
        direction = "UNDERSTATING" if delta > 0 else "overstating"
        print(f"\n  {BOLD}Like-for-like median ${statistics.median(same):.0f} vs "
              f"any-date ${statistics.median(anyd):.0f}{RESET}")
        print(f"  -> the any-date method was {BOLD}{direction}{RESET} the real "
              f"saving by ~${abs(delta):.0f}.")

    print()
    if matched_routes >= 8:
        print(f"  {BOLD}-> VIABLE.{RESET} Enough shared dates to put real "
              f"numbers on the page.")
    elif matched_routes >= 4:
        print(f"  {BOLD}-> MARGINAL.{RESET} Works for some routes; show same-date "
              f"where it exists,\n     stay directional elsewhere.")
    else:
        print(f"  {BOLD}-> NOT VIABLE.{RESET} Too few shared dates. Keep the "
              f"directional claim and\n     do not dress it up as precise.")
    print()


if __name__ == "__main__":
    main()
