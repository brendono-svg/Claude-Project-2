"""Research feeds: company announcements, news, short interest and company profiles.

Sources (all public, read-only, cached; one request at a time):
- ASX announcements  asx.api.markitdigital.com (the ASX website's own feed; latest 5 per company)
- NZX announcements  www.nzx.com/markets/NZSX/announcements (latest ~200 across the market)
- News headlines     news.google.com RSS search for each company name (NZ edition)
- Short interest     download.asic.gov.au daily aggregated short positions (ASX only)
- Company profiles   Yahoo Finance search (full name, sector, industry)

Feeds only show the latest items, so the refresh keeps an **archive**: every item is stored
under its source id, merged with the previous archive, and never dropped (beyond 3 years).
A feed that fails is recorded in `coverage` and the previous items are kept.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import Listing

NZ_TZ = ZoneInfo("Pacific/Auckland")
UA = {"User-Agent": "Mozilla/5.0 (sharepicker research)"}
KEEP_DAYS = 3 * 365


class Fetcher:
    def __init__(self, cache_dir: str | Path, cache_hours: float = 12, delay: float = 0.25, opener=None):
        self.cache = Path(cache_dir)
        self.cache_hours = cache_hours
        self.delay = delay
        self._open = opener or urllib.request.urlopen
        self.errors: dict[str, str] = {}

    def text(self, url: str, key: str) -> str | None:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:120]
        path = self.cache / name
        if path.exists() and time.time() - path.stat().st_mtime < self.cache_hours * 3600:
            return path.read_text(encoding="utf-8")
        if self.delay:
            time.sleep(self.delay)
        try:
            with self._open(urllib.request.Request(url, headers=UA), timeout=25) as r:
                body = r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.errors[key] = str(e)[:200]
            return None
        self.cache.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return body

    def json(self, url: str, key: str) -> Any:
        t = self.text(url, key)
        try:
            return json.loads(t) if t else None
        except ValueError:
            self.errors[key] = "not JSON"
            return None


def _code(lst: Listing) -> str:
    return lst.ticker.split(".")[0]


# ---------------------------------------------------------------------------
# ASX
# ---------------------------------------------------------------------------

def asx_announcements(f: Fetcher, lst: Listing) -> list[dict] | None:
    code = _code(lst).lower()
    d = f.json(f"https://asx.api.markitdigital.com/asx-research/1.0/companies/{code}/announcements",
               f"asx-{code}.json")
    if d is None:
        return None
    out = []
    for it in ((d.get("data") or {}).get("items") or []):
        when = datetime.fromisoformat(it["date"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=10)))
        out.append({"id": f"ASX:{it.get('documentKey') or it['date']}", "date": when.date().isoformat(),
                    "ticker": lst.ticker, "headline": it.get("headline", "").strip(),
                    "type": it.get("announcementType", ""), "price_sensitive": bool(it.get("isPriceSensitive")),
                    "url": f"https://www.asx.com.au/markets/trade-our-cash-market/announcements.{code}",
                    "source": "ASX"})
    return out


# ---------------------------------------------------------------------------
# NZX (the announcements page carries its data as JSON in the page)
# ---------------------------------------------------------------------------

def nzx_announcements(f: Fetcher, listings: list[Listing]) -> list[dict] | None:
    html = f.text("https://www.nzx.com/markets/NZSX/announcements", "nzx-announcements.html")
    if html is None:
        return None
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        f.errors["nzx"] = "page layout changed: no __NEXT_DATA__"
        return None
    try:
        queries = json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
        items = next(q["state"]["data"]["data"] for q in queries if q.get("queryKey", [None])[0] == "announcements")
    except (KeyError, StopIteration, TypeError, ValueError):
        f.errors["nzx"] = "page layout changed: announcements not found"
        return None
    by_code = {_code(x): x.ticker for x in listings if x.exchange == "NZX"}
    out = []
    for it in items:
        sm, dt = it.get("summary") or {}, it.get("details") or {}
        t = by_code.get(sm.get("companyCode"))
        if not t:
            continue
        out.append({"id": f"NZX:{sm.get('id')}", "date": (dt.get("releaseDate") or sm.get("publicationDate", ""))[:10],
                    "ticker": t, "headline": (sm.get("title") or "").strip(), "type": sm.get("type", ""),
                    "price_sensitive": bool(dt.get("isPriceSensitive")),
                    "url": f"https://www.nzx.com/announcements/{sm.get('id')}", "source": "NZX"})
    return out


def nzx_company(f: Fetcher, lst: Listing) -> dict | None:
    """Company page: past dividends with imputation credits, balance date, board and management."""
    code = _code(lst)
    html = f.text(f"https://www.nzx.com/instruments/{code}", f"nzx-{code}.html")
    if html is None:
        return None
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        f.errors[f"nzx {code}"] = "page layout changed"
        return None
    try:
        queries = json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError, ValueError):
        f.errors[f"nzx {code}"] = "page layout changed"
        return None
    out: dict[str, Any] = {"dividends": [], "people": [], "year_end": None, "first_listed": None, "auditor": None}
    for q in queries:
        key, data = q.get("queryKey") or [], (q.get("state") or {}).get("data")
        if key[:2] == ["instrument", "pastDividends"] and isinstance(data, list):
            for dv in data:
                try:
                    cash = float(dv["amount"]) / 100          # cents per share
                    ic = float(dv.get("imputationCreditAmount") or 0)   # dollars per share
                    # NZX timestamps are midnight NZ time: convert there, not in UTC.
                    when = datetime.fromtimestamp(dv.get("recordDate") or dv.get("expectedDate"), NZ_TZ)
                except (KeyError, TypeError, ValueError, OSError):
                    continue
                full = cash * 28 / 72
                out["dividends"].append({
                    "id": f"NZXDIV:{dv.get('id')}", "ticker": lst.ticker, "date": when.date().isoformat(),
                    "kind": dv.get("type"), "cash": round(cash, 6), "currency": dv.get("currencyCode", "NZD"),
                    "imputation_credit": ic, "imputed_pct": round(min(ic / full, 1.0), 4) if full else None})
        elif key[:2] == ["company", "summary"] and isinstance(data, dict):
            ye = data.get("financialYearEnd") or {}
            if ye.get("month"):
                out["year_end"] = f"{int(ye['month']):02d}-{int(ye.get('day') or 30):02d}"
            out["first_listed"] = (data.get("firstListedDate") or "")[:10] or None
            out["auditor"] = data.get("auditor")
        elif key[:2] == ["company", "directors"] and isinstance(data, list):
            out["people"] = [{"name": f"{p.get('firstName', '')} {p.get('lastName', '')}".strip(), "role": p.get("role")}
                             for p in data if p.get("isCurrent")]
    return out


# ---------------------------------------------------------------------------
# News (Google News RSS search)
# ---------------------------------------------------------------------------

def _news_query(lst: Listing) -> str:
    plain = re.sub(r"\s*\(.*?\)", "", lst.name)
    # The exchange name keeps short or common names (AMP, IGO, Vector) on topic.
    return f'"{plain}" {"NZX" if lst.exchange == "NZX" else "ASX"}'


def news(f: Fetcher, lst: Listing, days: int = 120, limit: int = 10) -> list[dict] | None:
    q = urllib.parse.urlencode({"q": _news_query(lst), "hl": "en-NZ", "gl": "NZ", "ceid": "NZ:en"})
    xml = f.text(f"https://news.google.com/rss/search?{q}", f"news-{lst.ticker}.xml")
    if xml is None:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        f.errors[f"news {lst.ticker}"] = "bad RSS"
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if when < cutoff:
            continue
        link = (it.findtext("link") or "").strip()
        out.append({"id": "NEWS:" + ((it.findtext("guid") or link)[-120:]), "date": when.date().isoformat(),
                    "ticker": lst.ticker, "headline": title, "publisher": src, "url": link, "source": "News"})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# ASIC short positions (ASX only), chosen from ASIC's own index of published days
# ---------------------------------------------------------------------------

def asic_shorts(f: Fetcher, listings: list[Listing], lookback_days: int = 35) -> list[dict] | None:
    idx = f.json("https://download.asic.gov.au/short-selling/short-selling-data.json", "asic-index.json")
    if not idx:
        return None
    days = sorted(({"date": str(x["date"]), "version": x.get("version", "001")} for x in idx),
                  key=lambda x: x["date"], reverse=True)
    if not days:
        return None
    latest = datetime.strptime(days[0]["date"], "%Y%m%d").date()
    # The latest day, plus one about a month earlier, for the trend.
    month_ago = next((x for x in days if datetime.strptime(x["date"], "%Y%m%d").date() <= latest - timedelta(days=28)),
                     days[-1])
    want = {_code(x): x.ticker for x in listings if x.exchange == "ASX"}
    out = []
    for day in (days[0], month_ago):
        body = f.text(f"https://download.asic.gov.au/short-selling/RR{day['date']}-{day['version']}-SSDailyAggShortPos.csv",
                      f"asic-{day['date']}.csv")
        if body is None:
            continue
        d = datetime.strptime(day["date"], "%Y%m%d").date().isoformat()
        for row in csv.reader(io.StringIO(body)):
            if len(row) < 5 or row[1].strip() not in want:
                continue
            try:
                pct = float(row[4]) / 100
            except ValueError:
                continue
            out.append({"id": f"ASIC:{d}:{row[1].strip()}", "date": d, "ticker": want[row[1].strip()],
                        "short_pct": round(pct, 6)})
    return out or None


# ---------------------------------------------------------------------------
# Profiles (Yahoo search)
# ---------------------------------------------------------------------------

def profile(f: Fetcher, lst: Listing) -> dict | None:
    q = urllib.parse.urlencode({"q": lst.ticker, "quotesCount": 1, "newsCount": 0})
    d = f.json(f"https://query1.finance.yahoo.com/v1/finance/search?{q}", f"profile-{lst.ticker}.json")
    for qt in (d or {}).get("quotes") or []:
        if qt.get("symbol") == lst.ticker:
            return {"name": qt.get("longname") or qt.get("shortname"), "sector": qt.get("sector"),
                    "industry": qt.get("industry")}
    return None


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

EMPTY = {"announcements": [], "news": [], "shorts": [], "nzx_dividends": [], "profiles": {}, "companies": {},
         "coverage": []}


def merge(old: dict | None, new: dict, today: date | None = None) -> dict:
    """Union by source id; newer copies of an item replace older ones. Nothing is dropped
    except items over 3 years old."""
    today = today or date.today()
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    out = {k: (json.loads(json.dumps(v)) if isinstance(v, (list, dict)) else v) for k, v in EMPTY.items()}
    old = old or {}
    for kind in ("announcements", "news", "shorts", "nzx_dividends"):
        rows = {r["id"]: r for r in old.get(kind, [])}
        rows.update({r["id"]: r for r in new.get(kind, [])})
        out[kind] = sorted((r for r in rows.values() if r.get("date", "") >= cutoff),
                           key=lambda r: (r.get("date", ""), r["id"]), reverse=True)
    out["profiles"] = {**(old.get("profiles") or {}), **(new.get("profiles") or {})}
    out["companies"] = {**(old.get("companies") or {}), **(new.get("companies") or {})}
    out["coverage"] = (new.get("coverage") or []) + (old.get("coverage") or [])[:200]
    return out


def collect(listings: list[Listing], fetcher: Fetcher, previous: dict | None = None,
            feeds: tuple[str, ...] = ("asx", "nzx", "news", "shorts", "profiles"), log=print) -> dict:
    """Fetch every feed, merge with the previous archive, and record what worked."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new: dict[str, Any] = {"announcements": [], "news": [], "shorts": [], "nzx_dividends": [], "profiles": {},
                           "companies": {}, "coverage": []}
    companies = {}
    for lst in listings:
        companies.setdefault(lst.company_id, lst)   # news and profiles once per company

    def note(feed: str, ok: int, failed: int, rows: int) -> None:
        new["coverage"].append({"feed": feed, "fetched": stamp, "ok": ok, "failed": failed, "rows": rows})
        log(f"  {feed}: {ok} ok, {failed} failed, {rows} items")

    if "asx" in feeds:
        ok = bad = 0
        for lst in listings:
            if lst.exchange != "ASX":
                continue
            r = asx_announcements(fetcher, lst)
            if r is None:
                bad += 1
            else:
                ok += 1
                new["announcements"] += r
        note("ASX announcements", ok, bad, sum(1 for r in new["announcements"] if r["source"] == "ASX"))
    if "nzx" in feeds:
        r = nzx_announcements(fetcher, listings)
        new["announcements"] += r or []
        note("NZX announcements", 1 if r is not None else 0, 0 if r is not None else 1, len(r or []))
    if "nzx" in feeds:
        ok = bad = 0
        for lst in listings:
            if lst.exchange != "NZX":
                continue
            c = nzx_company(fetcher, lst)
            if c is None:
                bad += 1
                continue
            ok += 1
            new["nzx_dividends"] += c.pop("dividends")
            new["companies"][lst.company_id] = c
        note("NZX company pages", ok, bad, len(new["nzx_dividends"]))
    if "news" in feeds:
        ok = bad = 0
        for lst in companies.values():
            r = news(fetcher, lst)
            if r is None:
                bad += 1
            else:
                ok += 1
                new["news"] += r
        note("News headlines", ok, bad, len(new["news"]))
    if "shorts" in feeds:
        r = asic_shorts(fetcher, listings)
        new["shorts"] += r or []
        note("ASIC short positions", 1 if r else 0, 0 if r else 1, len(r or []))
    if "profiles" in feeds:
        ok = bad = 0
        for lst in listings:
            p = profile(fetcher, lst)
            if p:
                ok += 1
                new["profiles"][lst.ticker] = p
            else:
                bad += 1
        note("Company profiles", ok, bad, ok)
    return merge(previous, new)
