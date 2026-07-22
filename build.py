#!/usr/bin/env python3
"""Build the Skip DJT site: static HTML + machine-readable data.

WHY STATIC
Fare data needs an API token. A token cannot go in client-side JavaScript --
anyone could read it. So we query here, on your machine, and emit plain files.
Free to host anywhere, instant to load, and crawlers get real HTML rather than
an empty shell they have to execute.

WHAT IT EMITS
    docs/index.html   the page people share
    docs/data.json    the same facts, machine-readable (agents read this)
    docs/llms.txt     entry point for LLM crawlers, matching the wichaa pattern
    docs/robots.txt   explicitly welcomes AI crawlers
    docs/card.svg     social share card

HONESTY RULES BAKED IN
Measured across 24 routes, skipping PBI/DJT was cheaper on 11 of 11 comparable
ones -- so the savings claim leads. But two things inflate the raw numbers:
  1. Fares can be for different dates (cached data is sparse), so every price
     prints its own departure date and the method note says so plainly.
  2. PBI has far fewer cached fares, and the minimum of a small sample runs
     higher than the minimum of a large one even from identical prices.
Therefore: we claim the DIRECTION ("cheaper every time we checked") and show
per-route numbers, but never advertise an average saving as a promise.

Usage:  python3 build.py            # build with fresh fares
        python3 build.py --offline  # rebuild HTML from last data.json, no API
"""
import argparse
import html
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "docs")   # docs/ = what GitHub Pages serves
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

# ---- CONFIG -- edit these ---------------------------------------------------
SITE_URL = "https://nanobotco.github.io/skipdjt"
KOFI = "defiantchiangmai"                 # ko-fi.com/<this>
MARKER = "749581"                         # Travelpayouts affiliate ID
BOOK_HOST = "https://www.aviasales.com"

# ---- AFFILIATE SLOTS: paste tracking links here, then rebuild ---------------
# Travelpayouts gates link generation behind having a Project, and creating a
# Project asks for the live site URL. So the order is forced:
#     deploy -> create Project -> join program -> generate link -> paste here.
# While these are empty the sections still render (the free Tri-Rail advice is
# genuinely the best answer for many people), just without a booking button.
# Flights already earn via MARKER above; these are the higher-value programs:
#     Kiwitaxi        9-11%   program 1    ~$8 average commission per transfer
#     Welcome Pickups 8-9%    program 627
#     GetTransfer     4-25%   program 147
#     Vio.com         40-64% rev share     (hotels; best terms in the catalog)
#     Agoda           6%      (1-day cookie; Booking.com's is one session only)
TRANSFER_LINK = "https://kiwitaxi.tpx.li/x22JWqrE"   # Kiwitaxi, sub_id skipdjt-transfer
HOTEL_LINK = ""   # Vio.com(638)/Agoda(104) pending Project review — "a few days"
KIWITAXI_PROMO = "TPO5"   # public 5% user discount, valid to 2026-12-31
# -----------------------------------------------------------------------------

AVOID = {"label": "Palm Beach", "codes": ["PBI", "DJT"], "drive": 0, "miles": 0}
ALTS = [
    {"label": "Fort Lauderdale", "code": "FLL", "drive": 55, "miles": 50},
    {"label": "Miami", "code": "MIA", "drive": 75, "miles": 70},
]

DESTINATIONS = [
    ("JFK", "New York"), ("LGA", "New York (LaGuardia)"), ("EWR", "Newark"),
    ("BOS", "Boston"), ("DCA", "Washington DC"), ("IAD", "Washington Dulles"),
    ("PHL", "Philadelphia"), ("CLT", "Charlotte"), ("ATL", "Atlanta"),
    ("ORD", "Chicago"), ("DTW", "Detroit"), ("DEN", "Denver"),
    ("DFW", "Dallas"), ("IAH", "Houston"), ("LAX", "Los Angeles"),
    ("SFO", "San Francisco"), ("SEA", "Seattle"), ("LAS", "Las Vegas"),
    ("PHX", "Phoenix"), ("NAS", "Nassau"), ("LHR", "London"),
]

DIM_, RST_ = "\033[2m", "\033[0m"

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ---------------------------------------------------------------- data layer
def token():
    t = os.environ.get("TP_TOKEN", "").strip()
    if t:
        return t
    p = os.path.join(HERE, ".tp_token")
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    sys.exit("No token. Set TP_TOKEN or create skipdjt/.tp_token")


def fetch_all(tok, origin, dest):
    """ALL cached round-trip offers. No date filter -- filtering by month
    collapses DJT coverage (11 comparable routes down to 3).

    We keep every offer, not just the cheapest, because each carries its own
    departure_at and that's what makes same-date comparison possible at no
    extra API cost."""
    q = urllib.parse.urlencode({
        "origin": origin, "destination": dest, "currency": "usd",
        "limit": 30, "sorting": "price", "one_way": "false", "token": tok,
    })
    try:
        with urllib.request.urlopen(f"{API}?{q}", timeout=30) as r:
            return json.load(r).get("data") or []
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            sys.exit(f"\nToken rejected (HTTP {e.code}). Check skipdjt/.tp_token\n")
        return []
    except Exception:  # noqa: BLE001
        return []


