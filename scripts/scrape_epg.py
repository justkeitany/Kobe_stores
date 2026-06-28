#!/usr/bin/env python3
"""
IPTV EPG scraper → XMLTV.

Builds an XMLTV `epg.xml` for two channels from public schedule sources:

  * Bounce TV     — bouncetv.com WordPress AJAX endpoint `get_epg_schedules`
                    (returns clean JSON per date; looped over today + 7 days).
  * Animal Planet — streamingtvguides.com/Channel/APL (static HTML, multi-day).
                    tvinsider.com is Cloudflare-blocked (403), so this is used.

All source times are US Eastern (ET); they're localized with pytz
(America/New_York, so DST is handled) and emitted as UTC. Each programme's stop
time is the next programme's start on the same channel; the last programme of a
day rolls into the first of the next day. The final programme falls back to a
+30min stop.

Output: /var/iptv/epg/epg.xml   (override with EPG_OUT env var)
Run:    python3 scrape_epg.py
"""
import html
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests
import pytz
from bs4 import BeautifulSoup

ET = pytz.timezone("America/New_York")
UTC = pytz.utc
OUT_PATH = os.environ.get("EPG_OUT", "/var/iptv/epg/epg.xml")
DAYS = 8  # today + 7

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

CHANNELS = {
    "BounceTV": {
        "name": "Bounce TV",
        "icon": "https://storage.googleapis.com/btvwp-uploads/2018/02/5bfb1301-bounce_adjusted1.png",
    },
    "AnimalPlanet": {
        "name": "Animal Planet",
        "icon": "https://www.tvinsider.com/wp-content/uploads/2022/07/animal-planet.png",
    },
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def _get(s: requests.Session, *args, **kw):
    """GET/POST with a couple of retries; returns the response or None."""
    method = kw.pop("method", "GET")
    for attempt in range(3):
        try:
            r = s.request(method, *args, timeout=25, **kw)
            if r.status_code == 200:
                return r
            print(f"  ! HTTP {r.status_code} (attempt {attempt+1})", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  ! {e} (attempt {attempt+1})", file=sys.stderr)
        time.sleep(2)
    return None


# ── Bounce TV ───────────────────────────────────────────────────────────────

BOUNCE_AJAX = "https://www.bouncetv.com/wp-admin/admin-ajax.php"
BOUNCE_DEFAULT_IMG = ("https://storage.googleapis.com/btvwp-uploads/2020/02/"
                      "9c067e39-bounce-place-holder-image_1280x720.jpg")


def scrape_bounce(s: requests.Session) -> list[dict]:
    """One programme dict per airing across today + 7 days (ET start times)."""
    progs: list[dict] = []
    today = datetime.now(ET).date()
    for offset in range(DAYS):
        d = today + timedelta(days=offset)
        ds = d.strftime("%Y-%m-%d")
        r = _get(s, BOUNCE_AJAX, method="POST", data={
            "action": "get_epg_schedules",
            "network": "bouncetv",
            "date": ds,
            "default_image": BOUNCE_DEFAULT_IMG,
        })
        if not r:
            print(f"  Bounce {ds}: fetch failed", file=sys.stderr)
            continue
        try:
            rows = r.json()
        except ValueError:
            print(f"  Bounce {ds}: bad JSON", file=sys.stderr)
            continue
        for row in rows:
            t = (row.get("start_time") or "").strip()
            mer = (row.get("meridiem") or "").strip()
            if not t or not mer:
                continue
            try:
                naive = datetime.strptime(f"{ds} {t} {mer}", "%Y-%m-%d %I:%M %p")
            except ValueError:
                continue
            start = ET.localize(naive).astimezone(UTC)
            program = (row.get("program") or row.get("title_name") or "").strip()
            series = (row.get("title_name") or "").strip()
            # title_name is the series; if it differs from the season-tagged
            # `program`, there's no separate episode title in this feed.
            progs.append({
                "start": start,
                "title": program or series or "Bounce TV",
                "sub_title": "",
                "desc": (row.get("title_synopsis") or "").strip(),
                "rating": (row.get("rating") or "").strip(),
                "icon": (row.get("image") or "").strip(),
            })
        print(f"  Bounce {ds}: {len(rows)} airings")
    return progs


# ── Animal Planet (streamingtvguides.com) ───────────────────────────────────

APL_URL = "https://streamingtvguides.com/Channel/APL"
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _parse_card_date(span_text: str, base_year: int, base_month: int) -> datetime | None:
    """'Sat, Jun 27' → naive date; rolls year forward across a Dec→Jan boundary."""
    m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})", span_text)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1))
    if not mon:
        return None
    day = int(m.group(2))
    year = base_year + 1 if mon < base_month else base_year
    try:
        return datetime(year, mon, day)
    except ValueError:
        return None


