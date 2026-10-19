"""
Offline tests for every parser in scraper.py, using samples captured from the
real sites on 2026-10-19. Run with:  python -m pytest -q
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scraper as S  # noqa: E402

CONFIG = json.loads((ROOT / "rinks.json").read_text())
DEFAULTS = CONFIG["defaults"]
RINKS = {r["id"]: r for r in CONFIG["rinks"]}
WIN = S.Window(date(2026, 10, 19), date(2026, 11, 1))
FIX = ROOT / "tests" / "fixtures"


def M(rink_id):
    return S.matcher_for(RINKS[rink_id], DEFAULTS)


def hm(s):
    """'Tue 10/20 10:30-11:50' style summary in Detroit time."""
    a, b = s.start.astimezone(S.TZ), s.end.astimezone(S.TZ)
    return f"{a:%a %m/%d %H:%M}-{b:%H:%M}"


# ── filters ──────────────────────────────────────────────────────────────────

def test_default_filter_keeps_hockey_and_drops_youth_and_figure():
    m = S.Matcher(DEFAULTS["include"], DEFAULTS["exclude"])
    keep = ["Stick And Puck", "Sticks and Pucks", "Sticks & Pucks ", "Adult Sticks & Pucks", "35+ Drop -In Hockey",
            "25+ Drop In Hockey Game/ Skills", "ADI", "18+ Drop-In Hockey", "Pickup Hockey", "Adult Drop-In",
            "Senior Sticks & Pucks:  T/Th", "ADULT DROP IN HOCKEY", "A DULT DROP IN HOCKEY", "Open Hockey", "Drop In"]
    drop = ["Public Skate", "Freestyle Drop-In - October 2026", "Stick &. Puck (5 - 13 year olds) - NO ADULTS",
            "Sticks & Pucks: 14-18", "Sticks & Pucks: 5-9", "Youth Sticks & Pucks", "HS Drop In",
            "Downriver Unified Drop In", "Adult Private Drop - In Hockey | Adult Drop - In", "Pucks & Parents",
            "Parent & Child Open Hockey", "Open Skate CANCELED", "Drop In Figure Skating"]
    assert [k for k in keep if not m.ok(k)] == []
    assert [d for d in drop if m.ok(d)] == []


# ── ice-finder ───────────────────────────────────────────────────────────────

ICE_FINDER_TEXT = """Novi Ice Arena Schedule (Next Week)
Stick And Puck
Tue, October 20 • 10:30 AM – 11:50 AM
NOVI ICE ARENA
42400 Nick Lidstrom Dr, Novi, MI 48375
$15.00
Public Skate
Tue, October 20 • 12:00 PM – 1:50 PM
NOVI ICE ARENA
42400 Nick Lidstrom Dr, Novi, MI 48375
$5.00
Stick And Puck
Wed, October 21 • 10:30 AM – 11:50 AM
NOVI ICE ARENA
$15.00
Stick And Puck
Tue, October 20 • 12:00 AM – 12:00 AM
NOVI ICE ARENA
Pickup Hockey
Sun, November 1 • 7:00 PM – 8:20 PM
NOVI ICE ARENA
Pickup Hockey
Mon, November 2 • 7:00 PM – 8:20 PM
"""


def test_ice_finder_full_month_names_and_junk_filtered():
    got = S.parse_ice_finder(ICE_FINDER_TEXT, RINKS["novi"], M("novi"), WIN)
    assert [hm(s) for s in got] == ["Tue 10/20 10:30-11:50", "Wed 10/21 10:30-11:50", "Sun 11/01 19:00-20:20"]
    assert got[0].price == "$15.00"


def test_ice_finder_html_to_lines():
    # Real markup: React comment nodes split the date line into pieces
    html = ('<html><body><main><div class="MuiListItemText-root"><span><p class="MuiTypography-root">Stick And Puck</p>'
            '<div class="MuiStack-root"><p class="MuiTypography-root">Tue, October 20<!-- --> <!-- -->• <!-- -->'
            '10:30 AM<!-- --> <!-- -->– <!-- -->11:50 AM</p></div><div class="MuiStack-root"><p>NOVI ICE ARENA</p>'
            '</div><div><svg><path d="M12 2"></path></svg><p>42400 Nick Lidstrom Dr, Novi, MI 48375</p></div></span>'
            '</div><div><p>$15.00</p></div></main></body></html>')
    got = S.parse_ice_finder(S.html_to_lines(html), RINKS["novi"], M("novi"), WIN)
    assert [hm(s) for s in got] == ["Tue 10/20 10:30-11:50"]


# ── Bond Sports ──────────────────────────────────────────────────────────────

def bond_ev(prog, name, d, s, e, space="North Rink", status="available"):
    return {"programName": prog, "eventName": name, "eventStartDate": d, "eventEndDate": d,
            "eventTimezone": "America/Detroit", "eventStartTime": s, "eventEndTime": e,
            "spaces": [{"spaceName": space}], "status": status}


def test_bond_chelsea_and_brighton():
    data = {"data": [
        bond_ev("Sticks and Pucks", "Sticks and Pucks", "2026/10/20", "10:00:00", "11:20:00"),
        bond_ev("Sticks and Pucks", "Sticks and Pucks", "2026/10/20", "10:00:00", "11:20:00"),  # dupe
        bond_ev("Freestyle Drop-in", "Freestyle Drop-In - October 2026 - Tue - 06:00 am", "2026/10/20", "06:00:00", "06:30:00"),
        bond_ev("25+ Adult drop in Hockey", "25+ Drop In Hockey Game/ Skills", "2026/10/22", "21:00:00", "22:20:00"),
        bond_ev("Adult Private Drop - In Hockey", "Adult Drop - In", "2026/10/23", "21:00:00", "22:00:00"),
        bond_ev("Public Skate", "Public Skate", "2026/10/20", "11:00:00", "12:20:00"),
        bond_ev("Sticks and Pucks", "Sticks and Pucks", "2026/11/05", "10:00:00", "11:20:00"),  # outside window
    ]}
    got = S.parse_bond(data, RINKS["biggby-chelsea"], M("biggby-chelsea"), WIN)
    assert [(hm(s), s.kind) for s in got] == [("Tue 10/20 10:00-11:20", "Stick & Puck"),
                                             ("Thu 10/22 21:00-22:20", "Drop-In")]
    assert got[0].location == "North Rink"


def test_bond_hazel_park_name_suffix_trimmed():
    data = {"data": [bond_ev("Stick & Puck", "Stick & Puck - Fri - 10:00 am", "2026/10/23", "10:00:00", "11:30:00"),
                     bond_ev("18+ Drop-In Hockey", "18+ Drop-In Hockey", "2026/10/20", "11:30:00", "13:00:00"),
                     bond_ev("Paul Demees Private Drop-In", "Opha Private Drop-In", "2026/10/21", "20:00:00", "21:00:00")]}
    got = S.parse_bond(data, RINKS["hazel-park"], M("hazel-park"), WIN)
    assert sorted(s.name for s in got) == ["18+ Drop-In Hockey", "Stick & Puck"]


# ── DaySmart ─────────────────────────────────────────────────────────────────

def ds_ev(desc, start_gmt, end_gmt, hteam=None):
    return {"attributes": {"desc": desc, "start_gmt": start_gmt, "end_gmt": end_gmt, "hteam_id": hteam,
                           "start": "x", "end": "x"}}


def test_daysmart_tam_o_shanter_names_and_youth_filter():
    pages = [{"data": [
        ds_ev("ADI", "2026-10-19T16:00:00", "2026-10-19T17:50:00", 900),
        ds_ev("Stick &. Puck (5 - 13 year olds) - NO ADULTS", "2026-10-19T20:00:00", "2026-10-19T20:50:00"),
        ds_ev("HS Drop In ", "2026-10-25T01:00:00", "2026-10-25T02:50:00", 900),
        ds_ev("Sylvania Tam-O-Shanter Learn to Skate programs ... hockey", "2026-10-19T16:00:00", "2026-10-19T17:00:00"),
    ], "included": [{"type": "teams", "id": "900", "attributes": {"name": "October 2026 - Adult Drop In Hockey"}}]}]
    got = S.parse_daysmart(pages, RINKS["tam-o-shanter"], M("tam-o-shanter"), WIN)
    assert [(hm(s), s.name) for s in got] == [("Mon 10/19 12:00-13:50", "Adult Drop In Hockey")]


def test_daysmart_trenton_team_names():
    pages = [{"data": [
        ds_ev("", "2026-10-19T13:00:00", "2026-10-19T14:30:00", 5179),
        ds_ev("", "2026-10-20T14:00:00", "2026-10-20T15:30:00", 5180),
        ds_ev("", "2026-10-30T15:00:00", "2026-10-30T16:00:00", 5200),
        ds_ev("", "2026-10-20T22:00:00", "2026-10-20T23:00:00", 5186),
    ], "included": [
        {"type": "teams", "id": "5179", "attributes": {"name": "Adult Drop-In"}},
        {"type": "teams", "id": "5180", "attributes": {"name": "Senior Sticks & Pucks:  T/Th"}},
        {"type": "teams", "id": "5200", "attributes": {"name": "Sticks & Pucks: 14-18"}},
        {"type": "teams", "id": "5186", "attributes": {"name": "Open Skate"}},
    ]}]
    got = S.parse_daysmart(pages, RINKS["trenton"], M("trenton"), WIN)
    assert [(hm(s), s.name) for s in got] == [("Mon 10/19 09:00-10:30", "Adult Drop-In"),
                                             ("Tue 10/20 10:00-11:30", "Senior Sticks & Pucks")]


# ── iCal (Google Calendar / CivicPlus / SportsEngine) ────────────────────────

def test_ics_royal_oak_recurrences_overrides_and_exdates():
    got = S.parse_ics((FIX / "royal_oak_gcal.ics").read_bytes(), RINKS["royal-oak"], M("royal-oak"), WIN)
    got = sorted(got, key=lambda s: s.start)
    summary = [f"{hm(s)} {s.kind}" for s in got]
    # Thursday series is 9:30 but 10/22 and 10/29 were moved to 9:00 (RECURRENCE-ID overrides)
    assert "Thu 10/22 09:00-10:20 Stick & Puck" in summary
    assert "Thu 10/22 09:30-10:50 Stick & Puck" not in summary
    assert "Thu 10/29 09:00-10:20 Stick & Puck" in summary
    # Friday drop-in on 10/23 and 10/30 (10/16 EXDATE is outside the window anyway)
    assert "Fri 10/23 11:30-12:50 Drop-In" in summary
    assert "Fri 10/30 11:30-12:50 Drop-In" in summary
    # One-off UTC events
    assert "Sun 10/25 10:45-12:05 Stick & Puck" in summary
    # Public Skate is not included
    assert not any("12:00-13:00" in x for x in summary)


WESTLAND_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CivicPlus//Calendar//EN
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20261019T173000
DTEND;TZID=America/New_York:20261019T183000
SUMMARY:Sticks and Pucks
UID:a1
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20261026T173000
DTEND;TZID=America/New_York:20261026T183000
SUMMARY:Sticks and Pucks
UID:a2
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20261023T200000
DTEND;TZID=America/New_York:20261023T212000
SUMMARY:Mike Modano Open Skate
UID:a3
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20261030T173000
DTEND;TZID=America/New_York:20261030T183000
SUMMARY:Sticks and Pucks CANCELED
UID:a4
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261026
SUMMARY:Sticks and Pucks all day placeholder
UID:a5
END:VEVENT
END:VCALENDAR
"""