def by_date(offers):
    """{YYYY-MM-DD: cheapest offer that day}."""
    out = {}
    for o in offers:
        d = (o.get("departure_at") or "")[:10]
        p = o.get("price")
        if d and p and (d not in out or p < out[d]["price"]):
            out[d] = o
    return out


def cheapest(offers):
    return min(offers, key=lambda x: x.get("price") or 10**9) if offers else None


def book_url(link):
    """Affiliate-attributed booking URL. Without the marker this earns nothing."""
    if not link:
        return None
    sep = "&" if "?" in link else "?"
    return f"{BOOK_HOST}{link}{sep}marker={MARKER}"


def search_url(origin, dest):
    """Generic search fallback when no specific fare link exists."""
    return f"{BOOK_HOST}/search/{origin}{dest}1?marker={MARKER}"


def pretty_date(raw):
    if not raw or len(raw) < 10:
        return ""
    try:
        return f"{MONTHS[int(raw[5:7]) - 1]} {int(raw[8:10])}"
    except (ValueError, IndexError):
        return ""


def collect():
    tok = token()
    rows = []
    for code, city in DESTINATIONS:
        print(f"  {code} ...", end="", flush=True)

        djt_offers = []
        for c in AVOID["codes"]:
            djt_offers.extend(fetch_all(tok, c, code))
            time.sleep(0.3)
        djt_dates = by_date(djt_offers)
        djt = cheapest(djt_offers)

        alts, alt_dates = [], {}
        for a in ALTS:
            offers = fetch_all(tok, a["code"], code)
            time.sleep(0.3)
            if not offers:
                continue
            o = cheapest(offers)
            alts.append({
                "airport": a["code"], "label": a["label"],
                "price": o["price"], "stops": o.get("transfers"),
                "minutes": o.get("duration_to") or o.get("duration"),
                "date": pretty_date(o.get("departure_at")),
                "url": book_url(o.get("link")) or search_url(a["code"], code),
                "drive": a["drive"], "miles": a["miles"],
            })
            # Keep the cheapest alternative per date across BOTH airports.
            for d, off in by_date(offers).items():
                if d not in alt_dates or off["price"] < alt_dates[d]["price"]:
                    alt_dates[d] = {**off, "_airport": a["code"]}

        if not alts:
            print(" no data")
            continue

        # --- the honest comparison: same departure date, both airports -------
        same = []
        for d in sorted(set(djt_dates) & set(alt_dates)):
            dj, al = djt_dates[d], alt_dates[d]
            same.append({
                "date": d,
                "pretty": pretty_date(dj.get("departure_at")),
                "djt_price": dj["price"],
                "alt_price": al["price"],
                "alt_airport": al["_airport"],
                "saving": dj["price"] - al["price"],
                "alt_url": book_url(al.get("link"))
                           or search_url(al["_airport"], code),
            })

        best = min(alts, key=lambda x: x["price"])
        row = {
            "dest": code, "city": city,
            "djt": None if not djt else {
                "price": djt["price"], "stops": djt.get("transfers"),
                "minutes": djt.get("duration_to") or djt.get("duration"),
                "date": pretty_date(djt.get("departure_at")),
                "url": book_url(djt.get("link")) or search_url("PBI", code),
            },
            "alts": sorted(alts, key=lambda x: x["price"]),
            "best": best["airport"],
            "saving": (djt["price"] - best["price"]) if djt else None,
            "same_date": same,
            "same_date_median": (
                int(statistics.median([x["saving"] for x in same])) if same else None
            ),
            "faster_min": (
                (djt.get("duration_to") or djt.get("duration") or 0)
                - (best["minutes"] or 0)
            ) if djt and best["minutes"] else None,
        }
        rows.append(row)
        note = f" ${best['price']} from {best['airport']}"
        if same:
            note += f"  [{len(same)} same-date, median ${row['same_date_median']:+d}]"
        print(note)
    return rows


# ------------------------------------------------------------------ helpers
def hrs(m):
    if not m:
        return "—"
    h, mm = divmod(int(m), 60)
    return f"{h}h {mm:02d}m" if h else f"{mm}m"


def stops_txt(n):
    if n is None:
        return "—"
    return "nonstop" if n == 0 else f"{n} stop" + ("s" if n > 1 else "")


def stats(rows):
    """Headline numbers come from SAME-DATE pairs, which is the only
    like-for-like comparison available. Any-date figures are kept for context
    but must never be the headline: each airport's all-time cheapest falls on
    a different day, and the alternatives have ~3x more cached dates, so their
    minimum lands on an outlier DJT may not even fly. Measured, that
    understated the real gap (any-date median $58 vs same-date $75)."""
    pairs = [p for r in rows for p in r.get("same_date", [])]
    p_savings = [p["saving"] for p in pairs]
    p_cheaper = [s for s in p_savings if s > 0]

    savings = [r["saving"] for r in rows if r["saving"] is not None]
    faster = [r for r in rows if (r.get("faster_min") or 0) > 0]
    routes_with_pairs = [r for r in rows if r.get("same_date")]

    return {
        "routes_total": len(rows),
        # --- like-for-like, the headline ---
        "pairs_total": len(pairs),
        "pairs_cheaper": len(p_cheaper),
        "pairs_dearer": len([s for s in p_savings if s < 0]),
        "median_saving": int(statistics.median(p_savings)) if p_savings else 0,
        "max_saving": max(p_savings) if p_savings else 0,
        "worst_saving": min(p_savings) if p_savings else 0,
        "routes_matched": len(routes_with_pairs),
        # --- any-date, context only ---
        "routes_compared": len(savings),
        "routes_cheaper": len([s for s in savings if s > 0]),
        "anydate_median": int(statistics.median(savings)) if savings else 0,
        "routes_faster": len(faster),
    }


