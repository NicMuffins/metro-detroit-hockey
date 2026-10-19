#!/usr/bin/env python3
"""
Metro Detroit Hockey — Stick & Puck / Drop-In iCal feed (v3)
=============================================================

Reads every rink from rinks.json, pulls the next N days of sessions from each
rink's own scheduling system, keeps only stick & puck / drop-in style sessions,
and writes one .ics file plus a per-rink health report (rink_status.json).

Sources (the "source" field in rinks.json):
  ice_finder      ice-finder.com rink pages (next ~7 days)
  bond            Bond Sports public JSON API (Biggby rinks, Hazel Park)
  daysmart        DaySmart Recreation public JSON API (Taylor, Trenton, Southgate, Tam-O-Shanter)
  ics             Any public iCal feed (Google Calendar, CivicPlus, SportsEngine)
  ezfacility      EZFacility session calendar (Big Boy Arena)
  finnly          FinnlyConnect schedule page (Hartland Ice House)
  static_weekly   A fixed weekly schedule from the rink's site, optionally checked against that page
  jackson_vision  Monthly JPEG schedule read with the Claude API
  scs_pdf         St Clair Shores monthly PDF calendar
  allen_park      Allen Park city calendar (browser)
  eddie_edgar     Eddie Edgar FullCalendar (browser)

Usage:
  python scraper.py [--days 14] [--output hockey_sessions.ics] [--headless true]
                    [--status rink_status.json] [--only id1,id2]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Detroit")
HERE = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Sessions longer than this are placeholders ("12:00 AM – 11:59 PM"), not real ice times.
# (Allen Park's Wednesday S&P really is 4.5 hours.)
MAX_SESSION_HOURS = 6


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ─── Session model ────────────────────────────────────────────────────────────

@dataclass
class Session:
    rink_id: str
    rink: str
    name: str
    start: datetime          # timezone-aware
    end: datetime            # timezone-aware
    dist: int
    url: str
    source: str
    price: str | None = None
    location: str | None = None
    extra: str | None = None

    @property
    def kind(self) -> str:
        return "Stick & Puck" if re.search(r"stick|puck", self.name, re.I) else "Drop-In"


@dataclass
class Window:
    start: date               # first day (inclusive)
    end: date                 # last day (inclusive)

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.start, datetime.min.time(), TZ)

    @property
    def end_dt(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), datetime.min.time(), TZ)

    def months(self) -> list[tuple[int, int]]:
        out, d = [], self.start.replace(day=1)
        while d <= self.end:
            out.append((d.year, d.month))
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return out


# ─── Filtering ────────────────────────────────────────────────────────────────

class Matcher:
    def __init__(self, include: str, exclude: str):
        self.inc = re.compile(include, re.I)
        self.exc = re.compile(exclude, re.I) if exclude else None

    def ok(self, *texts: str) -> bool:
        blob = " | ".join(t for t in texts if t)
        if not self.inc.search(blob):
            return False
        return not (self.exc and self.exc.search(blob))


def matcher_for(rink: dict, defaults: dict) -> Matcher:
    return Matcher(rink.get("include", defaults["include"]),
                   rink.get("exclude", defaults["exclude"]))


# ─── Time helpers ─────────────────────────────────────────────────────────────

def local(d: date, hhmm: str) -> datetime:
    """'10:30 AM' / '10:30am' / '22:10' / '10:30:00' on date d → aware Detroit datetime."""
    s = hhmm.strip().upper().replace(".", "")
    s = re.sub(r"(\d)(AM|PM)$", r"\1 \2", s)
    for fmt in ("%I:%M %p", "%I %p", "%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            return datetime.combine(d, t, TZ)
        except ValueError:
            pass
    raise ValueError(f"bad time {hhmm!r}")


def from_naive_local(s: str) -> datetime:
    """'2026-10-19T12:00:00' (rink local time) → aware."""
    return datetime.fromisoformat(s[:19]).replace(tzinfo=TZ)


def sane(start: datetime, end: datetime) -> bool:
    return end > start and (end - start) <= timedelta(hours=MAX_SESSION_HOURS)


def clean(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


# ─── HTTP helpers (requests first, browser fallback) ──────────────────────────

class Fetcher:
    """Plain HTTP when it works; a headless Chrome page when a site needs JS or blocks bots."""

    def __init__(self, headless: bool = True):
        import requests
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        self.headless = headless
        self._driver = None

    @property
    def driver(self):
        if self._driver is None:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            if self.headless:
                opts.add_argument("--headless=new")
            for a in ("--no-sandbox", "--disable-dev-shm-usage", "--window-size=1400,900",
                      "--disable-blink-features=AutomationControlled", f"--user-agent={UA}"):
                opts.add_argument(a)
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            try:
                self._driver = webdriver.Chrome(options=opts)  # Selenium Manager finds a driver
            except Exception:
                from selenium.webdriver.chrome.service import Service
                from webdriver_manager.chrome import ChromeDriverManager
                self._driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
            self._driver.set_page_load_timeout(45)
        return self._driver

    def close(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass

    # -- text/HTML --
    def html(self, url: str, must_contain: str | None = None, wait: float = 4) -> str:
        try:
            r = self.http.get(url, timeout=30)
            if r.status_code == 200 and (not must_contain or re.search(must_contain, r.text, re.I)):
                return r.text
            log(f"    http {r.status_code} for {url} — trying browser")
        except Exception as e:
            log(f"    http error {e.__class__.__name__} for {url} — trying browser")
        self.driver.get(url)
        time.sleep(wait)
        return self.driver.page_source

    def bytes(self, url: str, referer: str | None = None) -> bytes:
        h = {"Referer": referer} if referer else {}
        r = self.http.get(url, timeout=45, headers=h)
        r.raise_for_status()
        return r.content

    # -- JSON APIs --
    def json(self, url: str, params: dict | None = None, headers: dict | None = None,
             method: str = "GET", data: str | None = None, browser_origin: str | None = None):
        try:
            if method == "GET":
                r = self.http.get(url, params=params, headers=headers or {}, timeout=30)
            else:
                r = self.http.post(url, params=params, headers=headers or {}, data=data, timeout=30)
            if r.status_code == 200:
                return r.json()
            log(f"    api {r.status_code} for {url}")
        except Exception as e:
            log(f"    api error {e.__class__.__name__}: {e}")
        if not browser_origin:
            raise RuntimeError(f"API request failed: {url}")
        # Fallback: run fetch() from inside a page on the site's own origin.
        import urllib.parse
        full = url + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        self.driver.get(browser_origin)
        time.sleep(4)
        js = """
