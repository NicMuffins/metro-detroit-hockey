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

# SportsEngine rinks — scraped via /event/show_month_list/ plain text
SPORTNGIN_RINKS = [
    {
        "name": "Arctic Edge Ice Arena – Canton",
        "dist": 27, "price": None,
        "url":  "https://www.arcticarenas.com/page/show/3414762-calendar",
        "month_list_url": "https://www.arcticarenas.com/event/show_month_list/3414762",
        # Sticks & Pucks tag: 3517364, Open Skate tag: 3517362
    },
]


# DaySmart Recreation (DASH) rinks — REST API, no auth needed
DAYSMART_RINKS = [
    {
        "name": "Tam-O-Shanter Ice Rink – Sylvania, OH",
        "company": "tamoshanter",
        "dist": 58, "price": "$10",
        "url": "https://apps.daysmartrecreation.com/dash/x/#/online/tamoshanter/event-registration",
    },
]

# Image-schedule rinks — schedule is a monthly JPEG; parsed via Claude vision
IMAGE_SCHEDULE_RINKS = [
    {
        "name": "Jackson Optimist Ice Arena – Jackson, MI",
        "dist": 38, "price": "$10",
        "url": "https://www.optimisticearena.com/stick-and-puck/",
        "type": "wordpress_image",
    },
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


# ─── DaySmart Recreation (DASH) scraper ────────────────────────────────────────

def scrape_daysmart(driver, dates: list) -> list[dict]:
    """
    DaySmart REST API — no auth required.
    GET https://api.daysmartrecreation.com/v1/events
    Required header: Accept: application/vnd.api+json
    Key params: company, filter[start_date__gte], filter[start_date__lte],
                filter[unconstrained]=1, page[size]=100
    Event name lives in attributes.best_description.
    """
    from selenium.webdriver.common.by import By
    import json as _json
    HOCKEY_PAT = re.compile(r'hockey|drop.?in|stick.*puck', re.I)
    all_sessions = []

    if not dates:
        return []

    # Batch the full date range in one API call
    start_str = min(dates).strftime('%Y-%m-%d')
    end_str   = max(dates).strftime('%Y-%m-%d')

    for rink in DAYSMART_RINKS:
        print(f"  {rink['name']}: fetching {start_str} – {end_str}...", file=sys.stderr)
        try:
            # Use JavaScript fetch inside the browser (already on the domain)
            # Navigate to the rink's page first to get same-origin context
            driver.get(f"{rink['url']}?date={start_str}&")
            time.sleep(5)

            script = f"""
return (async () => {{
  const p = new URLSearchParams();
  p.set('page[size]', '100');
  p.set('sort', 'start');
  p.set('company', '{rink["company"]}');
  p.set('filter[unconstrained]', '1');
  p.set('filter[start_date__gte]', '{start_str}');
  p.set('filter[start_date__lte]', '{end_str}');
  const r = await fetch('https://api.daysmartrecreation.com/v1/events?' + p.toString(), {{
    headers: {{'Accept': 'application/vnd.api+json'}}
  }});
  const d = await r.json();
  return JSON.stringify(d);
}})();
"""
            result = driver.execute_async_script("""
const callback = arguments[arguments.length - 1];
(async () => {
  const p = new URLSearchParams();
  p.set('page[size]', '100');
  p.set('sort', 'start');
  p.set('company', '""" + rink["company"] + """');
  p.set('filter[unconstrained]', '1');
  p.set('filter[start_date__gte]', '""" + start_str + """');
  p.set('filter[start_date__lte]', '""" + end_str + """');
  try {
    const r = await fetch('https://api.daysmartrecreation.com/v1/events?' + p.toString(), {
      headers: {'Accept': 'application/vnd.api+json'}
    });
    const d = await r.json();
    callback(JSON.stringify(d));
  } catch(e) {
    callback('ERROR:' + e.message);
  }
})();
""")
            if not result or result.startswith('ERROR'):
                print(f"    API error: {result}", file=sys.stderr)
                continue

            data = _json.loads(result)
            events = data.get('data', [])

            for e in events:
                attrs = e.get('attributes', {})
                name = attrs.get('best_description', '') or ''
                if not HOCKEY_PAT.search(name):
                    continue
                start_raw = attrs.get('start', '')  # ISO: "2026-05-12T12:00:00"
                end_raw   = attrs.get('end', '')
                if not start_raw:
                    continue
                try:
                    dt_start = datetime.fromisoformat(start_raw)
                    dt_end   = datetime.fromisoformat(end_raw) if end_raw else None
                    event_date = dt_start.date()
                except ValueError:
                    continue
                if event_date not in dates:
                    continue

                start_fmt = dt_start.strftime('%-I:%M %p')
                end_fmt   = dt_end.strftime('%-I:%M %p') if dt_end else ''

                all_sessions.append({
                    "name":      name,
                    "date":      event_date,
                    "start_raw": start_fmt,
                    "end_raw":   end_fmt,
                    "location":  None,
                    "rink":      rink["name"],
                    "dist":      rink["dist"],
                    "price":     rink.get("price"),
                    "url":       rink["url"],
                    "source":    "DaySmart",
                })

        except Exception as e:
            print(f"    error: {e}", file=sys.stderr)

    if all_sessions:
        print(f"    → {len(all_sessions)} DaySmart session(s)", file=sys.stderr)
    return all_sessions


# ─── Jackson Optimist scraper (WordPress image calendar via Claude vision) ──────

def scrape_jackson_optimist(driver, dates: list) -> list[dict]:
    """
    Jackson Optimist posts a monthly JPEG image of their schedule to WordPress.
    Strategy:
      1. Load the page, find the current month's calendar image URL
      2. Download the image
      3. Use the Anthropic API (claude vision) to OCR the schedule
      4. Parse the returned sessions
    Falls back gracefully if the image can't be found or parsed.
    """
    from selenium.webdriver.common.by import By
    import base64
    import json as _json
    import urllib.request

    rink = IMAGE_SCHEDULE_RINKS[0]
    if not dates:
        return []

    print(f"  {rink['name']}: loading schedule image...", file=sys.stderr)
    sessions = []

    try:
        driver.get(rink['url'])
        time.sleep(4)

        # Find the calendar image
        imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="Calendar"]')
        if not imgs:
            imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="calendar"]')
        if not imgs:
            print("    no calendar image found", file=sys.stderr)
            return []

        img_url = imgs[0].get_attribute('src')
        print(f"    image: {img_url.split('/')[-1]}", file=sys.stderr)

        # Download the image
        req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            img_bytes = resp.read()
        img_b64 = base64.b64encode(img_bytes).decode()

        # Determine current month/year for context
        target_month = min(dates).strftime('%B %Y')

        # Call Claude vision via Anthropic API
        import os as _os
        import requests as _req
        api_key = _os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            print("    ANTHROPIC_API_KEY not set — skipping vision OCR", file=sys.stderr)
            return []
        resp = _req.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            },
            json={
                'model': 'claude-opus-4-5',
                'max_tokens': 1024,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': 'image/jpeg',
                                'data': img_b64,
                            }
                        },
                        {
                            'type': 'text',
                            'text': f"""This is the {target_month} skating schedule for Jackson Optimist Ice Arena.
Extract ONLY the Stick & Puck sessions (highlighted in yellow).
Return a JSON array with objects: {{date_num: int, time_start: "H:MM AM/PM", time_end: "H:MM AM/PM"}}
Only include Stick & Puck entries, not public skating.
Return ONLY the JSON array, no other text."""
                        }
                    ]
                }]
            },
            timeout=30
        )

        if resp.status_code != 200:
            print(f"    Claude vision API error: {resp.status_code}", file=sys.stderr)
            return []

        raw = resp.json()['content'][0]['text'].strip()
        # Strip markdown fences if present
        raw = re.sub(r'```[a-z]*', '', raw).replace('```', '').strip()
        parsed = _json.loads(raw)

        # Map date numbers to actual dates
        year  = min(dates).year
        month = min(dates).month
        date_set = set(dates)

        for item in parsed:
            try:
                event_date = datetime(year, month, int(item['date_num'])).date()
            except (ValueError, KeyError):
                continue
            if event_date not in date_set:
                continue

            start_raw = item.get('time_start', '').strip()
            end_raw   = item.get('time_end', '').strip()
            if not start_raw:
                continue

            sessions.append({
                "name":      "Stick & Puck",
                "date":      event_date,
                "start_raw": start_raw,
                "end_raw":   end_raw,
                "location":  None,
                "rink":      rink["name"],
                "dist":      rink["dist"],
                "price":     rink.get("price"),
                "url":       rink["url"],
                "source":    "Jackson Optimist (vision)",
            })

    except Exception as e:
        print(f"    error: {e}", file=sys.stderr)

    if sessions:
        print(f"    → {len(sessions)} Jackson Optimist session(s)", file=sys.stderr)
    return sessions