def test_ics_civicplus_westland():
    got = S.parse_ics(WESTLAND_ICS, RINKS["westland"], M("westland"), WIN)
    assert sorted(hm(s) for s in got) == ["Mon 10/19 17:30-18:30", "Mon 10/26 17:30-18:30"]


def test_ics_sportsengine_quirks_and_empty_feed():
    raw = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME::Arctic Edge Ice Arena Events\r\n"
           "X-WR-TIMEZONE::America/New_York\r\nMETHOD::PUBLISH\r\nEND:VCALENDAR\r\n")
    assert S.parse_ics(raw, RINKS["canton"], M("canton"), WIN) == []


# ── EZFacility ───────────────────────────────────────────────────────────────

def test_ezfacility_big_boy():
    items = [
        {"title": "Open Skate", "start": "2026-10-19T18:40:00.0000000", "end": "2026-10-19T19:40:00.0000000"},
        {"title": "Stick & Puck", "start": "2026-10-19T19:00:00.0000000", "end": "2026-10-19T20:30:00.0000000", "resourceName": "Rink 5"},
        {"title": "Stick & Puck", "start": "2026-10-19T21:10:00.0000000", "end": "2026-10-19T22:40:00.0000000", "resourceName": "Rink 4"},
    ]
    got = S.parse_ezfacility(items, RINKS["big-boy-fraser"], M("big-boy-fraser"), WIN)
    assert [(hm(s), s.location) for s in got] == [("Mon 10/19 19:00-20:30", "Rink 5"), ("Mon 10/19 21:10-22:40", "Rink 4")]


