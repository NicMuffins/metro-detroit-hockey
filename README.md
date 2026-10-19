# Metro Detroit Hockey Sessions

Daily iCal feed of **stick & puck** and **drop-in hockey** sessions at rinks within about an hour of Ann Arbor, MI. Rebuilt every morning by GitHub Actions and published to GitHub Pages.

## Subscribe

**Apple Calendar:** File → New Calendar Subscription → paste the URL below → set auto-refresh to **Every Day**.
**Google Calendar:** Settings → Other calendars → **+** → From URL → paste the URL below.

```
https://NicMuffins.github.io/metro-detroit-hockey/hockey_sessions.ics
```

Each event is titled like `🏒 Stick & Puck – Novi` or `🏒 Drop-In – Chelsea`. The notes show the session's own name, price (when the rink lists one), rink distance, and a link to the rink's schedule.

## Rinks covered (27)

| Rink | Distance | Source |
|------|----------|--------|
| Biggby Ice Cube – Ann Arbor | ~3 mi | Bond Sports API |
| Biggby Ice Cube – Chelsea | ~16 mi | Bond Sports API |
| USA Hockey Arena – Plymouth | ~22 mi | Weekly schedule (rink site) |
| Biggby Ice Cube – Brighton | ~22 mi | Bond Sports API |
| Novi Ice Arena | ~26 mi | ice-finder.com |
| Arctic Edge Ice Arena – Canton | ~27 mi | iCal feed |
| Eddie Edgar Ice Arena – Livonia | ~27 mi | Arena calendar |
| Mike Modano Ice Arena – Westland | ~27 mi | iCal feed |
| Taylor Sportsplex | ~30 mi | DaySmart API |
| Suburban Ice – Farmington Hills | ~32 mi | ice-finder.com |
| Hartland Ice House | ~33 mi | FinnlyConnect |
| Garden City Civic Arena | ~34 mi | ice-finder.com |
| Southgate Civic Arena | ~35 mi | DaySmart API |
| Frank J. Lada Civic Arena – Allen Park | ~37 mi | City calendar |
| Yack Arena – Wyandotte | ~37 mi | Weekly schedule (rink site) |
| Jackson Optimist Ice Arena – Jackson | ~38 mi | Monthly image (Claude vision) |
| Kennedy Recreation Center – Trenton | ~38 mi | DaySmart API |
| Southfield Sports Arena | ~40 mi | Weekly schedule (rink site) |
| Hazel Park Ice Arena | ~47 mi | Bond Sports API |
| St Clair Shores Civic Arena | ~50 mi | Monthly PDF |
| John Lindell Ice Arena – Royal Oak | ~54 mi | iCal feed |
| Tam-O-Shanter Ice Rink – Sylvania, OH | ~58 mi | DaySmart API |
| Buffalo Wild Wings Arena – Troy | ~58 mi | ice-finder.com |
| Suburban Ice Rochester | ~60 mi | ice-finder.com |
| Big Boy Arena – Fraser | ~60 mi | EZFacility |
| Biggby Ice Cube – East Lansing | ~62 mi | ice-finder.com |
| Suburban Ice Macomb | ~65 mi | ice-finder.com |

Notes:
- **ice-finder.com** rinks only show about 7 days ahead; everything else covers the full 14 days.
- **Weekly schedule** rinks (USA Hockey Arena, Wyandotte, Southfield) don't publish a calendar, so the feed uses the regular weekly time from the rink's site. Holidays and one-off cancellations won't show — confirm before you go. USA Hockey Arena and Wyandotte are skipped automatically if their page stops mentioning the session. Southfield's times come from the city's Fall 2026 guide (through late December) and need updating when the winter guide comes out.
- **Arctic Edge – Canton** is wired to its Sticks & Pucks calendar feed, which is empty right now. Sessions will show up on their own when the rink posts them.
- **Youth-only sessions are filtered out** (e.g. Tam-O-Shanter's ages 5–13 S&P, Trenton's age-banded S&P, "Youth Sticks & Pucks"). So are private, high-school, figure-skating and parent/tot sessions. **Senior** (Trenton) and **women's** (Southgate) drop-ins are included and labeled.

### Checked but not scraping

| Rink | Why |
|------|-----|
| Veterans Memorial Ice Arena – Ann Arbor | Closed for the 2026–27 season after tornado damage |
| Plymouth Cultural Center | Fall 2026 schedule is open skate and figure freestyle only — no hockey sessions |
| Birmingham Ice Sports Arena | Open skate, adult skate and youth/parent hockey only — no S&P or drop-in |
| Brownstown Sports Center | No ice programs published online |
| Lakeland Arena – Waterford | Runs Stick & Puck ($12) but the schedule isn't published anywhere machine-readable |
| Oak Park Ice Arena | Drop-in page says "No availability at this time" |
| Beech Tot Lot – Redford | Township facility calendar is empty |
| Farmington Hills Ice Arena (city rink) | Public skate only |
| Dearborn Ice Skating Center | Public skate only |
| Mount Clemens Ice Arena | Public skate and freestyle only |
| Crystal Fieldhouse | Has S&P and 50+ drop-in, but outside the 1-hour radius |
| Clark Park – Detroit | Outdoor and seasonal; online listings show youth hockey and open skate only |

## How it works

- `rinks.json` is the single list of rinks: one entry per rink with its source system and settings, plus the shared include/exclude filters.
- `scraper.py` goes through each active rink, pulls the next 14 days from that rink's own scheduling system (APIs and iCal feeds where possible, headless Chrome only where a site needs it), keeps the stick & puck / drop-in sessions, and writes `hockey_sessions.ics`.
- It also writes **`rink_status.json`**, a per-rink health report: how many sessions each rink produced and any error. **Check this first if a rink seems to be missing.** A rink with 0 sessions and no error just has nothing posted; one with an error needs a look.
- GitHub Actions runs it every morning (`.github/workflows/generate.yml`) and commits both files. GitHub Pages serves the `.ics`.
- Jackson's schedule is a monthly JPEG read with the Claude API. It needs the `ANTHROPIC_API_KEY` repo secret; without it, Jackson is skipped and the error shows in `rink_status.json`.

## Running locally

```bash
pip install -r requirements.txt
python -m pytest -q                       # offline parser tests
python scraper.py --days 14 --output hockey_sessions.ics
python scraper.py --only novi,westland    # just a few rinks
```

## Adding a rink

1. Find what the rink uses for its schedule: Bond Sports, DaySmart, a public Google/CivicPlus/SportsEngine calendar (`ics`), EZFacility, FinnlyConnect, or ice-finder.com.
2. Add an entry to `rinks.json` with that `source` and its settings (copy a rink that uses the same system).
3. Run `python scraper.py --only <new-id>` and check the sessions it prints.
