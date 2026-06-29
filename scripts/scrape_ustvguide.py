#!/usr/bin/env python3
"""
us-tv-guide.com EPG scraper → XMLTV.

Builds an XMLTV file for the entire us-tv-guide.com catalogue (~240 US
channels), today + the next N-1 days, and writes it where the panel's nginx
serves it. The panel registers the served file as an EpgSource and its existing
automap maps each channel to a stream by display-name — so no ingestion code is
added; this script only produces a standard XMLTV feed.

Why us-tv-guide.com: it returns plain HTML (no Cloudflare, unlike tvinsider),
and every programme row carries a `data-ts` UNIX timestamp, so start times are
exact UTC with no timezone/DST guesswork. Stop = the next programme's start on
the same channel; the final programme falls back to start + its listed duration.

Page scheme:
  index            https://us-tv-guide.com/tv-guide-usa/      (all channel links)
  channel (today)  https://us-tv-guide.com/tv-guide-<slug>/
  channel (+1..6)  https://us-tv-guide.com/tv-guide-<slug>/<weekday>/   (e.g. /friday/)

Output: /var/iptv/epg/ustv.xml   (override with USTV_OUT env var)
Run:    python3 scrape_ustvguide.py [--days 7] [--workers 8] [--limit N]
"""
import argparse
import html as H
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

BASE = "https://us-tv-guide.com"
INDEX = f"{BASE}/tv-guide-usa/"
OUT_PATH = os.environ.get("USTV_OUT", "/var/iptv/epg/ustv.xml")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _get(s: requests.Session, url: str, tries: int = 3) -> str | None:
    """GET with a few polite retries/backoff. Returns text or None."""
    for i in range(tries):
        try:
            r = s.get(url, timeout=25)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None  # channel has no page for that day
            # 429/5xx → back off and retry
        except requests.RequestException:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def channel_catalogue(s: requests.Session) -> dict[str, str]:
    """{slug: display_name} for every channel linked on the index page."""
    doc = _get(s, INDEX)
    if not doc:
        log("FATAL: could not fetch index")
        return {}
    soup = BeautifulSoup(doc, "html.parser")
    out: dict[str, str] = {}
    for a in soup.select('a[href^="/tv-guide-"]'):
        href = a.get("href", "")
        m = re.fullmatch(r"/tv-guide-([a-z0-9-]+)/", href)
        if not m:
            continue
        slug = m.group(1)
        if slug == "usa":          # the index page itself
            continue
        name = a.get_text(" ", strip=True)
        if slug not in out and name:
            out[slug] = name
    return out


def _parse_duration(text: str) -> int:
    """'3h 4m' / '28m' / '1h' → minutes (0 if unparseable)."""
    h = re.search(r"(\d+)\s*h", text or "")
    m = re.search(r"(\d+)\s*m", text or "")
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def _parse_page(doc: str) -> list[dict]:
    """Extract programmes from one channel/day page.

    Each .show-row carries the start as a UNIX ts (data-ts), so no timezone math.
    """
    soup = BeautifulSoup(doc, "html.parser")
    progs: list[dict] = []
    for row in soup.select(".show-row"):
        ts_el = row.select_one(".show-time span[data-ts]")
        if not ts_el or not ts_el.get("data-ts", "").isdigit():
            continue
        title_el = row.select_one(".show-title-link") or row.select_one(".show-title")
        if not title_el:
            continue
        dur_el = row.select_one(".time-dur")
        desc_el = row.select_one(".show-desc")
        genre_el = row.select_one(".genre-tag")
        progs.append({
            "start": int(ts_el["data-ts"]),
            "dur": _parse_duration(dur_el.get_text(" ", strip=True) if dur_el else ""),
            "title": title_el.get_text(" ", strip=True),
            "desc": desc_el.get_text(" ", strip=True) if desc_el else "",
            "genre": genre_el.get_text(" ", strip=True) if genre_el else "",
        })
    return progs


def _day_urls(slug: str, days: int) -> list[str]:
    """Today's base page + the next (days-1) weekday-named pages."""
    urls = [f"{BASE}/tv-guide-{slug}/"]
    today = date.today()
    for d in range(1, days):
        wd = (today + timedelta(days=d)).strftime("%A").lower()
        urls.append(f"{BASE}/tv-guide-{slug}/{wd}/")
    return urls


def scrape_channel(slug: str, days: int) -> list[dict]:
    """All programmes for a channel across the window, deduped by start ts."""
    s = _session()
    by_ts: dict[int, dict] = {}
    for url in _day_urls(slug, days):
        doc = _get(s, url)
        if not doc:
            continue
        for p in _parse_page(doc):
            by_ts.setdefault(p["start"], p)   # first seen wins; dedup overlap
    return sorted(by_ts.values(), key=lambda p: p["start"])


def _xmltv_time(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def build_xmltv(catalogue: dict[str, str], programmes: dict[str, list[dict]]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<tv generator-info-name="iptv-panel us-tv-guide scraper">']
    # Channels first (XMLTV convention). Only those with programmes.
    for slug, name in catalogue.items():
        if not programmes.get(slug):
            continue
        cid = f"ustv.{slug}"
        lines.append(f'  <channel id="{escape(cid)}">')
        lines.append(f'    <display-name>{escape(name)}</display-name>')
        lines.append('  </channel>')
    # Programmes.
    for slug, progs in programmes.items():
        if not progs:
            continue
        cid = f"ustv.{slug}"
        for i, p in enumerate(progs):
            start = p["start"]
            # Stop = next programme's start; else fall back to listed duration
            # (or +30min when even that is missing) so the box always has width.
            if i + 1 < len(progs):
                stop = progs[i + 1]["start"]
            else:
                stop = start + (p["dur"] * 60 if p["dur"] else 1800)
            title = escape(H.unescape(p["title"]) or "No information")
            lines.append(f'  <programme start="{_xmltv_time(start)}" '
                         f'stop="{_xmltv_time(stop)}" channel="{escape(cid)}">')
            lines.append(f'    <title lang="en">{title}</title>')
            if p["desc"]:
                lines.append(f'    <desc lang="en">{escape(H.unescape(p["desc"]))}</desc>')
            if p["genre"]:
                lines.append(f'    <category lang="en">{escape(H.unescape(p["genre"]))}</category>')
            lines.append('  </programme>')
    lines.append('</tv>')
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="today + (days-1) ahead")
    ap.add_argument("--workers", type=int, default=8, help="concurrent channels")
    ap.add_argument("--limit", type=int, default=0, help="cap channels (testing)")
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()

    s = _session()
    catalogue = channel_catalogue(s)
    if not catalogue:
        return 1
    if args.limit:
        catalogue = dict(list(catalogue.items())[: args.limit])
    log(f"channels: {len(catalogue)} | days: {args.days} | workers: {args.workers}")

    programmes: dict[str, list[dict]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(scrape_channel, slug, args.days): slug for slug in catalogue}
        for fut in as_completed(futs):
            slug = futs[fut]
            try:
                programmes[slug] = fut.result()
            except Exception as e:
                programmes[slug] = []
                log(f"  [FAIL] {slug}: {e}")
            done += 1
            if done % 25 == 0 or done == len(catalogue):
                log(f"  {done}/{len(catalogue)} channels")

    total = sum(len(v) for v in programmes.values())
    with_data = sum(1 for v in programmes.values() if v)
    xml = build_xmltv(catalogue, programmes)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(xml)
    os.replace(tmp, args.out)   # atomic — nginx never serves a half-written file
    log(f"wrote {args.out}: {with_data} channels, {total} programmes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