# ── FinnlyConnect ────────────────────────────────────────────────────────────

def test_finnly_hartland():
    items = [
        {"EventStartTime": "2026-10-19T12:30:00", "EventEndTime": "2026-10-19T13:50:00", "AccountName": "Sticks & Pucks",
         "Description": "Activity: Sticks & Pucks ", "EventTypeName": "Public Session", "FacilityName": "Pond", "Closed": False},
        {"EventStartTime": "2026-10-19T13:00:00", "EventEndTime": "2026-10-19T14:20:00", "AccountName": "Public Skate",
         "Description": "Activity: Public Skate", "EventTypeName": "Public Session", "FacilityName": "Stadium", "Closed": False},
        {"EventStartTime": "2026-10-19T06:00:00", "EventEndTime": "2026-10-19T08:20:00", "AccountName": "Harmony",
         "Description": "Freestyle", "EventTypeName": "Figure Skating", "FacilityName": "Stadium", "Closed": False},
        {"EventStartTime": "2026-11-09T13:15:00", "EventEndTime": "2026-11-09T14:35:00", "AccountName": "Sticks & Pucks",
         "Description": "Activity: Sticks & Pucks", "EventTypeName": "Public Session", "FacilityName": "Pond", "Closed": False},
    ]
    html = ("<script>\n let _onlineScheduleList;\n var facilityDataSource = new kendo.data.DataSource({\n"
            " data: [{ FacilityName: \"View All\", FacilityId: -1 }, ..._facilityList]\n });\n"
            " $(window).load(function () {\n _onlineScheduleList = "
            + json.dumps(items) + ";\n _eventTypeResourceList = [];\n});</script>")
    got = S.parse_finnly(html, RINKS["hartland"], M("hartland"), WIN)
    assert [(hm(s), s.name, s.location) for s in got] == [("Mon 10/19 12:30-13:50", "Sticks & Pucks", "Pond")]


