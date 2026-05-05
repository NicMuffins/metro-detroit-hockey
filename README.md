# Metro Detroit Hockey Sessions

Daily iCal feed for stick & puck and drop-in hockey sessions near Ann Arbor, MI. Updated every morning at 7am ET.

## Subscribe in Apple Calendar

1. Open Apple Calendar
2. File → New Calendar Subscription
3. Paste this URL: `https://NicMuffins.github.io/metro-detroit-hockey/hockey_sessions.ics`
4. Set auto-refresh to **Every Day**

## Subscribe in Google Calendar

1. Open Google Calendar → Settings
2. Other calendars → **+** → From URL
3. Paste the URL above

## Rinks covered (14 total)

| Rink | Distance | Source |
|------|----------|--------|
| Biggby Ice Cube – Ann Arbor | ~3 mi | Bond Sports |
| Biggby Ice Cube – Chelsea | ~16 mi | Bond Sports |
| Biggby Ice Cube – Brighton | ~22 mi | Bond Sports |
| Novi Ice Arena | ~26 mi | ice-finder.com |
| Arctic Edge – Canton | ~27 mi | ice-finder.com |
| Suburban Ice – Farmington Hills | ~32 mi | ice-finder.com |
| Garden City Civic Arena | ~34 mi | ice-finder.com |
| Frank J. Lada Civic Arena – Allen Park | ~37 mi | City calendar |
| Eddie Edgar Ice Arena – Dearborn | ~38 mi | City calendar |
| Hazel Park Viking Arena | ~47 mi | ice-finder.com |
| John Lindell – Royal Oak | ~54 mi | ice-finder.com |
| Buffalo Wild Wings Arena – Troy | ~58 mi | ice-finder.com |
| Suburban Ice Rochester | ~60 mi | ice-finder.com |
| Suburban Ice Macomb | ~65 mi | ice-finder.com |

## How it works

A GitHub Actions workflow runs `scraper.py` every morning at 7am ET. It uses a headless Chrome browser to load each rink's schedule page, extract hockey sessions for the next 14 days, and write them to `hockey_sessions.ics`. That file is then published to GitHub Pages where your calendar app can subscribe to it.

## Running locally

```bash
pip install -r requirements.txt
python scraper.py --days 14 --output hockey_sessions.ics
```