const cb = arguments[arguments.length - 1];
fetch(arguments[0], {method: arguments[1], headers: arguments[2], body: arguments[3]})
  .then(r => r.text()).then(t => cb(t)).catch(e => cb('ERROR:' + e));
"""
        self.driver.set_script_timeout(45)
        txt = self.driver.execute_async_script(js, full, method, headers or {}, data)
        if not txt or txt.startswith("ERROR:"):
            raise RuntimeError(f"browser fetch failed: {txt}")
        return json.loads(txt)


# ─── Source: ice-finder.com ───────────────────────────────────────────────────

ICE_FINDER_LINE = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,\s+([A-Za-z]+\s+\d{1,2})\s*[•·]\s*"
    r"(\d{1,2}:\d{2}\s*[AP]M)\s*[–\-]\s*(\d{1,2}:\d{2}\s*[AP]M)", re.I)


def _parse_month_day(md: str, today: date) -> date | None:
    for fmt in ("%B %d %Y", "%b %d %Y"):          # "October 20" (site today) or "Oct 20"
        try:
            d = datetime.strptime(f"{md} {today.year}", fmt).date()
            if d < today - timedelta(days=60):    # Dec → Jan rollover
                d = d.replace(year=today.year + 1)
            return d
        except ValueError:
            continue
    return None


def parse_ice_finder(text: str, rink: dict, m: Matcher, win: Window) -> list[Session]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = []
    for i, line in enumerate(lines):
        mm = ICE_FINDER_LINE.match(line)
        if not mm or i == 0:
            continue
        name = lines[i - 1]
        if not m.ok(name):
            continue
        d = _parse_month_day(mm.group(1), win.start)
        if not d or not win.contains(d):
            continue
        start, end = local(d, mm.group(2)), local(d, mm.group(3))
        if not sane(start, end):
            continue
        price = next((lines[k] for k in range(i + 1, min(i + 4, len(lines))) if "$" in lines[k]), None)
        out.append(Session(rink["id"], rink["name"], name, start, end, rink["dist_mi"],
                           rink.get("url") or "", "ice-finder.com", price or rink.get("price")))
    return out


def html_to_lines(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    root = soup.find("main") or soup.body or soup
    # React splits one visible line into several text nodes ("Tue, October 20<!-- --> <!-- -->• ..."),
    # so read each paragraph/heading as one line instead of every text node separately.
    blocks = root.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"])
    if blocks:
        return "\n".join(b.get_text("", strip=False).strip() for b in blocks if b.get_text(strip=True))
    return root.get_text("\n")


def scrape_ice_finder(f: Fetcher, rink, m, win) -> list[Session]:
    url = f"https://ice-finder.com/rinks/michigan/rink/{rink['slug']}"
    html = f.html(url, must_contain=r"Schedule")
    return parse_ice_finder(html_to_lines(html), rink, m, win)


# ─── Source: Bond Sports API ──────────────────────────────────────────────────

def parse_bond(data: dict, rink: dict, m: Matcher, win: Window) -> list[Session]:
    out, seen = [], set()
    for e in data.get("data", []):
        prog, ev = clean(e.get("programName")), clean(e.get("eventName"))
        if not m.ok(prog, ev):
            continue
        if str(e.get("status", "")).lower() in ("canceled", "cancelled", "inactive"):
            continue
        try:
            d = datetime.strptime(e["eventStartDate"].replace("-", "/"), "%Y/%m/%d").date()
            d_end = datetime.strptime((e.get("eventEndDate") or e["eventStartDate"]).replace("-", "/"), "%Y/%m/%d").date()
            tz = ZoneInfo(e.get("eventTimezone") or "America/Detroit")
            start = datetime.combine(d, datetime.strptime(e["eventStartTime"], "%H:%M:%S").time(), tz)
            end = datetime.combine(d_end, datetime.strptime(e["eventEndTime"], "%H:%M:%S").time(), tz)
        except (KeyError, ValueError):
            continue
        if not win.contains(d) or not sane(start, end):
            continue
        key = (start, ev.lower())
        if key in seen:
            continue
        seen.add(key)
        # "Stick & Puck - Fri - 10:00 am" / "Freestyle Drop-In - October 2026 - Tue - 06:00 am" → base name
        name = re.sub(r"\s*-\s*(?:\w+ \d{4}\s*-\s*)?\w{3}\s*-\s*\d\d:\d\d\s*[ap]m$", "", ev, flags=re.I) or prog
        loc = ", ".join(s.get("spaceName", "") for s in (e.get("spaces") or []) if s.get("spaceName")) or None
        out.append(Session(rink["id"], rink["name"], name, start, end, rink["dist_mi"],
                           rink.get("url") or "", "Bond Sports", rink.get("price"), loc))
    return out


def scrape_bond(f: Fetcher, rink, m, win) -> list[Session]:
    url = f"https://api.bondsports.co/v4/facilities/{rink['facility_id']}/programs-schedule"
    params = {"startDate": win.start.isoformat(), "endDate": (win.end + timedelta(days=1)).isoformat()}
    data = f.json(url, params, headers={"Origin": "https://bondsports.co", "Referer": "https://bondsports.co/"},
                  browser_origin="https://bondsports.co/")
    return parse_bond(data, rink, m, win)


# ─── Source: DaySmart Recreation API ──────────────────────────────────────────

def parse_daysmart(pages: list[dict], rink: dict, m: Matcher, win: Window) -> list[Session]:
    teams = {}
    for p in pages:
        for inc in p.get("included", []) or []:
            if inc.get("type") == "teams":
                teams[str(inc["id"])] = clean(inc.get("attributes", {}).get("name"))
    out, seen = [], set()
    for p in pages:
        for e in p.get("data", []):
            a = e.get("attributes", {})
            desc = clean(a.get("desc"))
            team = teams.get(str(a.get("hteam_id")), "")
            if not m.ok(desc, team):
                continue
            try:
                if a.get("start_gmt") and a.get("end_gmt"):
                    start = datetime.fromisoformat(a["start_gmt"][:19]).replace(tzinfo=timezone.utc)
                    end = datetime.fromisoformat(a["end_gmt"][:19]).replace(tzinfo=timezone.utc)
                else:
                    start, end = from_naive_local(a["start"]), from_naive_local(a["end"])
            except (KeyError, ValueError):
                continue
            if not win.contains(start.astimezone(TZ).date()) or not sane(start, end):
                continue
            # Short codes like "ADI" read better as the team/program name
            name = desc if desc and not re.fullmatch(r"[A-Z]{2,4}", desc) else (team or desc)
            name = re.sub(r"^\w+ \d{4}\s*-\s*", "", name)          # "October 2026 - Adult Drop In Hockey"
            name = re.sub(r"\s*:\s*(?:M|T|W|Th|F|Sa|Su|Sundays?|Mondays?)(?:/\w+)*\s*$", "", name)
            key = (start, name.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(Session(rink["id"], rink["name"], name, start, end, rink["dist_mi"],
                               rink.get("url") or "", "DaySmart", rink.get("price")))
    return out


def scrape_daysmart(f: Fetcher, rink, m, win) -> list[Session]:
    company = rink["company"]
    pages, page = [], 1
    while True:
        params = {
            "page[size]": "200", "page[number]": str(page), "sort": "start",
            "company": company, "filter[unconstrained]": "1", "include": "homeTeam",
            "filter[start_date__gte]": win.start.isoformat(),
            "filter[start_date__lte]": win.end.isoformat(),
        }
        data = f.json("https://api.daysmartrecreation.com/v1/events", params,
                      headers={"Accept": "application/vnd.api+json"},
                      browser_origin=f"https://apps.daysmartrecreation.com/dash/x/#/online/{company}/event-registration")
        pages.append(data)
        last = (((data.get("meta") or {}).get("page") or {}).get("last-page")) or 1
        if page >= last or page >= 20:
            break
        page += 1
    return parse_daysmart(pages, rink, m, win)


# ─── Source: iCal feeds ───────────────────────────────────────────────────────

def parse_ics(raw: bytes | str, rink: dict, m: Matcher, win: Window) -> list[Session]:
    import icalendar
    import recurring_ical_events
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    # SportsEngine writes "X-WR-TIMEZONE::America/New_York" — harmless, but tidy it.
    raw = re.sub(r"^(X-WR-[A-Z]+|METHOD)::", r"\1:", raw, flags=re.M)
    cal = icalendar.Calendar.from_ical(raw)
    out, seen = [], set()
    for ev in recurring_ical_events.of(cal).between(win.start_dt, win.end_dt):
        summary = clean(str(ev.get("SUMMARY", "")))
        if str(ev.get("STATUS", "")).upper() == "CANCELLED" or re.search(r"cancel", summary, re.I):
            continue
        if not m.ok(summary):
            continue
        s, e = ev.get("DTSTART").dt, (ev.get("DTEND").dt if ev.get("DTEND") else None)
        if not isinstance(s, datetime):
            continue                                 # all-day entries aren't sessions
        s = s if s.tzinfo else s.replace(tzinfo=TZ)
        e = e if isinstance(e, datetime) else s + timedelta(minutes=80)
        e = e if e.tzinfo else e.replace(tzinfo=TZ)
        if not win.contains(s.astimezone(TZ).date()) or not sane(s, e):
            continue
        key = (s, summary.lower())
        if key in seen:
            continue
        seen.add(key)
        loc = clean(str(ev.get("LOCATION", ""))) or None
        out.append(Session(rink["id"], rink["name"], summary, s, e, rink["dist_mi"],
                           rink.get("url") or "", "iCal", rink.get("price"), loc))
    return out


def scrape_ics(f: Fetcher, rink, m, win) -> list[Session]:
    r = f.http.get(rink["ics_url"], timeout=30)
    r.raise_for_status()
    if b"BEGIN:VCALENDAR" not in r.content[:500]:
        raise RuntimeError("response is not an iCal feed")
    return parse_ics(r.content, rink, m, win)


# ─── Source: EZFacility ───────────────────────────────────────────────────────

def parse_ezfacility(items: list[dict], rink: dict, m: Matcher, win: Window) -> list[Session]:
    out = []
    for e in items or []:
        title = clean(e.get("title") or e.get("reservationType"))
        if not m.ok(title):
            continue
        try:
            start, end = from_naive_local(e["start"]), from_naive_local(e["end"])
        except (KeyError, ValueError):
            continue
        if not win.contains(start.date()) or not sane(start, end):
            continue
        out.append(Session(rink["id"], rink["name"], title, start, end, rink["dist_mi"],
                           rink.get("url") or "", "EZFacility", rink.get("price"), e.get("resourceName")))
    return out


def scrape_ezfacility(f: Fetcher, rink, m, win) -> list[Session]:
    host = rink["ezfacility_host"]
    body = (f"LocationId={rink['location_id']}&Sunday=true&Monday=true&Tuesday=true&Wednesday=true"
            "&Thursday=true&Friday=true&Saturday=true&StartTime=12%3A00%20AM&EndTime=12%3A00%20AM"
            "&ReservationTypes%5B0%5D.Selected=true&ReservationTypes%5B0%5D.Id=-1"
            "&Resources%5B0%5D.Selected=true&Resources%5B0%5D.Id=-1"
            f"&StartDate={win.start.isoformat()}&EndDate={(win.end + timedelta(days=1)).isoformat()}")
    items = f.json(f"https://{host}/Sessions/FilterResults", method="POST", data=body,
                   headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "X-Requested-With": "XMLHttpRequest", "Referer": f"https://{host}/Sessions"},
                   browser_origin=f"https://{host}/Sessions")
    return parse_ezfacility(items, rink, m, win)


# ─── Source: FinnlyConnect ────────────────────────────────────────────────────

def parse_finnly(html: str, rink: dict, m: Matcher, win: Window) -> list[Session]:
    mm = re.search(r"_onlineScheduleList\s*=\s*\[", html)     # the assignment, not the `let` declaration
    if not mm:
        raise RuntimeError("schedule data not found on page")
    items, _ = json.JSONDecoder().raw_decode(html[mm.end() - 1:])
    out, seen = [], set()
    for e in items:
        acct, desc = clean(e.get("AccountName")), clean(e.get("Description"))
        if not m.ok(acct, desc):
            continue
        try:
            start, end = from_naive_local(e["EventStartTime"]), from_naive_local(e["EventEndTime"])
        except (KeyError, ValueError):
            continue
        if e.get("Closed") or not win.contains(start.date()) or not sane(start, end):
            continue
        name = acct or re.sub(r"^Activity:\s*", "", desc)
        key = (start, name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(Session(rink["id"], rink["name"], name, start, end, rink["dist_mi"],
                           rink.get("url") or "", "FinnlyConnect", rink.get("price"), e.get("FacilityName")))
    return out


def scrape_finnly(f: Fetcher, rink, m, win) -> list[Session]:
    return parse_finnly(f.html(rink["schedule_url"], must_contain="_onlineScheduleList"), rink, m, win)


# ─── Source: fixed weekly schedule ────────────────────────────────────────────

DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def build_static(rink: dict, win: Window) -> list[Session]:
    out = []
    skip = {date.fromisoformat(x) for x in rink.get("skip_dates", [])}
    for block in rink["schedule"]:
        days = {DOW[d[:3].lower()] for d in block["days"]}
        lo = max(win.start, date.fromisoformat(block.get("from", win.start.isoformat())))
        hi = min(win.end, date.fromisoformat(block.get("until", win.end.isoformat())))
        d = lo
        while d <= hi:
            if d.weekday() in days and d not in skip:
                s, e = local(d, block["start"]), local(d, block["end"])
                if e <= s:
                    e += timedelta(days=1)
                out.append(Session(rink["id"], rink["name"], block["name"], s, e, rink["dist_mi"],
                                   rink.get("url") or "", "rink website (weekly schedule)", rink.get("price"),
                                   extra="Regular weekly time from the rink's website — holidays/cancellations not reflected."))
            d += timedelta(days=1)
    return out


def scrape_static(f: Fetcher, rink, m, win) -> list[Session]:
    if rink.get("verify_url"):
        page = f.html(rink["verify_url"])
        if not re.search(rink["verify_text"], page, re.I):
            raise RuntimeError("rink page no longer mentions this session — check and update rinks.json")
    return build_static(rink, win)


# ─── Source: Jackson Optimist (monthly JPEG → Claude vision) ──────────────────

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
VISION_MODELS = [m for m in [os.environ.get("VISION_MODEL"), "claude-sonnet-5-5",
                             "claude-sonnet-4-5", "claude-opus-4-5"] if m]


def find_month_images(html: str, base_url: str) -> dict[tuple[int, int], str]:
    """Map (year, month) → schedule image URL, based on the filename."""
    found = {}
    urls = re.findall(r"""https?://[^\s"'<>(),]+?/wp-content/uploads/[^\s"'<>(),]+?\.(?:jpe?g|png|webp)""", html, re.I)
    # full-size files first (WordPress also emits resized copies like name-300x232.jpg)
    urls = sorted(dict.fromkeys(urls), key=lambda u: bool(re.search(r"-\d+x\d+\.\w+$", u)))
    for src in urls:
        name = src.rsplit("/", 1)[-1].lower()
        if re.search(r"logo|icon|cropped|favicon", name):
            continue
        mo = next((i + 1 for i, mn in enumerate(MONTHS) if mn in name or mn[:3] + "-" in name), None)
        yr = re.search(r"(20\d\d)", name) or re.search(r"/(20\d\d)/\d\d/", src)
        if mo and yr:
            found.setdefault((int(yr.group(1)), mo), src)
    return found