# ── fixed weekly schedules ───────────────────────────────────────────────────

def test_static_weekly_wyandotte_and_southfield_windows():
    w = S.build_static(RINKS["wyandotte"], WIN)
    assert len(w) == 6 and {s.start.astimezone(S.TZ).strftime("%a %H:%M") for s in w} == {"Tue 09:00", "Wed 09:00", "Thu 09:00"}
    sf = S.build_static(RINKS["southfield"], S.Window(date(2026, 12, 21), date(2027, 1, 3)))
    # Mon 12/21 & 12/28, Wed 12/23 & 12/30, Sun 12/27 — Fridays ended 12/18
    assert sorted(hm(s) for s in sf) == ["Mon 12/21 10:30-11:50", "Mon 12/28 10:30-11:50", "Sun 12/27 12:00-13:20",
                                         "Wed 12/23 10:30-11:50", "Wed 12/30 10:30-11:50"]


def test_static_late_night_usa_hockey_arena():
    got = S.build_static(RINKS["usa-hockey-arena"], WIN)
    assert hm(got[0]) == "Tue 10/20 22:10-23:30" and len(got) == 4


# ── Allen Park ───────────────────────────────────────────────────────────────

ALLEN_PARK_TEXT = ("October 19, 2026\nMonday\n\n9:00am - 10:00am\t\tSenior Exercise\n10:00am - 12:00pm\t\tOpen Skate\n"
                   "10:00am - 11:00am\t\tPUBLIC ACCURACY TEST\n12:30pm - 3:30pm\t\tSticks and Pucks\n5:15pm - 6:15pm\t\tYoga\n\n"
                   "October 20, 2026\nTuesday\n\n10:00am - 1:00pm\t\tOpen Skate\n1:30pm - 3:30pm\t\tSticks and Pucks\n\n"
                   "October 21, 2026\nWednesday\n\n10:00am - 12:00pm\t\tOpen Skate\n12:30pm - 5:00pm\t\tSticks and Pucks\n"
                   "1:00pm - 3:00pm\t\tPing Pong for Seniors\n")