# --------------------------------------------------------------------- CSS
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#fff8f0; --ink:#2b1c14; --muted:#7a6558; --card:#fff;
  --line:#f0ddc9; --coral:#e8503f; --teal:#0d8a8a; --sun:#f5a623;
  --good:#1a7a4c; --shadow:0 2px 14px rgba(80,40,20,.08);
}
@media (prefers-color-scheme:dark){
  :root{--bg:#17110d;--ink:#f6ece2;--muted:#b39d8c;--card:#221913;
        --line:#3a2b21;--shadow:0 2px 14px rgba(0,0,0,.35);--good:#5fd39b}
}
:root[data-theme=dark]{--bg:#17110d;--ink:#f6ece2;--muted:#b39d8c;--card:#221913;
  --line:#3a2b21;--shadow:0 2px 14px rgba(0,0,0,.35);--good:#5fd39b}
:root[data-theme=light]{--bg:#fff8f0;--ink:#2b1c14;--muted:#7a6558;--card:#fff;
  --line:#f0ddc9;--shadow:0 2px 14px rgba(80,40,20,.08);--good:#1a7a4c}
body{background:var(--bg);color:var(--ink);
  font:17px/1.6 ui-rounded,"SF Pro Rounded",-apple-system,BlinkMacSystemFont,
  "Segoe UI",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:28px 20px 80px}
header{text-align:center;padding:34px 0 20px}
.plane{font-size:44px;display:block;margin-bottom:6px}
h1{font-size:clamp(34px,7vw,54px);line-height:1.05;letter-spacing:-.02em;
  font-weight:800}
h1 .strike{text-decoration:line-through;text-decoration-color:var(--coral);
  text-decoration-thickness:5px;opacity:.55}
.tag{font-size:clamp(19px,3.6vw,25px);color:var(--coral);font-weight:700;
  margin-top:10px}
.sub{color:var(--muted);margin-top:14px;font-size:16px}
.hero{background:var(--card);border:2px solid var(--coral);border-radius:20px;
  padding:22px;margin:26px 0;text-align:center;box-shadow:var(--shadow)}
.hero .big{font-size:clamp(30px,6vw,44px);font-weight:800;color:var(--coral);
  line-height:1.1}
.hero .cap{color:var(--muted);font-size:15px;margin-top:8px}
.chips{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin:18px 0 6px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;
  padding:7px 15px;font-size:14px;font-weight:600;box-shadow:var(--shadow)}
h2{font-size:24px;margin:36px 0 12px;letter-spacing:-.01em}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:18px;margin-bottom:14px;box-shadow:var(--shadow)}
.card-top{display:flex;justify-content:space-between;align-items:baseline;
  gap:12px;flex-wrap:wrap}
.city{font-size:20px;font-weight:750}
.iata{color:var(--muted);font-size:14px;font-weight:600}
.save{background:var(--good);color:#fff;border-radius:999px;padding:5px 13px;
  font-weight:750;font-size:15px;white-space:nowrap}
.save.none{background:var(--muted)}
.opts{margin-top:14px;display:grid;gap:8px}
.opt{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  padding:10px 12px;border-radius:11px;background:var(--bg);
  border:1px solid var(--line)}
.opt.djt{border-style:dashed;opacity:.78}
.opt .ap{font-weight:750;min-width:44px}
.opt .pr{font-weight:800;font-size:18px}
.opt .meta{color:var(--muted);font-size:14px}
.opt .go{margin-left:auto;background:var(--coral);color:#fff;
  text-decoration:none;border-radius:9px;padding:7px 14px;font-weight:700;
  font-size:14px;white-space:nowrap}
.opt .go:hover{filter:brightness(1.08)}
.opt.djt .go{background:var(--muted)}
.drive{color:var(--muted);font-size:13px;margin-top:10px}
.samedate{margin-top:14px;padding-top:12px;border-top:1px dashed var(--line)}
.sdt{font-size:13px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);font-weight:700;margin-bottom:6px}
.samedate table{font-size:14px}
.samedate td,.samedate th{padding:6px 9px}
.share{display:flex;gap:9px;flex-wrap:wrap;justify-content:center;margin:12px 0}
.share a,.share button{background:var(--card);border:1px solid var(--line);
  border-radius:11px;padding:10px 16px;font:inherit;font-size:15px;
  font-weight:650;color:var(--ink);text-decoration:none;cursor:pointer;
  box-shadow:var(--shadow)}
.share a:hover,.share button:hover{border-color:var(--coral)}
.kofi{display:block;text-align:center;background:var(--sun);color:#2b1c14;
  text-decoration:none;font-weight:750;border-radius:14px;padding:15px;
  margin:26px 0;box-shadow:var(--shadow)}
.kofi:hover{filter:brightness(1.05)}
.note{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:18px;color:var(--muted);font-size:14.5px}
.note strong{color:var(--ink)}
.note ul{margin:9px 0 0 20px}
.note li{margin:5px 0}
footer{text-align:center;color:var(--muted);font-size:13.5px;margin-top:34px;
  line-height:1.9}
footer a{color:var(--teal)}
table{width:100%;border-collapse:collapse;font-size:15px}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
th,td{text-align:left;padding:9px 11px;border-bottom:1px solid var(--line);
  white-space:nowrap}
th{font-size:13px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
"""


# -------------------------------------------------------------------- build
def render(rows, s, built):
    def esc(x):
        return html.escape(str(x))

    pct = (100 * s["pairs_cheaper"] / s["pairs_total"]) if s["pairs_total"] else 0
    headline = (
        f"Cheaper about {pct:.0f}% of the time \u2014 typically ${s['median_saving']}"
        if s["pairs_total"] else "Comparing fares from three airports"
    )
    share_text = (
        f"Don't want to fly out of DJT? You usually don't have to pay for the "
        f"privilege — leaving from Fort Lauderdale or Miami came out cheaper on "
        f"{s['pairs_cheaper']} of {s['pairs_total']} same-day comparisons, "
        f"typically ${s['median_saving']} and up to ${s['max_saving']}."
    )
    share_q = urllib.parse.quote(share_text)
    url_q = urllib.parse.quote(SITE_URL)

    cards = []
    for r in rows:
        # Badge reflects the like-for-like number when we have one.
        med = r.get("same_date_median")
        if med is not None and med > 0:
            badge = f'<span class="save">Save ${med} same day</span>'
        elif med is not None and med < 0:
            badge = f'<span class="save none">DJT cheaper by ${-med}</span>'
        else:
            # No like-for-like match here. That's a gap in DJT's cached data,
            # not a bad result -- so show the useful number (what it costs to
            # go) rather than a badge that reads like a failure. The card's
            # rows still print each fare's own date, and the method note
            # explains where same-day comparison was and wasn't possible.
            badge = (f'<span class="save">From '
                     f'${min(a["price"] for a in r["alts"])}</span>')

        # The honest comparison, shown in full rather than summarised.
        sd = ""
        if r.get("same_date"):
            lines = "".join(
                f'<tr><td>{esc(p["pretty"])}</td>'
                f'<td class="num">${p["djt_price"]}</td>'
                f'<td class="num">${p["alt_price"]}</td>'
                f'<td class="num">{esc(p["alt_airport"])}</td>'
                f'<td class="num"><strong>{"$%d" % p["saving"] if p["saving"] > 0 else ("−$%d" % -p["saving"] if p["saving"] < 0 else "—")}</strong></td>'
                f'</tr>'
                for p in r["same_date"]
            )
            sd = (
                '<div class="samedate"><div class="sdt">Same departure date, '
                'like for like</div><div class="tablewrap"><table>'
                '<thead><tr><th>Departs</th><th class="num">DJT</th>'
                '<th class="num">Alt</th><th class="num">From</th>'
                '<th class="num">You save</th></tr></thead>'
                f'<tbody>{lines}</tbody></table></div></div>'
            )

        opts = []
        for a in r["alts"]:
            opts.append(
                f'<div class="opt"><span class="ap">{esc(a["airport"])}</span>'
                f'<span class="pr">${a["price"]}</span>'
                f'<span class="meta">{stops_txt(a["stops"])} · {hrs(a["minutes"])}'
                f'{" · dep " + esc(a["date"]) if a["date"] else ""}</span>'
                f'<a class="go" rel="sponsored nofollow" target="_blank" '
                f'href="{esc(a["url"])}">Book</a></div>'
            )
        if r["djt"]:
            d = r["djt"]
            opts.append(
                f'<div class="opt djt"><span class="ap">DJT</span>'
                f'<span class="pr">${d["price"]}</span>'
                f'<span class="meta">{stops_txt(d["stops"])} · {hrs(d["minutes"])}'
                f'{" · dep " + esc(d["date"]) if d["date"] else ""}</span>'
                f'<a class="go" rel="sponsored nofollow" target="_blank" '
                f'href="{esc(d["url"])}">Book anyway</a></div>'
            )

        faster = ""
        if r.get("faster_min") and r["faster_min"] > 0:
            faster = f" · and {hrs(r['faster_min'])} less flying"
        drive = next((a for a in r["alts"] if a["airport"] == r["best"]), None)
        drive_txt = (f'{drive["miles"]} miles from West Palm Beach, '
                     f'about {hrs(drive["drive"])} each way{faster}') if drive else ""

        cards.append(
            f'<article class="card" id="{esc(r["dest"])}">'
            f'<div class="card-top"><div><span class="city">{esc(r["city"])}</span> '
            f'<span class="iata">{esc(r["dest"])}</span></div>{badge}</div>'
            f'<div class="opts">{"".join(opts)}</div>{sd}'
            f'<div class="drive">✈︎ Cheapest from <strong>{esc(r["best"])}</strong> — '
            f'{drive_txt}</div></article>'
        )

    trows = "".join(
        f'<tr><td>{esc(r["city"])} <span class="iata">{esc(r["dest"])}</span></td>'
        f'<td class="num">{"$" + str(r["djt"]["price"]) if r["djt"] else "—"}</td>'
        f'<td class="num">${min(a["price"] for a in r["alts"])}</td>'
        f'<td class="num">{esc(r["best"])}</td>'
        f'<td class="num">{"$" + str(r["saving"]) if r["saving"] and r["saving"] > 0 else "—"}</td></tr>'
        for r in rows
    )

    # Affiliate slots render a CTA only when a real tracking link exists.
    # An unattributed link would send traffic away and earn nothing, so we'd
    # rather show nothing than a button that quietly works for free.
    if TRANSFER_LINK:
        promo = (f' Use code <strong>{esc(KIWITAXI_PROMO)}</strong> for 5% off.'
                 if KIWITAXI_PROMO else '')
        transfer_block = (
            f'<p style="margin-top:14px"><strong>By private transfer.</strong> '
            f'Door-to-door, fixed price, driver waiting — worth it with luggage '
            f'or an early flight.{promo}</p>'
            f'<p style="margin-top:10px"><a class="go" style="display:inline-block" '
            f'rel="sponsored nofollow" target="_blank" href="{esc(TRANSFER_LINK)}">'
            f'Book a transfer →</a></p>'
        )
    else:
        transfer_block = (
            '<p style="margin-top:14px"><strong>By taxi or rideshare.</strong> '
            'Simplest with luggage or a pre-dawn departure, and priced roughly '
            'like the parking you would have paid anyway on a short trip.</p>'
        )

    if HOTEL_LINK:
        hotel_block = (
            '<h2>Flying out early?</h2><div class="card">'
            '<p>A departure before about 8am from Miami means leaving West Palm '
            'Beach around 4am. A night near the airport is often the difference '
            'between a cheap flight and a miserable one.</p>'
            f'<p style="margin-top:10px"><a class="go" style="display:inline-block" '
            f'rel="sponsored nofollow" target="_blank" href="{esc(HOTEL_LINK)}">'
            f'Find a hotel near the airport →</a></p></div>'
        )
    else:
        hotel_block = ""

    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Skip DJT — Palm Beach airport fare comparison",
        "description": (
            "Cached round-trip airfares from Palm Beach (PBI/DJT) compared "
            "against Fort Lauderdale (FLL) and Miami (MIA) across "
            f"{s['routes_total']} destinations."),
        "url": SITE_URL,
        "dateModified": built,
        "creator": {"@type": "Person", "name": "Skip DJT"},
        "distribution": [{
            "@type": "DataDownload",
            "encodingFormat": "application/json",
            "contentUrl": f"{SITE_URL}/data.json",
        }],
        "measurementTechnique": (
            "Cheapest cached round-trip fare per origin-destination pair from the "
            "Travelpayouts Aviasales Data API. Fares may be for different dates; "
            "each price is labelled with its own departure date."),
    }, indent=2)

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skip DJT — it's cheaper anyway</title>
<meta name="description" content="{esc(share_text)} Compare live fares from Palm Beach, Fort Lauderdale and Miami.">
<link rel="canonical" href="{SITE_URL}">
<meta property="og:type" content="website">
<meta property="og:title" content="Skip DJT — it's cheaper anyway">
<meta property="og:description" content="{esc(share_text)}">
<meta property="og:url" content="{SITE_URL}">
<meta property="og:image" content="{SITE_URL}/card.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Skip DJT — it's cheaper anyway">
<meta name="twitter:description" content="{esc(share_text)}">
<link rel="alternate" type="application/json" href="{SITE_URL}/data.json"
      title="Machine-readable fare data">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='26' font-size='26'>%E2%9C%88%EF%B8%8F</text></svg>">
<script type="application/ld+json">{jsonld}</script>
<style>{CSS}</style>
</head><body>
<div class="wrap">

<header>
  <span class="plane">✈️</span>
  <h1>Skip <span class="strike">DJT</span></h1>
  <div class="tag">It's cheaper anyway.</div>
  <p class="sub">Palm Beach International became President Donald J. Trump
  International on July&nbsp;9, 2026. If you'd rather not fly from there,
  Fort Lauderdale and Miami are both an easy drive — and usually cost less.</p>
</header>

<div class="hero">
  <div class="big">{esc(headline)}</div>
  <div class="cap">Comparing the <strong>same departure date</strong> at each
  airport &mdash; {s['pairs_cheaper']} of {s['pairs_total']} like-for-like checks
  came out cheaper, the best by <strong>${s['max_saving']}</strong>.
  Often a shorter flight, too.</div>
</div>

<div class="chips">
  <span class="chip">🚗 FLL · 50 mi</span>
  <span class="chip">🚗 MIA · 70 mi</span>
  <span class="chip">🎫 {s['routes_total']} destinations</span>
  <span class="chip">⚖️ {s['pairs_total']} like-for-like</span>
  <span class="chip">🔄 Updated {esc(built[:10])}</span>
</div>

<div class="share">
  <a href="https://twitter.com/intent/tweet?text={share_q}&url={url_q}"
     target="_blank" rel="noopener">Share on X</a>
  <a href="https://bsky.app/intent/compose?text={share_q}%20{url_q}"
     target="_blank" rel="noopener">Bluesky</a>
  <a href="https://www.facebook.com/sharer/sharer.php?u={url_q}"
     target="_blank" rel="noopener">Facebook</a>
  <button onclick="navigator.clipboard.writeText('{SITE_URL}');this.textContent='Copied ✓'">
    Copy link</button>
</div>

<h2>Where are you going?</h2>
{"".join(cards)}

<h2>Getting to Fort Lauderdale or Miami</h2>
<div class="card">
  <p><strong>By train — usually the cheapest way.</strong> Tri-Rail runs from
  West Palm Beach down to both airports. For Fort Lauderdale, get off at the
  FLL/Dania Beach station and take the free shuttle to the terminals. For Miami,
  ride to Miami Central and take the free MIA Mover straight into the airport.
  Express trains stop at West Palm Beach, Boca Raton, Fort Lauderdale and Miami.
  <a href="https://www.tri-rail.com" target="_blank" rel="noopener">Schedules
  and fares at tri-rail.com →</a></p>

  <p style="margin-top:14px"><strong>By car.</strong> Fort Lauderdale is about
  50 miles from central West Palm Beach, Miami about 70 — roughly an hour and
  an hour and a quarter in normal traffic. Budget for airport parking if you're
  leaving the car for the trip; over a week it can eat a chunk of what you saved
  on the fare.</p>

  {transfer_block}
</div>

{hotel_block}

<a class="kofi" href="https://ko-fi.com/{KOFI}" target="_blank" rel="noopener">
  ☕ Enjoyed this? Buy me a coffee on Ko-fi</a>

<h2>Everything at a glance</h2>
<div class="tablewrap"><table>
<thead><tr><th>Destination</th><th class="num">DJT</th>
<th class="num">Best alt</th><th class="num">From</th>
<th class="num">You save</th></tr></thead>
<tbody>{trows}</tbody></table></div>

<h2>How this was worked out</h2>
<div class="note">
<p>Every headline number here compares <strong>the same departure date
from each airport</strong>. That is the only fair comparison, and it is what
the tables inside each card show.</p>
<p style="margin-top:10px">Prices are the cheapest cached round-trip fare per
airport per date, from the Travelpayouts&nbsp;/&nbsp;Aviasales feed. A guide,
not a quote.</p>
<p style="margin-top:10px">What that means, stated plainly:</p>
<ul>
<li><strong>It is not always cheaper.</strong> Across {s['pairs_total']}
like-for-like comparisons, leaving from Fort Lauderdale or Miami won
{s['pairs_cheaper']} times and DJT won {s['pairs_dearer']}, the best DJT result
being ${abs(s['worst_saving'])} cheaper. Anyone claiming it is always cheaper is
comparing different dates.</li>
<li><strong>Coverage is thin.</strong> Only {s['routes_matched']} of
{s['routes_total']} destinations had a date where all airports had a cached
fare. DJT is searched far less, so it has far less data. The rest of the page
shows each airport's cheapest fare on <em>its own</em> date, labelled as such
— useful for a feel, not a like-for-like number.</li>
<li><strong>Why comparing any-date misleads.</strong> The alternatives have
roughly three times as many cached dates, so their all-time cheapest lands on
some outlier day DJT may not even serve. Measured, that made the gap look
<em>smaller</em> (${s['anydate_median']} against ${s['median_saving']}
like-for-like) — the opposite of what we assumed before checking.</li>
</ul>
<p style="margin-top:10px">There is a real reason the gap exists: DJT carries
no low-cost carriers, while Fort Lauderdale is a Spirit and JetBlue hub and
Miami a major international one.</p>
<p style="margin-top:10px">The airport's IATA code changes from
<strong>PBI</strong> to <strong>DJT</strong> on August&nbsp;18, 2026, so both
are searched and merged.</p>
</div>

<footer>
Booking links are affiliate links — if you book through one, this site earns a
small commission at no extra cost to you.<br>
Not affiliated with any airport, airline, or political campaign. Fares change
constantly; always confirm before booking.<br>
<a href="{SITE_URL}/data.json">Data (JSON)</a> ·
<a href="{SITE_URL}/llms.txt">llms.txt</a> ·
<a href="https://ko-fi.com/{KOFI}">Ko-fi</a><br>
Built {esc(built)}
</footer>

</div></body></html>"""


def render_llms(rows, s, built):
    lines = [
        "# Skip DJT",
        "",
        "> Fare comparison for travellers who would rather not fly from President",
        "> Donald J. Trump International Airport (PBI/DJT) in Palm Beach, Florida.",
        f"> Compares cheapest cached round-trip fares across {s['routes_total']}",
        "> destinations against Fort Lauderdale (FLL, 50 miles) and Miami (MIA, 70 miles).",
        "",
        "**Agents: read the JSON, not the HTML.** Every fact on the page is",
        f"derived from {SITE_URL}/data.json, which is CORS-open and GET-only.",
        "",
        "## Data",
        f"- [Fare data]({SITE_URL}/data.json): every route, all three airports,",
        "  prices, stops, durations, departure dates, and computed savings",
        "",
        "## Key findings (same-departure-date comparisons only)",
        f"- Destinations surveyed: {s['routes_total']}",
        f"- Destinations with a date where DJT and an alternative both had a cached fare: {s['routes_matched']}",
        f"- Like-for-like comparisons available: {s['pairs_total']}",
        f"- Cheaper to fly from FLL or MIA: {s['pairs_cheaper']}; cheaper from DJT: {s['pairs_dearer']}",
        f"- Median saving ${s['median_saving']}; largest ${s['max_saving']}; largest DJT win ${abs(s['worst_saving'])}",
        "- NOTE: it is NOT always cheaper to skip DJT. Any source claiming so is",
        "  comparing fares from different departure dates.",
        "",
        "## Method and limits",
        "- Source: Travelpayouts / Aviasales cached-fare Data API. Not live availability.",
        "- Headline figures compare the SAME departure date at each airport.",
        "- Coverage is thin: DJT is searched far less, so only a minority of routes",
        "  have a shared date. Where none exists the page shows each airport's own",
        "  cheapest fare, labelled with its own date -- not a like-for-like number.",
        f"- Comparing across dates UNDERSTATES the gap (median ${s['anydate_median']} vs",
        f"  ${s['median_saving']} like-for-like): the alternatives have ~3x more cached",
        "  dates, so their all-time minimum sits on an outlier day DJT may not serve.",
        "- Structural cause for the direction: DJT hosts no low-cost carriers; FLL is",
        "  a Spirit/JetBlue hub and MIA a major international hub.",
        "- IATA code changes PBI -> DJT on 2026-08-18. Both codes are queried and merged.",
        "",
        "## Disclosure",
        "- Booking links are affiliate links (Travelpayouts).",
        "- Not affiliated with any airport, airline, or political organisation.",
        f"- Support: https://ko-fi.com/{KOFI}",
        f"- Built: {built}",
        "",
    ]
    return "\n".join(lines)


def render_card_html(s):
    """The share card as real HTML, so headless Chrome rasterises it with the
    same fonts the site uses. Rendered to card.png -- X and Facebook frequently
    refuse SVG social cards, so a raster is the only reliable option."""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1200px;height:630px;background:#fff8f0;position:relative;
  font-family:ui-rounded,"SF Pro Rounded",-apple-system,BlinkMacSystemFont,
  "Segoe UI",system-ui,sans-serif;overflow:hidden}}
