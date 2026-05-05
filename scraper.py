#!/usr/bin/env python3
"""
Metro Detroit Hockey Session iCal Generator v2
===============================================
Uses Selenium (Chrome) to scrape three sources:

1. Bond Sports (3 rinks) — Biggby Ann Arbor, Chelsea, Brighton
   bondsports.co/org/{org}/{fac}/schedule — click dates, read text

2. ice-finder.com (9 rinks) — Novi, Canton, Farmington Hills,
   Garden City, Troy, Hazel Park, Royal Oak, Rochester, Macomb

3. Direct city calendar scrapes (2 rinks):
   - Allen Park (Frank J. Lada) — CivicPlus/Revize list view
     cityofallenpark.org/calendar.php?view=list&...
   - Eddie Edgar Ice Arena — Drupal FullCalendar list view
     eddieedgar.org/schedule (click List, read text)

Total: 14 rinks automated.

Setup:  pip install selenium webdriver-manager
Usage:  python hockey_ical_v2.py [--days 14] [--output hockey.ics] [--headless false]
"""

import sys, re, time, hashlib, argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOCKEY_RE = re.compile(
    r'stick\s*[&and]+\s*pucks?|drop.?in hockey|adult sticks|adult drop|open hockey|35\+\s*drop|pickup hockey',
    re.IGNORECASE
)

# ─── Rink definitions ─────────────────────────────────────────────────────────

BOND_RINKS = [
    {"name": "Biggby Ice Cube – Ann Arbor", "org": 246, "fac": 262, "dist": 3,  "price": "$15"},
    {"name": "Biggby Ice Cube – Chelsea",   "org": 474, "fac": 529, "dist": 16, "price": "$15"},
    {"name": "Biggby Ice Cube – Brighton",  "org": 353, "fac": 386, "dist": 22, "price": "$15"},
]

ICE_FINDER_RINKS = [
    {"name": "Novi Ice Arena",                   "slug": "novi-ice-arena",                  "dist": 26, "url": "https://www.noviicearena.com/public-schedule"},
    {"name": "Arctic Edge Ice Arena – Canton",   "slug": "arctic-edge-ice-arena-of-canton", "dist": 27, "url": "https://www.arcticarenas.com/page/show/3414762-calendar"},
    {"name": "Suburban Ice – Farmington Hills",  "slug": "suburban-ice-farmington-hills",   "dist": 32, "url": "https://www.suburbanicefarmingtonhills.com/public-skating"},
    {"name": "Garden City Civic Arena",          "slug": "garden-city-civic-arena",         "dist": 34, "url": "https://www.gardencitymi.org/calendar.aspx?CID=66,14"},
    {"name": "Buffalo Wild Wings Arena – Troy",  "slug": "buffalo-wild-wings-arena",        "dist": 58, "url": "https://troy.frontline-connect.com/"},
    {"name": "Hazel Park Viking Arena",          "slug": "hazel-park-ice-arena",            "dist": 47, "url": "https://www.hazelpark.org/residents/ice_arena.php"},
    {"name": "John Lindell – Royal Oak",         "slug": "john-lindell-ice-arena-royal-oak","dist": 54, "url": "https://www.royaloakicearena.com/public-skating"},
    {"name": "Suburban Ice Rochester",           "slug": "suburban-ice-rochester",          "dist": 60, "url": "https://www.suburbanicerochester.com/public-skating"},
    {"name": "Suburban Ice Macomb",              "slug": "suburban-ice-macomb",             "dist": 65, "url": "https://www.suburbanicemacomb.com/public-skating"},
]

# Direct-scrape rinks — custom per-site scraper logic
DIRECT_RINKS = [
    {
        "name": "Frank J. Lada Civic Arena – Allen Park",
        "dist": 37, "price": None,
        "url":  "https://cityofallenpark.org/calendar.php",
        "type": "civicplus_revize",
        # Note: ice closed for season after May 9 each year
    },
    {
        "name": "Eddie Edgar Ice Arena – Dearborn",
        "dist": 38, "price": None,
        "url":  "https://www.eddieedgar.org/schedule",
        "type": "fullcalendar_list",
        # Sticks & Pucks Tuesdays only, 11:45am-1:15pm, Edgar B arena
    },
]


# ─── Selenium setup ───────────────────────────────────────────────────────────

def make_driver(headless=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)