def test_allen_park_uses_the_sessions_own_time():
    got = sorted(S.parse_allen_park(ALLEN_PARK_TEXT, RINKS["allen-park"], M("allen-park"), WIN), key=lambda s: s.start)
    assert [hm(s) for s in got] == ["Mon 10/19 12:30-15:30", "Tue 10/20 13:30-15:30", "Wed 10/21 12:30-17:00"]


# ── Eddie Edgar ──────────────────────────────────────────────────────────────

EDDIE_TEXT = ("October 18, 2026\nSunday\n\n01:30 pm - 02:50 pm\t\t(Open Skating) @ Eddie Edgar Arena arena, Open Skating\n\n"
              "October 20, 2026\nTuesday\n\n11:45 am - 01:15 pm\t\t(Sticks and Pucks), Sticks and Pucks\n"
              "01:30 pm - 02:50 pm\t\t(Open Skating) @ Eddie Edgar Arena arena, Open Skating\n\n"
              "October 27, 2026\nTuesday\n\n11:45 am - 01:15 pm\t\t(Sticks and Pucks), Sticks and Pucks\n\n"
              "November 3, 2026\nTuesday\n\n11:45 am - 01:15 pm\t\t(Sticks and Pucks), Sticks and Pucks\n")


def test_eddie_edgar_list_year_format():
    got = sorted(S.parse_eddie_edgar(EDDIE_TEXT, RINKS["eddie-edgar"], M("eddie-edgar"), WIN), key=lambda s: s.start)
    assert [hm(s) for s in got] == ["Tue 10/20 11:45-13:15", "Tue 10/27 11:45-13:15"]


# ── St Clair Shores PDF grid ─────────────────────────────────────────────────

def _words(items):
    """pdf.js-style (text, x, top) phrases → pdfplumber-style word dicts (~4.6pt per char)."""
    out = []
    for text, x, top in items:
        cx = x
        for tok in text.split(" "):
            if tok:
                out.append({"text": tok, "x0": cx, "x1": cx + 4.6 * len(tok), "top": top})
            cx += 4.6 * (len(tok) + 1)
    return out


SCS_ITEMS = [
    ("Youth Stick & Puck: $10 per person/ Goalies Free. 17 & Under only. Limited to 20 skaters", 308, 97),
    ("Sun", 57, 139), ("Mon", 161, 139), ("Tue", 272, 139), ("Wed", 373, 139),
    ("Thu", 480, 139), ("Fri", 592, 139), ("Sat", 701, 139),
    # Sat 3: public skate first, then adult S&P — the S&P must get ITS time, not the first one in the cell
    ("3", 661, 165), ("PUBLIC SKATE", 683, 180), ("3:00 - 5:00 P", 689, 192),
    ("ADULT STICK & PUCK", 673, 214), ("6:30 - 8:00 P", 689, 225),
    # Wed 21: drop-in (with the "A" split off like the real PDF) then public skate
    ("17", 661, 329), ("NO", 700, 357), ("PUBLIC SKATE", 674, 376),
    ("18", 24, 420), ("19", 129, 420), ("20", 237, 420),
    ("21", 345, 420), ("A", 352, 443), ("DULT DROP IN HOCKEY", 357, 443), ("9:30 - 11:30 A", 371, 457),
    ("PUBLIC SKATE", 369, 471), ("1:30 - 3:00 P", 373, 485),
    ("22", 446, 420), ("PUCKS & PARENTS 2:00 - 3:00 P", 447, 478),
    ("23", 554, 420), ("ADULT SKATE 9:00 - 10:00 P", 558, 480),
    ("24", 661, 420), ("PUBLIC SKATE", 686, 438), ("3:00 - 5:00 P", 689, 450),
    ("ADULT STICK & PUCK", 673, 472), ("7:30 - 9:00 P", 689, 483),
    ("25", 24, 511), ("26", 129, 511), ("27", 237, 511),
    ("28", 345, 511), ("A", 352, 534), ("DULT DROP IN HOCKEY", 357, 534), ("9:30 - 11:30 A", 371, 548),
    ("29", 446, 511), ("30", 554, 511), ("31", 661, 511), ("PUBLIC SKATE", 680, 535), ("3:00 - 5:00 P", 684, 549),
]


def test_scs_pdf_grid():
    got = S.parse_calendar_words(_words(SCS_ITEMS), 612, 2026, 10, RINKS["st-clair-shores"], M("st-clair-shores"), WIN)
    got = sorted(got, key=lambda s: s.start)
    assert [(hm(s), s.kind) for s in got] == [
        ("Wed 10/21 09:30-11:30", "Drop-In"),
        ("Sat 10/24 19:30-21:00", "Stick & Puck"),
        ("Wed 10/28 09:30-11:30", "Drop-In"),
    ]
    assert got[0].name == "Adult Drop In Hockey"


