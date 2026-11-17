# Metro Detroit Hockey Session Finder

## What this project does
Builds a daily iCal feed of stick & puck and drop-in hockey sessions at 33 rinks within about an hour of Ann Arbor, MI (48108), published at:
https://nicmuffins.github.io/metro-detroit-hockey/hockey_sessions.ics

## Folder structure
- Project files: `~/Desktop/Claude CoWork/Projects/metro-detroit-hockey/`
- Output files:  `~/Desktop/Claude CoWork/Claude Outputs/metro-detroit-hockey/`

## Key files
- `rinks.json` — THE rink list: one entry per rink (source system + settings), the default include/exclude regexes, and `not_scraping` (rinks checked and skipped, with reasons and dates).
- `scraper.py` — reads rinks.json, scrapes each rink, writes `hockey_sessions.ics` and `rink_status.json`.
- `rink_status.json` — per-rink health from the latest run (session count + error). Start here when auditing.
- `tests/test_parsers.py` — offline parser tests using samples captured from the real sites.
- `.github/workflows/generate.yml` — daily GitHub Actions run (7am ET; GitHub often starts it late).

## Source types (rinks.json `source`)
| source | used by | notes |
|---|---|---|
| bond | Biggby Ann Arbor/Chelsea/Brighton, Hazel Park | `api.bondsports.co/v4/facilities/{facility_id}/programs-schedule` |
| daysmart | Taylor (tsx), Trenton, Southgate, Tam-O-Shanter, Ottawa Park | `api.daysmartrecreation.com/v1/events`, paginated, names from `desc` or `homeTeam`; `resource_ids` splits rinks that share one account (Tam-O-Shanter / Ottawa Park) |
| ics | Royal Oak (Google Cal), Westland (CivicPlus), Canton (SportsEngine tag feed) | any public iCal URL |
| ice_finder | Novi, Suburban Ice x3, Garden City, Biggby East Lansing | ~7 days ahead only |
| frontline | Crystal Fieldhouse, Troy (BWW Arena) | `monthlysched.cfm?fac=..&session=N&month=M`, one page per session type |
| allstar | The Barn (Fenton) | multipart POST to allstarrecmanager.com/functions.php (`change-event-view`) |
| wix_events | 810 Sports Complex (Flint) | `"scheduling"` blocks embedded in the homepage HTML |
| ezfacility | Big Boy Arena (Fraser) | POST /Sessions/FilterResults, no login needed |
| finnly | Hartland Ice House, Yost | schedule JSON embedded in the page (`_onlineScheduleList`) |
| static_weekly | Buhr Park, USA Hockey Arena, Wyandotte, Southfield | fixed weekly times; `verify_text` (regex list, include the listed times) must match the rink page or the rink errors out |
| jackson_vision | Jackson Optimist | monthly JPEG read with the Claude API (needs ANTHROPIC_API_KEY) |
| scs_pdf | St Clair Shores | monthly PDF grid; scanned (image-only) PDFs are read with the Claude API |
| allen_park / eddie_edgar | Allen Park, Eddie Edgar (Livonia) | headless Chrome |

## Common tasks

### Show me today's / this week's sessions
```bash
cd ~/Desktop/Claude\ CoWork/Projects/metro-detroit-hockey
python scraper.py --days 1 --output ~/Desktop/Claude\ CoWork/Claude\ Outputs/metro-detroit-hockey/today.ics --status /tmp/status.json
python scraper.py --days 7 --output ~/Desktop/Claude\ CoWork/Claude\ Outputs/metro-detroit-hockey/this_week.ics --status /tmp/status.json
```
Then show the sessions as a table sorted by time.

### Audit the feed
Read `rink_status.json` from the repo (latest Actions run). Any rink with an `error` needs a look; 0 sessions with no error usually just means nothing is posted.

### Add a rink
1. Identify its scheduling system (see the source table above).
2. Add an entry to `rinks.json` (copy a rink with the same source).
3. `python scraper.py --only <id>` and check the output.
4. `python -m pytest -q`, then commit and push.

## Install dependencies
```bash
pip install -r requirements.txt
```
Chrome must be installed (only needed for ice-finder fallback, Allen Park and Eddie Edgar).

## Seasonal notes
- Southfield's weekly times come from the city's Fall 2026 guide (valid through late Dec 2026). Update from the winter guide in December.
- Canton isn't posting S&P as of Oct 2026; its feed will fill in automatically.
- Allen Park and Plymouth Cultural Center ice close in early May; Ann Arbor Vets is closed for the 2026–27 season.
- Buhr Park is an outdoor rink: season dates in rinks.json run to mid-March 2027 — adjust when the city posts closing.
- Youth-only, private, HS, figure and parent/tot sessions are filtered by the default `exclude` regex in rinks.json.