def scrape_animal_planet(s: requests.Session) -> list[dict]:
    r = _get(s, APL_URL)
    if not r:
        print("  Animal Planet: fetch failed", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    now = datetime.now(ET)
    progs: list[dict] = []
    for card in soup.select("article.program-card"):
        timebox = card.select_one(".program-time")
        if not timebox:
            continue
        span = timebox.find("span")
        strong = timebox.find("strong")
        if not span or not strong:
            continue
        d = _parse_card_date(span.get_text(" ", strip=True), now.year, now.month)
        if not d:
            continue
        tm = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", strong.get_text(" ", strip=True), re.I)
        if not tm:
            continue
        try:
            t = datetime.strptime(tm.group(1).upper().replace(" ", ""), "%I:%M%p")
        except ValueError:
            continue
        naive = d.replace(hour=t.hour, minute=t.minute)
        start = ET.localize(naive).astimezone(UTC)

        main = card.select_one(".program-card-main")
        title = (main.find("h3").get_text(strip=True) if main and main.find("h3") else "Animal Planet")
        sub = main.select_one("p.muted")
        sub_title = sub.get_text(strip=True) if sub else ""
        # description: the non-muted <p> in the card body
        desc = ""
        if main:
            for p in main.find_all("p"):
                cls = p.get("class") or []
                if "muted" not in cls and p.get_text(strip=True):
                    desc = p.get_text(" ", strip=True)
                    break
        # rating + logo from the detail dialog
        rating = ""
        dlg = card.find_next("dialog", class_="program-dialog")
        if dlg:
            grid = dlg.select_one(".program-meta-grid")
            if grid:
                dts = grid.find_all("dt")
                dds = grid.find_all("dd")
                for dt, dd in zip(dts, dds):
                    if dt.get_text(strip=True).lower() == "rating":
                        rating = dd.get_text(strip=True)
                        break
        rating = re.sub(r"^VCHIP-", "", rating).strip()  # 'VCHIP-TV-PG' → 'TV-PG'

        progs.append({
            "start": start,
            "title": title,
            "sub_title": sub_title,
            "desc": desc,
            "rating": rating,
            "icon": CHANNELS["AnimalPlanet"]["icon"],
        })
    print(f"  Animal Planet: {len(progs)} airings")
    return progs


# ── XMLTV assembly ──────────────────────────────────────────────────────────

def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%d%H%M%S +0000")


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def assign_stops(progs: list[dict]) -> list[dict]:
    """Sort by start; stop = next programme's start (last → +30min)."""
    progs = sorted(progs, key=lambda p: p["start"])
    for i, p in enumerate(progs):
        if i + 1 < len(progs):
            p["stop"] = progs[i + 1]["start"]
        else:
            p["stop"] = p["start"] + timedelta(minutes=30)
        # guard: never emit a non-positive duration (dupe start times)
        if p["stop"] <= p["start"]:
            p["stop"] = p["start"] + timedelta(minutes=30)
    return progs


def build_xml(by_channel: dict[str, list[dict]]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
           '<tv generator-info-name="IPTV-EPG-Scraper">']
    for cid, meta in CHANNELS.items():
        out.append(f'  <channel id="{cid}">')
        out.append(f'    <display-name>{_esc(meta["name"])}</display-name>')
        out.append(f'    <icon src="{_esc(meta["icon"])}"/>')
        out.append('  </channel>')
    for cid in CHANNELS:
        for p in by_channel.get(cid, []):
            out.append(f'  <programme start="{_fmt(p["start"])}" stop="{_fmt(p["stop"])}" channel="{cid}">')
            out.append(f'    <title>{_esc(p["title"])}</title>')
            if p.get("sub_title"):
                out.append(f'    <sub-title>{_esc(p["sub_title"])}</sub-title>')
            if p.get("desc"):
                out.append(f'    <desc>{_esc(p["desc"])}</desc>')
            if p.get("icon"):
                out.append(f'    <icon src="{_esc(p["icon"])}"/>')
            if p.get("rating"):
                out.append(f'    <rating><value>{_esc(p["rating"])}</value></rating>')
            out.append('  </programme>')
    out.append('</tv>')
    return "\n".join(out) + "\n"


def main() -> int:
    s = _session()
    print("Scraping Bounce TV…")
    bounce = assign_stops(scrape_bounce(s))
    print("Scraping Animal Planet…")
    animal = assign_stops(scrape_animal_planet(s))

    if not bounce and not animal:
        print("ERROR: no programmes scraped from either source — keeping old file.",
              file=sys.stderr)
        return 1

    xml = build_xml({"BounceTV": bounce, "AnimalPlanet": animal})
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(xml)
    os.replace(tmp, OUT_PATH)  # atomic: nginx never serves a half-written file
    print(f"Wrote {OUT_PATH}: BounceTV={len(bounce)} AnimalPlanet={len(animal)} "
          f"programmes ({datetime.now(UTC).isoformat()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