def test_time_range_meridiem_inference():
    m = S.TIME_RANGE.search("PARENT & TOT 11:00 A - 12:00 P")
    assert S._range_to_times(m) == ("11:00 AM", "12:00 PM")
    m = S.TIME_RANGE.search("11:30 - 1:00 P")
    assert S._range_to_times(m) == ("11:30 AM", "1:00 PM")
    m = S.TIME_RANGE.search("6:30-8:00 P")
    assert S._range_to_times(m) == ("6:30 PM", "8:00 PM")


def test_scs_finds_pdf_link_even_in_hidden_tab():
    html = ('<div class="tab hidden" style="display:none"><a href="/DocumentCenter/View/13352">'
            'October 2026 Public Skate Calendar</a><a href="/DocumentCenter/View/1">Title VI Plan</a></div>')
    links = S.find_month_pdf_links(html, "https://www.scsmi.net/324/Civic-Arena")
    assert links == {(2026, 10): "https://www.scsmi.net/DocumentCenter/View/13352"}


# ── Jackson ──────────────────────────────────────────────────────────────────

def test_jackson_image_detection_and_json():
    html = ('<img src="https://www.optimisticearena.com/wp-content/uploads/2023/01/logo.png">'
            '<img src="https://www.optimisticearena.com/wp-content/uploads/2026/10/october-skating-schedule-2026.jpg">'
            '<img src="https://www.optimisticearena.com/wp-content/uploads/2026/10/November-Calendar-2026-scaled.jpg">')
    html += ('<img src="data:image/gif;base64,R0lGOD" data-src="https://www.optimisticearena.com/wp-content/uploads/'
             '2026/10/october-skating-schedule-2026-300x232.jpg" srcset="https://www.optimisticearena.com/wp-content/'
             'uploads/2026/10/october-skating-schedule-2026-1024x791.jpg 1024w">')
    imgs = S.find_month_images(html, "")
    assert imgs[(2026, 10)].endswith("october-skating-schedule-2026.jpg")
    assert imgs[(2026, 11)].endswith("November-Calendar-2026-scaled.jpg")
    assert S.parse_vision_json('Here you go:\n```json\n[{"day": 5, "type": "Stick & Puck", "start": "3:30 PM", "end": "4:50 PM"}]\n```') \
        == [{"day": 5, "type": "Stick & Puck", "start": "3:30 PM", "end": "4:50 PM"}]


# ── iCal output ──────────────────────────────────────────────────────────────

def test_ical_output_round_trips_and_handles_dst():
    import icalendar
    ses = [
        S.Session("x", "Some Rink – Novi", "Stick And Puck", S.local(date(2026, 10, 30), "10:30 AM"),
                  S.local(date(2026, 10, 30), "11:50 AM"), 26, "https://example.com/" + "a" * 120, "test", "$15"),
        S.Session("x", "Some Rink – Novi", "Pickup Hockey", S.local(date(2026, 11, 2), "7:00 PM"),
                  S.local(date(2026, 11, 2), "8:20 PM"), 26, "", "test"),
    ]
    out = S.to_ical(ses)
    assert all(len(l.encode()) <= 75 for l in out.split("\r\n"))
    cal = icalendar.Calendar.from_ical(out)
    evs = [e for e in cal.walk("VEVENT")]
    assert len(evs) == 2
    # EDT (UTC-4) before Nov 1, EST (UTC-5) after
    assert evs[0]["DTSTART"].dt == datetime(2026, 10, 30, 14, 30, tzinfo=timezone.utc)
    assert evs[1]["DTSTART"].dt == datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
    assert str(evs[0]["SUMMARY"]) == "🏒 Stick & Puck – Novi"
    assert str(evs[1]["SUMMARY"]) == "🏒 Drop-In – Novi"


def test_every_active_rink_has_a_known_source():
    for r in CONFIG["rinks"]:
        assert r["source"] in S.SCRAPERS, r["id"]
        assert r["id"] and r["name"] and isinstance(r["dist_mi"], int)
    assert len({r["id"] for r in CONFIG["rinks"]}) == len(CONFIG["rinks"])
