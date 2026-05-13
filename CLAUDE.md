# Metro Detroit Hockey Session Finder

## What this project does
Finds stick & puck and drop-in hockey sessions at 17 rinks near Ann Arbor, MI.
Sorted by distance from Ann Arbor (48108).

## Folder structure
- Project files: `~/Desktop/Claude CoWork/Projects/metro-detroit-hockey/`
- Output files:  `~/Desktop/Claude CoWork/Claude Outputs/metro-detroit-hockey/`

## How to run a session lookup
```bash
cd ~/Desktop/Claude\ CoWork/Projects/metro-detroit-hockey
python scraper.py --days 1 --output ~/Desktop/Claude\ CoWork/Claude\ Outputs/metro-detroit-hockey/today.ics
```

For a specific date range:
```bash
python scraper.py --days 7 --output ~/Desktop/Claude\ CoWork/Claude\ Outputs/metro-detroit-hockey/this_week.ics
```

## Install dependencies (run once before first use)
```bash
pip install selenium webdriver-manager requests pdfplumber
```
Chrome must be installed on this machine.

## Rinks covered (17 total)

### Bond Sports (browser scrape)
- Biggby Ice Cube – Ann Arbor (~3 mi, $15)
- Biggby Ice Cube – Chelsea (~16 mi, $15)
- Biggby Ice Cube – Brighton (~22 mi, $15)

### ice-finder.com (browser scrape)
- Novi Ice Arena (~26 mi, $15)
- Arctic Edge Ice Arena – Canton (~27 mi)
- Suburban Ice – Farmington Hills (~32 mi, $15)
- Garden City Civic Arena (~34 mi, $12)
- Hazel Park Viking Arena (~47 mi)
- John Lindell – Royal Oak (~54 mi)
- Buffalo Wild Wings Arena – Troy (~58 mi, $15)
- Suburban Ice Rochester (~60 mi, $15)
- Suburban Ice Macomb (~65 mi, $15)

### Direct scrapes
- Frank J. Lada Civic Arena – Allen Park (~37 mi) — CivicPlus calendar
- Eddie Edgar Ice Arena – Dearborn (~38 mi) — FullCalendar, Tuesdays only
- Jackson Optimist Ice Arena – Jackson MI (~38 mi, $10) — vision OCR

### DaySmart API
- Tam-O-Shanter Ice Rink – Sylvania, OH (~58 mi, $10) — Tuesdays 12–1:50pm

### PDF calendar
- St Clair Shores Civic Arena (~50 mi, $10) — monthly PDF

## GitHub repo
https://github.com/NicMuffins/metro-detroit-hockey

The GitHub Actions workflow runs scraper.py daily at 7am ET and publishes
hockey_sessions.ics to GitHub Pages for Apple Calendar subscription:
https://nicmuffins.github.io/metro-detroit-hockey/hockey_sessions.ics

## Common CoWork tasks

### Show me today's sessions
Run the scraper for 1 day, write output to Claude Outputs, then display results
as a formatted table in a markdown file in Claude Outputs.

### Show me sessions for a specific date
```bash
python scraper.py --days 1 --output ~/Desktop/Claude\ CoWork/Claude\ Outputs/metro-detroit-hockey/sessions.ics
```
Then parse the .ics file and display as a table sorted by time.

### Add a new rink
1. Identify the rink's scheduling system (Bond Sports / ice-finder / SportsEngine / DaySmart / CivicPlus)
2. Add it to the appropriate list in scraper.py
3. Test with: python scraper.py --days 3
4. Push the updated scraper.py to GitHub:
```bash
cd ~/Desktop/Claude\ CoWork/Projects/metro-detroit-hockey
git add scraper.py
git commit -m "Add new rink"
git push
```

### Push changes to GitHub
```bash
cd ~/Desktop/Claude\ CoWork/Projects/metro-detroit-hockey
git add -A
git commit -m "Your message here"
git push
```

## Environment variables needed
- ANTHROPIC_API_KEY — for Jackson Optimist vision OCR (optional, skips gracefully if not set)

## Notes
- Allen Park ice closes for season after May 9 each year
- Plymouth Cultural Center closes May 9 each year
- USA Hockey Arena under renovation as of March 2026
- GitHub Actions runs automatically at 7am ET daily — no local action needed for iCal feed
- Big Boy Arena (Fraser) not yet cracked — EZFacility session cookie issue