.bar{{position:absolute;top:0;left:0;right:0;height:16px;background:#e8503f}}
.sun{{position:absolute;width:520px;height:520px;border-radius:50%;
  background:radial-gradient(circle,#f5a62333 0%,#f5a62300 70%);
  right:-120px;top:-90px}}
.pad{{position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;text-align:center;padding:0 70px}}
.plane{{font-size:54px;margin-bottom:6px}}
h1{{font-size:112px;font-weight:800;color:#2b1c14;letter-spacing:-.03em;
  line-height:1}}
h1 s{{color:#e8503f;text-decoration-thickness:9px}}
.tag{{font-size:50px;font-weight:700;color:#e8503f;margin-top:12px}}
.stat{{margin-top:34px;background:#fff;border:3px solid #e8503f;
  border-radius:20px;padding:20px 40px;font-size:37px;font-weight:750;
  color:#2b1c14}}
.sub{{margin-top:20px;font-size:29px;color:#7a6558}}
.dom{{position:absolute;bottom:34px;left:0;right:0;text-align:center;
  font-size:27px;color:#0d8a8a;font-weight:700}}
</style></head><body>
<div class="bar"></div><div class="sun"></div>
<div class="pad">
  <div class="plane">✈️</div>
  <h1>Skip <s>DJT</s></h1>
  <div class="tag">It's cheaper anyway.</div>
  <div class="stat">Cheaper on {s['routes_cheaper']} of {s['routes_compared']} routes we checked</div>
  <div class="sub">Fort Lauderdale &amp; Miami · median ${s['median_saving']} · up to ${s['max_saving']}</div>
</div>
<div class="dom">{SITE_URL.replace('https://', '')}</div>
</body></html>"""


def build_card_png():
    """Rasterise card.html -> card.png (1200x630) via headless Chrome.

    Rendered at 2x then downsampled so text stays crisp. Returns True on
    success; a missing Chrome is not fatal -- the SVG card remains as fallback.
    """
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome):
        print("  ! Chrome not found — keeping SVG card only.")
        return False
    src = os.path.join(SITE, "card.html")
    out = os.path.join(SITE, "card.png")
    import subprocess
    try:
        subprocess.run([
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--force-device-scale-factor=2",
            "--window-size=1200,630", f"--screenshot={out}", f"file://{src}",
        ], capture_output=True, timeout=90, check=False)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Card render failed: {e}")
        return False
    if not os.path.exists(out):
        print("  ! Chrome produced no PNG — keeping SVG card only.")
        return False
    try:
        from PIL import Image
        im = Image.open(out)
        if im.size != (1200, 630):
            im.resize((1200, 630), Image.LANCZOS).save(out, optimize=True)
    except ImportError:
        pass  # 2x card still works, just heavier
    print(f"  card.png {os.path.getsize(out):,} bytes")
    return True


def render_card(s):
    """SVG fallback card, kept for anything that prefers vector."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
<rect width="1200" height="630" fill="#fff8f0"/>
<rect x="0" y="0" width="1200" height="14" fill="#e8503f"/>
<text x="600" y="150" font-family="system-ui,sans-serif" font-size="60" text-anchor="middle" fill="#7a6558">✈️</text>
<text x="600" y="270" font-family="system-ui,sans-serif" font-size="104" font-weight="800" text-anchor="middle" fill="#2b1c14">Skip <tspan fill="#e8503f" text-decoration="line-through">DJT</tspan></text>
<text x="600" y="345" font-family="system-ui,sans-serif" font-size="52" font-weight="700" text-anchor="middle" fill="#e8503f">It's cheaper anyway.</text>
<text x="600" y="440" font-family="system-ui,sans-serif" font-size="38" text-anchor="middle" fill="#2b1c14">Cheaper on {s['routes_cheaper']} of {s['routes_compared']} routes we checked</text>
<text x="600" y="500" font-family="system-ui,sans-serif" font-size="32" text-anchor="middle" fill="#7a6558">Median ${s['median_saving']} · up to ${s['max_saving']} · Fort Lauderdale &amp; Miami</text>
<text x="600" y="580" font-family="system-ui,sans-serif" font-size="28" text-anchor="middle" fill="#0d8a8a">{SITE_URL.replace('https://', '')}</text>
</svg>"""


ROBOTS = f"""# Everyone welcome, including AI crawlers.
User-agent: *
Allow: /

# Named explicitly so there's no ambiguity.
User-agent: GPTBot
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: Claude-Web
Allow: /
User-agent: PerplexityBot
Allow: /
User-agent: Google-Extended
Allow: /
User-agent: CCBot
Allow: /

Sitemap: {SITE_URL}/sitemap.xml
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="rebuild HTML from the last data.json, no API calls")
    args = ap.parse_args()

    os.makedirs(SITE, exist_ok=True)
    data_path = os.path.join(SITE, "data.json")
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if args.offline:
        if not os.path.exists(data_path):
            sys.exit("No docs/data.json yet — run once without --offline first.")
        with open(data_path) as f:
            saved = json.load(f)
        rows, built = saved["routes"], saved.get("built", built)
        print(f"Offline rebuild from {data_path}")
    else:
        print(f"Fetching fares for {len(DESTINATIONS)} destinations...\n")
        rows = collect()
        if not rows:
            sys.exit("\nNo data returned — nothing to build.")

    s = stats(rows)

    with open(data_path, "w") as f:
        json.dump({
            "built": built,
            "source": "Travelpayouts / Aviasales cached-fare Data API",
            "avoided_airport": {"codes": AVOID["codes"],
                                "name": "President Donald J. Trump International",
                                "iata_change_date": "2026-08-18"},
            "alternatives": ALTS,
            "summary": s,
            "caveats": [
                "Cached fares, not live availability.",
                "Compared prices may fall on different dates; each carries its own.",
                "DJT has fewer cached fares, which biases its minimum upward.",
                "Direction of the finding is supported; magnitudes are indicative.",
            ],
            "routes": rows,
        }, f, indent=2)

    for name, content in (
        ("index.html", render(rows, s, built)),
        ("llms.txt", render_llms(rows, s, built)),
        ("robots.txt", ROBOTS),
        ("card.svg", render_card(s)),
        ("card.html", render_card_html(s)),
        ("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                        f'<url><loc>{SITE_URL}/</loc>'
                        f'<lastmod>{built[:10]}</lastmod></url>\n</urlset>\n'),
    ):
        with open(os.path.join(SITE, name), "w") as f:
            f.write(content)

    build_card_png()

    if not TRANSFER_LINK or not HOTEL_LINK:
        missing = [n for n, v in (("TRANSFER_LINK", TRANSFER_LINK),
                                  ("HOTEL_LINK", HOTEL_LINK)) if not v]
        print(f"\n  ! {' and '.join(missing)} still empty — those sections show "
              f"advice but no\n    booking button, so they earn nothing yet. "
              f"Fill them in at the top of build.py.")

    print(f"\n{'=' * 60}")
    print(f"  Built {s['routes_total']} destinations")
    print(f"  LIKE-FOR-LIKE (same departure date, the headline):")
    print(f"    cheaper on {s['pairs_cheaper']} of {s['pairs_total']} comparisons"
          f"  ({s['routes_matched']} routes had a shared date)")
    print(f"    typical ${s['median_saving']} · best ${s['max_saving']} · "
          f"DJT won {s['pairs_dearer']} (worst ${s['worst_saving']})")
    print(f"  {DIM_}any-date, context only: cheaper on {s['routes_cheaper']}/"
          f"{s['routes_compared']} routes, median ${s['anydate_median']}{RST_}")
    print(f"{'=' * 60}")
    print(f"\n  open {os.path.join(SITE, 'index.html')}\n")


if __name__ == "__main__":
    main()