def parse_vision_json(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    return json.loads(m.group(0)) if m else []


def scrape_jackson(f: Fetcher, rink, m, win) -> list[Session]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — can't read the image schedule")
    html = f.html(rink["url"], must_contain=r"wp-content/uploads")
    imgs = find_month_images(html, rink["url"])
    out, seen_any = [], []
    for (yr, mo) in win.months():
        src = imgs.get((yr, mo))
        if not src:
            log(f"    no {MONTHS[mo-1].title()} {yr} image posted yet")
            continue
        img = f.bytes(src, referer=rink["url"])
        media = "image/png" if src.lower().endswith(".png") else ("image/webp" if src.lower().endswith(".webp") else "image/jpeg")
        prompt = (f"This is the {MONTHS[mo-1].title()} {yr} skating schedule for an ice arena, laid out as a calendar grid. "
                  "List every Stick & Puck session and every Drop-in Hockey session. Ignore public/open skating. "
                  "Read the day number in each cell carefully. Return ONLY a JSON array of objects: "
                  '{"day": int, "type": "Stick & Puck" | "Drop-in Hockey", "start": "H:MM AM/PM", "end": "H:MM AM/PM"}')
        items = None
        for model in VISION_MODELS:
            r = f.http.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
                "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media,
                                                 "data": base64.b64encode(img).decode()}},
                    {"type": "text", "text": prompt}]}]})
            if r.status_code == 200:
                blocks = r.json().get("content", [])
                items = parse_vision_json("".join(b.get("text", "") for b in blocks if b.get("type") == "text"))
                break
            log(f"    {model}: HTTP {r.status_code}")
            if r.status_code not in (400, 404):
                break
        if items is None:
            raise RuntimeError("Claude API call failed")
        log(f"    {MONTHS[mo-1].title()}: model listed {len(items)} session(s)")
        for it in items:
            try:
                d = date(yr, mo, int(it.get("day") or it.get("date_num")))
                s, e = vision_times(d, str(it.get("start") or it.get("time_start")),
                                    str(it.get("end") or it.get("time_end") or ""))
            except (KeyError, ValueError, TypeError):
                log(f"    couldn't read {it}")
                continue
            name = it.get("type") or "Stick & Puck"
            if win.contains(d) and sane(s, e) and m.ok(name):
                out.append(Session(rink["id"], rink["name"], name, s, e, rink["dist_mi"],
                                   rink["url"], "monthly schedule image", rink.get("price")))
        seen_any += items
    if not out:
        raise RuntimeError("no usable sessions from schedule image(s); found images for "
                           f"{sorted(imgs)}; model returned {json.dumps(seen_any[:3])[:200]}")
    return out