# ─── SportsEngine scraper ────────────────────────────────────────────────────

def scrape_sportngin(driver, dates: list) -> list[dict]:
    """
    SportsEngine month list view renders clean plain text.
    URL: /event/show_month_list/{page_id}
    Format per event block:
      May 4
      STICKS & PUCKS
      Monday, 12:00pm EDT-12:50pm EDT
      Tag(s): Sticks & Pucks
    """
    from selenium.webdriver.common.by import By
    HOCKEY_PAT = re.compile(r'sticks?\s*[&and]+\s*pucks?|drop.?in hockey|pickup hockey|adult hockey', re.I)
    DATE_PAT   = re.compile(r'^([A-Z][a-z]+ \d{1,2})$')
    TIME_PAT   = re.compile(r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*(\d{1,2}:\d{2}(?:am|pm))\s*EDT[\-–](\d{1,2}:\d{2}(?:am|pm))', re.I)
    all_sessions = []

    processed_months = set()
    for rink in SPORTNGIN_RINKS:
        rink_sessions = []
        for date in dates:
            mk = (date.year, date.month)
            if mk in processed_months:
                continue
            processed_months.add(mk)

            # Navigate to month list for this month
            url = rink["month_list_url"]
            # SportsEngine month list shows current month by default;
            # for future months append ?month=YYYY-MM
            if date.year != datetime.now().year or date.month != datetime.now().month:
                from calendar import monthrange
                url += f"?month={date.year}-{date.month:02d}-01"

            print(f"  {rink['name']}: fetching {date.year}-{date.month:02d}...", file=sys.stderr)
            try:
                driver.get(url)
                time.sleep(3)
                body = driver.find_element(By.TAG_NAME, "body").text
            except Exception as e:
                print(f"    error: {e}", file=sys.stderr)
                continue

            # Parse the plain text
            lines = [l.strip() for l in body.split("\n") if l.strip()]
            i = 0
            while i < len(lines):
                dm = DATE_PAT.match(lines[i])
                if dm:
                    # Try to parse the date (e.g. "May 4")
                    try:
                        event_date = datetime.strptime(f"{dm.group(1)} {date.year}", "%B %d %Y").date()
                        # Handle year boundary
                        if abs((event_date - date).days) > 180:
                            event_date = datetime.strptime(f"{dm.group(1)} {date.year+1}", "%B %d %Y").date()
                    except ValueError:
                        i += 1
                        continue

                    if event_date not in dates:
                        i += 1
                        continue

                    # Look ahead for hockey event name and time
                    j = i + 1
                    while j < min(i + 6, len(lines)):
                        if HOCKEY_PAT.search(lines[j]):
                            event_name = lines[j].title()
                            # Look for time on next line
                            if j + 1 < len(lines):
                                tm = TIME_PAT.search(lines[j+1])
                                if tm:
                                    start_raw = tm.group(1).upper()
                                    end_raw   = tm.group(2).upper()
                                    # Normalize: 12:00PM -> 12:00 PM
                                    start_raw = re.sub(r'(\d)(AM|PM)', r'\1 \2', start_raw)
                                    end_raw   = re.sub(r'(\d)(AM|PM)', r'\1 \2', end_raw)
                                    rink_sessions.append({
                                        "name":      event_name,
                                        "date":      event_date,
                                        "start_raw": start_raw,
                                        "end_raw":   end_raw,
                                        "location":  None,
                                        "rink":      rink["name"],
                                        "dist":      rink["dist"],
                                        "price":     rink["price"],
                                        "url":       rink["url"],
                                        "source":    "SportsEngine",
                                    })
                        j += 1
                i += 1

        if rink_sessions:
            print(f"    → {len(rink_sessions)} session(s)", file=sys.stderr)
        all_sessions.extend(rink_sessions)

    return all_sessions


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

        date_list = [today + timedelta(days=i) for i in range(args.days)]

        print("\n── DaySmart (Tam-O-Shanter) ─────────────────────────────────", file=sys.stderr)
        all_sessions += scrape_daysmart(driver, date_list)

        print("\n── Jackson Optimist (vision OCR) ────────────────────────────", file=sys.stderr)
        all_sessions += scrape_jackson_optimist(driver, date_list)

        print("\n── SportsEngine (Arctic Edge Canton) ────────────────────────", file=sys.stderr)
        all_sessions += scrape_sportngin(driver, date_list)

        print("\n── Direct scrapes (Allen Park, Eddie Edgar) ─────────────────", file=sys.stderr)
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