# ─── ice-finder scraper ───────────────────────────────────────────────────────

def parse_ice_finder_page(body_text: str, rink: dict, today, cutoff) -> list[dict]:
    """Parse sessions from ice-finder rink page text."""
    TIME_RE = re.compile(
        r'(\w+,\s+\w+\s+\d+)\s*[•·]\s*(\d+:\d+\s*[AP]M)\s*[–\-]\s*(\d+:\d+\s*[AP]M)',
        re.IGNORECASE
    )
    sessions = []
    # Split into event blocks by looking for event-type lines followed by date lines
    lines = body_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Detect hockey session types
        if re.search(r'stick\s*[&and]+\s*puck|pickup hockey|adult drop|drop.?in hockey', line, re.I):
            event_type = line
            # Look ahead for the date/time line
            for j in range(i+1, min(i+5, len(lines))):
                m = TIME_RE.search(lines[j])
                if m:
                    date_str, start_str, end_str = m.group(1), m.group(2), m.group(3)
                    try:
                        year = today.year
                        event_date = datetime.strptime(f"{date_str} {year}", "%a, %b %d %Y").date()
                        if event_date < today - timedelta(days=30):
                            event_date = datetime.strptime(f"{date_str} {year+1}", "%a, %b %d %Y").date()
                    except ValueError:
                        break
                    if not (today <= event_date <= cutoff):
                        break
                    # Get price
                    price = None
                    for k in range(j+1, min(j+4, len(lines))):
                        if '$' in lines[k]:
                            price = lines[k].strip()
                            break
                    sessions.append({
                        "name": event_type,
                        "date": event_date,
                        "start_raw": start_str.strip(),
                        "end_raw": end_str.strip(),
                        "location": None,
                        "rink": rink["name"],
                        "dist": rink["dist"],
                        "price": price,
                        "url": rink["url"],
                        "source": "ice-finder",
                    })
                    break
        i += 1

    # Deduplicate
    seen, unique = set(), []
    for s in sessions:
        key = (s["date"], s["start_raw"], s["rink"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def scrape_ice_finder_all(driver, days: int, today, cutoff) -> list[dict]:
    """Scrape all ice-finder rinks using the Selenium driver."""
    from selenium.webdriver.common.by import By
    all_sessions = []
    for rink in ICE_FINDER_RINKS:
        url = f"https://ice-finder.com/rinks/michigan/rink/{rink['slug']}"
        print(f"  Fetching {rink['name']}...", file=sys.stderr)
        try:
            driver.get(url)
            time.sleep(3)
            body = driver.find_element(By.TAG_NAME, "body").text
            sessions = parse_ice_finder_page(body, rink, today, cutoff)
            if sessions:
                print(f"    → {len(sessions)} session(s)", file=sys.stderr)
            all_sessions.extend(sessions)
        except Exception as e:
            print(f"    ✗ Error: {e}", file=sys.stderr)
    return all_sessions


# ─── Bond Sports scraper ──────────────────────────────────────────────────────

def scrape_bond_all(driver, days: int, today) -> list[dict]:
    """Scrape all Bond Sports rinks."""
    from selenium.webdriver.common.by import By
    TIME_RE = re.compile(r'(\d{1,2}:\d{2}\s*(?:am|pm))\s*[–\-]\s*(\d{1,2}:\d{2}\s*(?:am|pm))', re.I)
    BOND_HOCKEY_RE = re.compile(r'stick\s*[&and]+\s*pucks?|adult sticks|35\+\s*drop', re.I)
    dates = [today + timedelta(days=i) for i in range(days)]
    all_sessions = []

    for rink in BOND_RINKS:
        print(f"  Fetching {rink['name']}...", file=sys.stderr)
        url = f"https://bondsports.co/org/{rink['org']}/{rink['fac']}/schedule"
        driver.get(url)
        time.sleep(6)

        for date in dates:
            # Click date in calendar
            day_str = str(date.day)
            try:
                els = driver.find_elements(By.XPATH,
                    f"//*[normalize-space(text())='{day_str}']"
                    f"[not(contains(@class,'other')) and not(@disabled)]"
                )
                for el in els:
                    if el.is_displayed():
                        el.click()
                        time.sleep(2)
                        break
            except Exception:
                pass

            lines = [l.strip() for l in driver.find_element(By.TAG_NAME, "body").text.split('\n') if l.strip()]
            seen_times = set()
            for i, line in enumerate(lines):
                if not BOND_HOCKEY_RE.search(line):
                    continue
                start_raw = end_raw = None
                for j in range(max(0, i-5), i):
                    m = TIME_RE.search(lines[j])
                    if m:
                        start_raw, end_raw = m.group(1).strip(), m.group(2).strip()
                        break
                if not start_raw or start_raw in seen_times:
                    continue
                seen_times.add(start_raw)
                loc = next((lines[k] for k in range(i+1, min(i+4, len(lines)))
                           if re.search(r'rink|ice|room', lines[k], re.I) and len(lines[k]) < 60), None)
                all_sessions.append({
                    "name": line, "date": date,
                    "start_raw": start_raw, "end_raw": end_raw, "location": loc,
                    "rink": rink["name"], "dist": rink["dist"],
                    "price": rink["price"], "url": url, "source": "Bond Sports",
                })

        count = sum(1 for s in all_sessions if s["rink"] == rink["name"])
        if count:
            print(f"    → {count} session(s) over {days} days", file=sys.stderr)

    return all_sessions


# ─── iCal ─────────────────────────────────────────────────────────────────────

def parse_dt(date, time_str):
    time_str = re.sub(r'(\d)(AM|PM)', r'\1 \2', time_str.strip().upper())
    dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %I:%M %p")
    m = date.month
    return dt.replace(tzinfo=timezone(timedelta(hours=-5 if m in (11,12,1,2,3) else -4)))

def ical_dt(dt): return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
def esc(s): return s.replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n")
def uid(s): return hashlib.md5(f"{s['rink']}-{s['date']}-{s['start_raw']}-{s['name']}".encode()).hexdigest() + "@mdh"

def to_ical(sessions):
    now = datetime.now(timezone.utc)
    out = [
        "BEGIN:VCALENDAR","VERSION:2.0",
        "PRODID:-//Metro Detroit Hockey v2//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
        "X-WR-CALNAME:Metro Detroit Hockey Sessions",
        "X-WR-TIMEZONE:America/Detroit",
        f"X-WR-CALDESC:Stick & Puck / Drop-In near Ann Arbor MI. Updated {now.strftime('%Y-%m-%d %H:%M UTC')}.",
    ]
    for s in sorted(sessions, key=lambda x: (x["date"], x["dist"])):
        try:
            ds, de = parse_dt(s["date"], s["start_raw"]), parse_dt(s["date"], s["end_raw"])
        except:
            continue
        short = re.split(r'[–—]', s["rink"])[-1].strip()
        stype = "Stick & Puck" if re.search(r'stick|puck', s["name"], re.I) else "Drop-In/Pickup"
        desc = esc("\n".join(filter(None,[
            s["name"],"",f"Rink: {s['rink']}",f"~{s['dist']} mi from Ann Arbor",
            f"Price: {s['price']}" if s.get("price") else None,
            f"Ice: {s['location']}" if s.get("location") else None,
            f"Source: {s.get('source','')}","",
            f"Info: {s['url']}","Confirm with rink — schedule subject to change.",
        ])))
        out += [
            "BEGIN:VEVENT", f"UID:{uid(s)}", f"DTSTAMP:{ical_dt(now)}",
            f"DTSTART:{ical_dt(ds)}", f"DTEND:{ical_dt(de)}",
            f"SUMMARY:{esc(f'🏒 {stype} – {short}')}",
            f"DESCRIPTION:{desc}", f"LOCATION:{esc(s['rink'])}",
            f"URL:{s.get('url','')}", "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


# ─── Allen Park scraper (CivicPlus/Revize list view) ─────────────────────────

def scrape_allen_park(driver, dates: list) -> list[dict]:
    """
    Allen Park uses a CivicPlus/Revize calendar.
    URL: calendar.php?view=list&month=M&day=1&year=Y&display=31
    Returns the full month as plain text with date headers like:
    "May 4, 2026Monday9:00am - 10:00amClassical Stretching12:30pm - 3:30pmSticks and Pucks"
    """
    from selenium.webdriver.common.by import By
    rink = DIRECT_RINKS[0]
    sessions = []
    processed_months = set()
    HOCKEY_PAT = re.compile(r'sticks? and pucks?|stick.?puck|drop.?in hockey', re.I)
    TIME_PAT = re.compile(r'(\d{1,2}:\d{2}[ap]m)\s*-\s*(\d{1,2}:\d{2}[ap]m)', re.I)

    for date in dates:
        month_key = (date.year, date.month)
        if month_key in processed_months:
            continue
        processed_months.add(month_key)

        url = f"https://cityofallenpark.org/calendar.php?view=list&month={date.month}&day=1&year={date.year}&display=31"
        print(f"  Allen Park: fetching {date.year}-{date.month:02d}...", file=sys.stderr)
        try:
            driver.get(url)
            time.sleep(3)
            text = driver.find_element(By.TAG_NAME, "body").text
        except Exception as e:
            print(f"    error: {e}", file=sys.stderr)
            continue

        # Parse: text has date headers like "May 4, 2026Monday" then time+name lines
        # We look for date blocks and extract events within them
        # Pattern: find "Month D, YYYY" then time-name pairs until next date
        date_blocks = re.split(r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},\s+20\d{2})', text)
        current_date_str = None
        for chunk in date_blocks:
            # Check if this chunk is a date header
            dm = re.match(r'(\w+ \d{1,2}, 20\d{2})', chunk)
            if dm:
                current_date_str = dm.group(1)
                continue
            if not current_date_str:
                continue
            # Parse the date
            try:
                event_date = datetime.strptime(current_date_str, "%B %d, %Y").date()
            except ValueError:
                try:
                    event_date = datetime.strptime(current_date_str, "%b %d, %Y").date()
                except ValueError:
                    continue
            if event_date not in dates:
                continue
            # Find hockey sessions in this chunk
            lines = [l.strip() for l in chunk.split("\n") if l.strip()]
            for i, line in enumerate(lines):
                if not HOCKEY_PAT.search(line):
                    continue
                # Time is usually in the same line or previous line
                time_str = None
                for j in range(max(0, i-1), min(i+2, len(lines))):
                    tm = TIME_PAT.search(lines[j])
                    if tm:
                        time_str = lines[j]
                        start_raw = tm.group(1).upper().replace(" ", "")
                        end_raw   = tm.group(2).upper().replace(" ", "")
                        # Normalize: "12:30PM" -> "12:30 PM"
                        start_raw = re.sub(r'(\d)(AM|PM)', r'\1 \2', start_raw)
                        end_raw   = re.sub(r'(\d)(AM|PM)', r'\1 \2', end_raw)
                        break
                if not time_str:
                    # Try extracting time from the same line
                    tm = TIME_PAT.search(line)
                    if tm:
                        start_raw = re.sub(r'(\d)(AM|PM)', r'\1 \2', tm.group(1).upper())
                        end_raw   = re.sub(r'(\d)(AM|PM)', r'\1 \2', tm.group(2).upper())
                    else:
                        continue
                sessions.append({
                    "name": "Sticks and Pucks",
                    "date": event_date,
                    "start_raw": start_raw,
                    "end_raw":   end_raw,
                    "location":  None,
                    "rink":      rink["name"],
                    "dist":      rink["dist"],
                    "price":     rink["price"],
                    "url":       rink["url"],
                    "source":    "Allen Park calendar",
                })

    if sessions:
        print(f"    → {len(sessions)} Allen Park session(s)", file=sys.stderr)
    return sessions


# ─── Eddie Edgar scraper (Drupal FullCalendar list view) ──────────────────────

def scrape_eddie_edgar(driver, dates: list) -> list[dict]:
    """
    Eddie Edgar uses a Drupal FullCalendar.
    Navigate to /schedule, click the List view button, read all events.
    Sticks & Pucks only appears on Tuesdays: 11:45am–1:15pm, Edgar B arena.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    rink = DIRECT_RINKS[1]

    # Only scrape if there are Tuesdays in our date range
    tuesday_dates = [d for d in dates if d.weekday() == 1]
    if not tuesday_dates:
        return []

    print(f"  Eddie Edgar: fetching schedule (Tuesdays: {tuesday_dates})...", file=sys.stderr)
    sessions = []
    HOCKEY_PAT = re.compile(r'stick|puck', re.I)

    try:
        driver.get("https://www.eddieedgar.org/schedule")
        time.sleep(5)
        # Click List view button
        list_btns = driver.find_elements(By.XPATH, "//button[contains(@class,'fc-listMonth') or contains(text(),'list') or @data-view='list']")
        if not list_btns:
            list_btns = driver.find_elements(By.XPATH, "//*[contains(@class,'fc-button') and contains(translate(text(),'LIST','list'),'list')]")
        if list_btns:
            list_btns[0].click()
            time.sleep(2)
        text = driver.find_element(By.TAG_NAME, "body").text
    except Exception as e:
        print(f"    error: {e}", file=sys.stderr)
        return []

    # Parse the list view text
    # Format: "May 2026 ... May 5, 2026 Tuesday ... 11:45 am (Sticks and Pucks) @ Edgar B arena..."
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    current_date = None
    DATE_PAT = re.compile(r'(\w+ \d{1,2}, 20\d{2})')
    TIME_PAT  = re.compile(r'(\d{1,2}:\d{2}\s*(?:am|pm))\s*[-–]\s*(\d{1,2}:\d{2}\s*(?:am|pm))', re.I)
    # Also match inline format: "11:45 am (Sticks and Pucks) @ Edgar B arena"
    INLINE_PAT = re.compile(r'(\d{1,2}:\d{2}\s*(?:am|pm))\s*\(([^)]+)\)', re.I)

    for line in lines:
        dm = DATE_PAT.search(line)
        if dm and not re.search(r'\d{1,2}:\d{2}', line):
            try:
                current_date = datetime.strptime(dm.group(1), "%B %d, %Y").date()
            except ValueError:
                pass
            continue
        if current_date not in dates:
            continue
        # Match inline event format from Eddie Edgar's list view
        im = INLINE_PAT.search(line)
        if im and HOCKEY_PAT.search(im.group(2)):
            start_t = im.group(1).strip().upper()
            start_t = re.sub(r'(\d)(AM|PM)', r'\1 \2', start_t)
            # Eddie Edgar S&P is always 11:45am-1:15pm
            sessions.append({
                "name":      "Sticks and Pucks",
                "date":      current_date,
                "start_raw": start_t,
                "end_raw":   "1:15 PM",
                "location":  "Edgar B arena",
                "rink":      rink["name"],
                "dist":      rink["dist"],
                "price":     rink["price"],
                "url":       rink["url"],
                "source":    "Eddie Edgar calendar",
            })

    if sessions:
        print(f"    → {len(sessions)} Eddie Edgar session(s)", file=sys.stderr)
    return sessions



# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--output", default="hockey_sessions.ics")
    p.add_argument("--headless", default="true")
    args = p.parse_args()
    headless = args.headless.lower() != "false"
    today = datetime.now().date()
    cutoff = today + timedelta(days=args.days)

    print(f"Metro Detroit Hockey iCal v2 — {args.days} days", file=sys.stderr)
    print(f"From {today} through {cutoff}\n", file=sys.stderr)

    try:
        driver = make_driver(headless)
    except Exception as e:
        print(f"Could not start browser: {e}", file=sys.stderr)
        print("Run: pip install selenium webdriver-manager", file=sys.stderr)
        sys.exit(1)

    all_sessions = []
    try:
        print("── ice-finder.com ──────────────────────────────────────────", file=sys.stderr)
        all_sessions += scrape_ice_finder_all(driver, args.days, today, cutoff)

        print("\n── Bond Sports ─────────────────────────────────────────────", file=sys.stderr)
        all_sessions += scrape_bond_all(driver, args.days, today)

        print("\n── Direct scrapes (Allen Park, Eddie Edgar) ─────────────────", file=sys.stderr)
        date_list = [today + timedelta(days=i) for i in range(args.days)]
        all_sessions += scrape_allen_park(driver, date_list)
        all_sessions += scrape_eddie_edgar(driver, date_list)
    finally:
        driver.quit()

    print(f"\nTotal: {len(all_sessions)} session(s)\n", file=sys.stderr)
    for s in sorted(all_sessions, key=lambda x: (x["date"], x["dist"])):
        print(f"  {s['date']}  {s['start_raw']:>10} – {s['end_raw']:<10}  ~{s['dist']:>2}mi  {s['rink']}", file=sys.stderr)

    Path(args.output).write_text(to_ical(all_sessions), encoding="utf-8")
    print(f"\n✓ Written to {args.output}", file=sys.stderr)

if __name__ == "__main__":
    main()