def vision_times(d: date, start: str, end: str) -> tuple[datetime, datetime]:
    """Forgiving time reader: '3:30 PM', '3:30pm', '3:30' + '4:50 PM', or a whole '3:30 – 4:50 PM' range."""
    if not end:
        m = TIME_RANGE.search(start.replace("–", "-")) or re.search(
            r"(\d{1,2}:\d{2})\s*([AP])?M?\s*-\s*(\d{1,2}:\d{2})\s*([AP])M?", start, re.I)
        if not m:
            raise ValueError(start)
        start, end = _range_to_times(m) if m.re is TIME_RANGE else (m.group(1), m.group(3) + " " + (m.group(4) or "P") + "M")
    if not re.search(r"[ap]\.?m", start, re.I):
        ap = re.search(r"([ap])\.?m", end, re.I)
        sh, eh = int(start.split(":")[0]) % 12, int(end.split(":")[0]) % 12
        e_ap = ap.group(1).upper() if ap else "P"
        start += " " + (e_ap if sh <= eh else ("A" if e_ap == "P" else "P")) + "M"
    return local(d, start), local(d, end)


# ─── Source: St Clair Shores monthly PDF ──────────────────────────────────────

TIME_RANGE = re.compile(
    r"(\d{1,2}:\d{2})\s*([AP])?\.?M?\.?\s*[-–]\s*(\d{1,2}:\d{2})\s*([AP])\.?M?\.?", re.I)
