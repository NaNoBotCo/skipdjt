# Skip DJT

A fare-comparison page for people who'd rather not fly out of President Donald J.
Trump International Airport (PBI/DJT) in Palm Beach. Compares cheapest cached
fares against Fort Lauderdale (FLL, 50 mi) and Miami (MIA, 70 mi).

**Launcher:** `~/Desktop/Skip DJT.command` — build, preview, or rebuild offline.

## How it works

`build.py` queries the Travelpayouts / Aviasales Data API and writes plain static
files to `docs/`. Nothing runs on the server; the API token never leaves your
machine. Host `docs/` anywhere.

```
python3 build.py             # fetch fresh fares + rebuild everything
python3 build.py --offline   # rebuild HTML from last data.json, no API calls
```

### Output

| File | Purpose |
|---|---|
| `docs/index.html` | the page |
| `docs/data.json` | same facts, machine-readable — agents read this |
| `docs/llms.txt` | LLM crawler entry point (matches the wichaa convention) |
| `docs/robots.txt` | explicitly allows GPTBot, ClaudeBot, PerplexityBot, CCBot |
| `docs/card.svg` | social share card |
| `docs/sitemap.xml` | sitemap |

## Money

Every booking link carries `marker=749581` (Travelpayouts affiliate ID) and is
tagged `rel="sponsored nofollow"`. **Without the marker the links earn nothing** —
the API returns them unattributed, so `book_url()` appends it. Ko-fi button points
at `ko-fi.com/defiantchiangmai`.

Aviasales pays only 1.1–1.3%, so flights are the hook, not the income. The higher-
value adjacent programs are already available in the same Travelpayouts account:

| Program | Rate | Why it fits |
|---|---|---|
| Kiwitaxi | 9–11% | ride to FLL/MIA — the problem avoidance creates |
| intui.travel | 10% | same |
| Welcome Pickups | 8–9% | same |
| Vio.com | 40–64% rev share | night before an early departure |
| Agoda | 6% | same (Booking.com's one-session cookie is near-worthless) |
| CheapOair | fixed $5–25 | out-earns Aviasales on most domestic fares |

Not yet wired — needs per-program approval in the dashboard first.

## Honesty rules (deliberate, don't quietly relax them)

Measured across 24 routes, skipping DJT was cheaper on **11 of 11** comparable
ones. That direction is solid and has a structural cause: DJT hosts no low-cost
carriers, FLL is a Spirit/JetBlue hub, MIA a major international one.

Two things inflate the raw dollar figures, so the page states both:

1. **Dates vary.** Cached fares are sparse, so a DJT price may sit beside an
   alternative from a different date. Every price prints its own departure date.
2. **DJT has less data.** The minimum of a small sample runs higher than the
   minimum of a large one even from identical prices.

So the page claims the *direction*, never an average saving as a promise. If you
ever want to advertise a specific dollar figure, build the same-date comparison
first (`departure_at=YYYY-MM-DD` for all three airports) — fewer routes survive,
but every number is real.

## Deployed

**Live: https://nanobotco.github.io/skipdjt/**
Repo `NaNoBotCo/skipdjt`, Pages serving `main` branch `/docs`.
To update: `python3 build.py` then commit and push — Pages redeploys itself.

### Other hosts

`docs/` is fully static. Any of these work:

- **GitHub Pages** — push `docs/` as the Pages directory
- **Netlify / Cloudflare Pages** — drag the `docs/` folder onto the dashboard
- **Any host** — it's just files

Then set `SITE_URL` at the top of `build.py` to the real domain and rebuild, so
canonical URLs, share links, sitemap, and `llms.txt` all point at the right place.

## Weekly auto-refresh

`.github/workflows/weekly-rebuild.yml` rebuilds from fresh fares every Monday
09:17 UTC and pushes, so Pages redeploys itself. Runs in the cloud deliberately —
a local cron only fires when the laptop is open.

It **refuses to publish** unless the build passes a sanity check: route count,
card/route parity, enough attributed links, **zero unattributed booking links**,
and the method note still present. A partial API failure fails the job instead of
overwriting a good site with a degraded one.

The token lives in two places: `.tp_token` locally and GitHub Secrets as
`TP_TOKEN`. Changing one without the other makes the Monday job fail silently.

**Use `~/Desktop/Rotate Skip DJT Token.command`** — it opens the token page,
verifies the new token against the API *before* changing anything, updates both
locations, rebuilds, and rolls back if the rebuild fails. Manual equivalent:

```
gh secret set TP_TOKEN --repo NaNoBotCo/skipdjt --body "<new token>"
```

The token is read-only price access. It cannot reach the account, payouts, or
money — the marker earns, and the marker is public by design. Rotation is
hygiene, not urgency.

Run it by hand any time from the Actions tab, or `gh workflow run "Weekly fare refresh"`.

## Known gaps

- **Transfer and hotel links not wired yet.** Travelpayouts gates link
  generation behind having a Project, and a Project asks for the live URL —
  which now exists. Steps: dashboard → Create Project (use
  `https://nanobotco.github.io/skipdjt`) → join Kiwitaxi (program 1) and a
  hotel program → Create link → paste into `TRANSFER_LINK` / `HOTEL_LINK`
  at the top of `build.py` → rebuild. Until then those sections render
  advice but no booking button, and `build.py` warns you on every run.
- Drive times are estimates, not from a maps API.
- IATA code flips PBI → DJT on 2026-08-18; both are queried and merged, so no
  change needed, but worth re-checking coverage after that date.

## Social card

`docs/card.png` (1200x630) is rendered from `docs/card.html` by headless
Chrome during every build, then downsampled from 2x for crisp text. SVG cards
were dropped because X and Facebook frequently refuse to render them.