WEEKDAY_HEADERS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]


def _range_to_times(m: re.Match) -> tuple[str, str]:
    s, s_ap, e, e_ap = m.group(1), m.group(2), m.group(3), m.group(4).upper()
    if s_ap:
        s_ap = s_ap.upper()
    else:
        sh, eh = int(s.split(":")[0]) % 12, int(e.split(":")[0]) % 12
        s_ap = e_ap if sh <= eh else ("A" if e_ap == "P" else "P")   # "11:30-1:00 P" → 11:30 AM
    return f"{s} {s_ap}M", f"{e} {e_ap}M"


def _lines(words: list[dict]) -> list[str]:
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    lines, cur, top = [], [], None
    for w in words:
        if top is None or abs(w["top"] - top) <= 3:
            cur.append(w)
            top = w["top"] if top is None else top
        else:
            lines.append(" ".join(x["text"] for x in sorted(cur, key=lambda x: x["x0"])))
            cur, top = [w], w["top"]
    if cur:
        lines.append(" ".join(x["text"] for x in sorted(cur, key=lambda x: x["x0"])))
    return lines


def parse_calendar_words(words: list[dict], page_height: float, year: int, month: int,
                         rink: dict, m: Matcher, win: Window) -> list[Session]:
    """Generic month-grid parser: weekday headers give columns, day numbers give rows."""
    full_names = ["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]
    heads = {}
    for w in sorted(words, key=lambda w: w["top"]):
        t = w["text"].strip().upper().rstrip(".")
        for i in range(7):
            if t in (WEEKDAY_HEADERS[i], full_names[i]):
                heads.setdefault(i, ((w["x0"] + w["x1"]) / 2, w["top"]))
    if len(heads) < 6:
        raise RuntimeError("couldn't find the weekday header row in the PDF")
    centers = [heads[i][0] if i in heads else None for i in range(7)]
    header_y = max(v[1] for v in heads.values())
    bounds = [-1e9] + [(centers[i] + centers[i + 1]) / 2 for i in range(6)] + [1e9]

    def col_of(x):
        return next(i for i in range(7) if bounds[i] <= x < bounds[i + 1])

    import calendar as _cal
    ndays = _cal.monthrange(year, month)[1]
    nums = []
    for w in words:
        if re.fullmatch(r"\d{1,2}", w["text"].strip()) and w["top"] > header_y + 2:
            n = int(w["text"])
            if 1 <= n <= ndays and col_of(w["x0"]) == (date(year, month, n).weekday() + 1) % 7:
                nums.append((n, w))
    pos = {}
    for n, w in nums:                                # keep the top-most match per day number
        if n not in pos or w["top"] < pos[n]["top"]:
            pos[n] = w
    row_tops = sorted({round(w["top"]) for w in pos.values()})

    out = []
    for n, w in pos.items():
        d = date(year, month, n)
        if not win.contains(d):
            continue
        c = col_of(w["x0"])
        top = w["top"]
        below = [t for t in row_tops if t > top + 5]
        bottom = below[0] - 2 if below else page_height
        cell = [x for x in words if top + 1 < x["top"] < bottom
                and bounds[c] <= (x["x0"] + x["x1"]) / 2 < bounds[c + 1] and x is not w]
        lines = _lines(cell)
        for i, line in enumerate(lines):
            label = TIME_RANGE.split(line)[0].strip() if TIME_RANGE.search(line) else line
            if not m.ok(label):
                continue
            tm = None
            for j in range(i, min(i + 3, len(lines))):  # time is on the label line or just below it
                tm = TIME_RANGE.search(lines[j])
                if tm:
                    break
            if not tm:
                continue
            s_txt, e_txt = _range_to_times(tm)
            s, e = local(d, s_txt), local(d, e_txt)
            if not sane(s, e):
                continue
            name = re.sub(r"\bA\s+DULT\b", "ADULT", label, flags=re.I).title()
            out.append(Session(rink["id"], rink["name"], name, s, e, rink["dist_mi"],
                               rink["url"], "monthly PDF calendar", rink.get("price")))
    return out


def find_month_pdf_links(html: str, base: str) -> dict[tuple[int, int], str]:
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True).lower()      # includes links hidden in collapsed tabs
        if "documentcenter" not in a["href"].lower():
            continue
        mo = next((i + 1 for i, mn in enumerate(MONTHS) if mn in txt), None)
        yr = re.search(r"20\d\d", txt)
        if mo and yr and re.search(r"calendar|skate|schedule", txt):
            found.setdefault((int(yr.group(0)), mo), urljoin(base, a["href"]))
    return found


def scrape_scs(f: Fetcher, rink, m, win) -> list[Session]:
    import pdfplumber
    html = f.html(rink["url"], must_contain="DocumentCenter")
    links = find_month_pdf_links(html, rink["url"])
    out = []
    for (yr, mo) in win.months():
        url = links.get((yr, mo))
        if not url:
            log(f"    no {MONTHS[mo-1].title()} {yr} PDF posted yet")
            continue
        pdf_bytes = f.bytes(url, referer=rink["url"])
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            words = page.extract_words(x_tolerance=1.5, y_tolerance=3)
            out += parse_calendar_words(words, page.height, yr, mo, rink, m, win)
    return out


# ─── Source: Allen Park city calendar (browser) ───────────────────────────────

def parse_allen_park(text: str, rink: dict, m: Matcher, win: Window) -> list[Session]:
    tp = re.compile(r"(\d{1,2}:\d{2}\s*[ap]m)\s*-\s*(\d{1,2}:\d{2}\s*[ap]m)", re.I)
    out = []
    chunks = re.split(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2})", text)
    cur = None
    for chunk in chunks:
        dm = re.fullmatch(r"(\w+)\s+(\d{1,2}),\s+(20\d{2})", chunk.strip())
        if dm:
            for fmt in ("%B %d, %Y", "%b %d, %Y"):
                try:
                    cur = datetime.strptime(chunk.strip(), fmt).date()
                    break
                except ValueError:
                    cur = None
            continue
        if not cur or not win.contains(cur):
            continue
        lines = [l.strip() for l in chunk.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if not m.ok(tp.sub("", line)):
                continue
            tm = tp.search(line) or (tp.search(lines[i - 1]) if i else None) or \
                (tp.search(lines[i + 1]) if i + 1 < len(lines) else None)
            if not tm:
                continue
            s, e = local(cur, tm.group(1)), local(cur, tm.group(2))
            if sane(s, e):
                out.append(Session(rink["id"], rink["name"], "Sticks and Pucks", s, e, rink["dist_mi"],
                                   rink["url"], "Allen Park city calendar", rink.get("price")))
    # one entry per start time
    uniq = {(x.start): x for x in out}
    return list(uniq.values())


def scrape_allen_park(f: Fetcher, rink, m, win) -> list[Session]:
    from selenium.webdriver.common.by import By
    out = []
    for (yr, mo) in win.months():
        url = f"https://cityofallenpark.org/calendar.php?view=list&month={mo}&day=1&year={yr}&display=31"
        f.driver.get(url)
        time.sleep(3)
        out += parse_allen_park(f.driver.find_element(By.TAG_NAME, "body").text, rink, m, win)
    return out


# ─── Source: Eddie Edgar FullCalendar (browser) ───────────────────────────────

def parse_eddie_edgar(text: str, rink: dict, m: Matcher, win: Window) -> list[Session]:
    out, cur = [], None
    date_pat = re.compile(r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2})")
    range_pat = re.compile(r"(\d{1,2}:\d{2}\s*[ap]m)\s*[-–]\s*(\d{1,2}:\d{2}\s*[ap]m)", re.I)
    inline_pat = re.compile(r"(\d{1,2}:\d{2}\s*[ap]m)\s*\(([^)]+)\)", re.I)
    for line in (l.strip() for l in text.split("\n") if l.strip()):
        dm = date_pat.search(line)
        if dm and not re.search(r"\d{1,2}:\d{2}", line):
            cur = datetime.strptime(dm.group(1), "%B %d, %Y").date()
            continue
        if not cur or not win.contains(cur):
            continue
        # e.g. "11:45 am - 01:15 pm\t\t(Sticks and Pucks), Sticks and Pucks"
        rm, im = range_pat.search(line), inline_pat.search(line)
        paren = re.search(r"\(([^)]+)\)", line)
        name = clean(paren.group(1)) if paren else clean(range_pat.sub("", line))
        if not (rm or im) or not m.ok(name):
            continue
        if rm:
            s, e = local(cur, rm.group(1)), local(cur, rm.group(2))
        else:                                          # start time only — S&P runs to 1:15
            s, e = local(cur, im.group(1)), local(cur, "1:15 PM")
        out.append(Session(rink["id"], rink["name"], name, s, e, rink["dist_mi"],
                           rink["url"], "Eddie Edgar calendar", rink.get("price")))
    uniq = {(x.start, x.name): x for x in out if sane(x.start, x.end)}
    return list(uniq.values())


def scrape_eddie_edgar(f: Fetcher, rink, m, win) -> list[Session]:
    from selenium.webdriver.common.by import By
    d = f.driver
    d.get(rink["url"])
    time.sleep(5)
    btns = d.find_elements(By.XPATH, "//button[contains(@class,'fc-listMonth') or contains(@class,'list') "
                                     "or contains(translate(text(),'LIST','list'),'list')]")
    if btns:
        btns[0].click()                                # list view (currently a full-year list)
        time.sleep(3)
    return parse_eddie_edgar(d.find_element(By.TAG_NAME, "body").text, rink, m, win)


# ─── iCal output ──────────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets."""
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        cb = ch.encode("utf-8")
        if len(cur) + len(cb) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += cb
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def short_name(rink: str) -> str:
    return re.split(r"\s[–—-]\s", rink)[-1].strip() if re.search(r"\s[–—-]\s", rink) else rink


def to_ical(sessions: list[Session]) -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Metro Detroit Hockey v3//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Metro Detroit Hockey Sessions", "X-WR-TIMEZONE:America/Detroit",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H",
        f"X-WR-CALDESC:Stick & Puck / Drop-In near Ann Arbor MI. Updated {now:%Y-%m-%d %H:%M} UTC.",
    ]
    for s in sorted(sessions, key=lambda x: (x.start, x.dist, x.rink)):
        uid = hashlib.md5(f"{s.rink_id}|{s.start.isoformat()}|{s.kind}".encode()).hexdigest() + "@mdh"
        desc = "\n".join(filter(None, [
            s.name, "",
            f"Rink: {s.rink}", f"~{s.dist} mi from Ann Arbor",
            f"Price: {s.price}" if s.price else None,
            f"Ice: {s.location}" if s.location else None,
            s.extra,
            f"Source: {s.source}", "",
            f"Info: {s.url}" if s.url else None,
            "Confirm with the rink — schedules change.",
        ]))
        lines += [
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_utc(now)}",
            f"DTSTART:{_utc(s.start)}", f"DTEND:{_utc(s.end)}",
            f"SUMMARY:{_esc(f'🏒 {s.kind} – {short_name(s.rink)}')}",
            f"DESCRIPTION:{_esc(desc)}", f"LOCATION:{_esc(s.rink)}",
        ]
        if s.url:
            lines.append(f"URL:{s.url}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


# ─── Main ─────────────────────────────────────────────────────────────────────

SCRAPERS = {
    "ice_finder": scrape_ice_finder,
    "bond": scrape_bond,
    "daysmart": scrape_daysmart,
    "ics": scrape_ics,
    "ezfacility": scrape_ezfacility,
    "finnly": scrape_finnly,
    "static_weekly": scrape_static,
    "jackson_vision": scrape_jackson,
    "scs_pdf": scrape_scs,
    "allen_park": scrape_allen_park,
    "eddie_edgar": scrape_eddie_edgar,
}


def dedupe(sessions: list[Session]) -> list[Session]:
    best = {}
    for s in sessions:
        key = (s.rink_id, s.start, s.kind)
        if key not in best or len(s.name) > len(best[key].name):
            best[key] = s
    return list(best.values())


def run(config: dict, win: Window, headless: bool, only: set[str] | None = None):
    defaults = config["defaults"]
    f = Fetcher(headless)
    sessions, status = [], []
    try:
        for rink in config["rinks"]:
            if not rink.get("active", True) or (only and rink["id"] not in only):
                continue
            fn = SCRAPERS.get(rink["source"])
            log(f"── {rink['name']}  [{rink['source']}]")
            st = {"id": rink["id"], "name": rink["name"], "source": rink["source"], "sessions": 0, "error": None}
            try:
                if fn is None:
                    raise RuntimeError(f"unknown source {rink['source']!r}")
                got = fn(f, rink, matcher_for(rink, defaults), win)
                st["sessions"] = len(got)
                sessions += got
                log(f"    → {len(got)} session(s)")
            except Exception as e:
                st["error"] = f"{e.__class__.__name__}: {e}"[:300]
                log(f"    ✗ {st['error']}")
            status.append(st)
    finally:
        f.close()
    return dedupe(sessions), status


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--output", default="hockey_sessions.ics")
    p.add_argument("--status", default="rink_status.json")
    p.add_argument("--config", default=str(HERE / "rinks.json"))
    p.add_argument("--headless", default="true")
    p.add_argument("--only", default="", help="comma-separated rink ids to run")
    a = p.parse_args()

    today = datetime.now(TZ).date()
    win = Window(today, today + timedelta(days=a.days - 1))
    config = json.loads(Path(a.config).read_text())
    only = {x.strip() for x in a.only.split(",") if x.strip()} or None

    log(f"Metro Detroit Hockey v3 — {win.start} through {win.end}\n")
    sessions, status = run(config, win, a.headless.lower() != "false", only)

    log(f"\nTotal: {len(sessions)} session(s) from {sum(1 for s in status if s['sessions'])} rink(s)\n")
    for s in sorted(sessions, key=lambda x: (x.start, x.dist)):
        t = s.start.astimezone(TZ)
        log(f"  {t:%a %m/%d %I:%M%p}  ~{s.dist:>2}mi  {s.kind:<12} {s.rink}")

    log("\nRink health:")
    for st in status:
        flag = "✗" if st["error"] else ("·" if st["sessions"] == 0 else "✓")
        log(f"  {flag} {st['sessions']:>3}  {st['name']}" + (f"  — {st['error']}" if st["error"] else ""))

    Path(a.output).write_text(to_ical(sessions), encoding="utf-8")
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": [win.start.isoformat(), win.end.isoformat()],
        "total_sessions": len(sessions),
        "rinks": status,
    }
    Path(a.status).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log(f"\n✓ Wrote {a.output} and {a.status}")


if __name__ == "__main__":
    main()
