"""Research layer: announcements, results calendar, short interest, your thesis journal,
screen history ("memory"), and a research checklist.

Everything here reads local files you keep in the data folder. Nothing is fetched:
links to primary sources (NZX, ASX, company sites) are built for you to open yourself.

Files (all optional, in the data folder):
  announcements.csv  date, ticker, headline, url, price_sensitive (1/0/blank), category (optional)
  events.csv         date, ticker, kind (results|quarterly|agm|ex_div|capital_raise|other), confirmed (1/0), note
  shorts.csv         date, ticker, short_pct (0-1; ASIC publishes ASX short positions daily)
  journal.yaml       your thesis per share, with rules that would break it
  research/<TICKER>/*.md   your own notes
"""
from __future__ import annotations

import csv
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from . import market_rules as mr
from .data import _b, _d, _f, add_months
from .factors import GROUPS, METRICS
from .models import Fundamentals, Listing, Series

# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

# (category, pattern, base relevance). First match wins, so specific patterns come first.
CATEGORIES: list[tuple[str, str, float]] = [
    # Meeting outcomes and results-date notices mention "results" but are not results releases.
    ("broker_rating", r"price target|target price|(upgrade|downgrade)[sd]? (to|from) |outperform|underperform|"
                      r"overweight|underweight|buy rating|sell rating|initiates coverage|reiterate", 0.55),
    ("agm", r"results? of (the )?(annual|special|general|shareholders'?)? ?(general )?(meeting|agm|asm)", 0.3),
    ("results_notice", r"results? (date|briefing|webcast|call|presentation date|announcement date)|"
                       r"notice of .*results|(date|timing) of .*results", 0.4),
    ("takeover", r"takeover|scheme (of arrangement|implementation)|indicative (proposal|offer)|non-binding|"
                 r"acquisition proposal|\bbid\b", 0.95),
    ("guidance_down", r"downgrade|profit warning|(lower|reduce|cut)[sd]? (its |full[- ]year |fy\d* )?(earnings |profit )?guidance|"
                      r"below (prior )?guidance|guidance (cut|lowered|reduced)", 0.95),
    ("guidance_up", r"upgrade|(raise|increase|lift)[sd]? (its |full[- ]year |fy\d* )?(earnings |profit )?guidance|"
                    r"ahead of (prior )?guidance|guidance (raised|increased|upgraded)", 0.95),
    ("trading_halt", r"trading halt|suspension|pause in trading|voluntary suspension", 0.85),
    ("capital_raise", r"placement|capital rais|entitlement offer|share purchase plan|\bspp\b|rights (issue|offer)|"
                      r"equity rais", 0.85),
    ("quarterly", r"quarterly (activities|cash ?flow|report|update)|appendix (4c|5b)|\b(4c|5b)\b", 0.6),
    ("results", r"(half[- ]year|full[- ]year|annual|interim|preliminary|fy\d*|h[12]\s?(fy)?\d*) "
                r"(result|report|financial|announcement)|appendix (4d|4e)|\bresults?\b", 0.9),
    ("guidance", r"guidance|outlook|earnings update|trading update|investor day|strategy day", 0.75),
    ("leadership", r"\bceo\b|chief executive|managing director|\bcfo\b|chair(man|person)?\b|resign|retire|appoint", 0.6),
    ("acquisition", r"acquisition|acquire|divest|sale of|merger|joint venture", 0.7),
    ("director_trade", r"director'?s?'? (interest|dealing|trading)|ongoing disclosure|appendix 3y|"
                       r"change of director", 0.5),
    ("substantial_holder", r"substantial (holder|product holder|shareholder)|becoming a substantial|"
                           r"ceasing to be", 0.4),
    ("debt", r"\bdebt\b|bond|notes? (offer|issue)|refinanc|covenant|credit rating", 0.5),
    ("dividend", r"dividend|distribution|\bdrp\b|reinvestment plan", 0.45),
    ("agm", r"annual (general|shareholders'?) meeting|\bagm\b|notice of meeting|\basm\b", 0.3),
]
LABELS = {"takeover": "Takeover / scheme", "guidance_down": "Guidance cut", "guidance_up": "Guidance raised",
          "trading_halt": "Trading halt", "capital_raise": "Capital raise", "quarterly": "Quarterly report",
          "results": "Results", "guidance": "Guidance / outlook", "leadership": "Leadership change",
          "acquisition": "Acquisition / sale", "director_trade": "Director trade",
          "substantial_holder": "Substantial holder", "debt": "Debt / funding", "dividend": "Dividend",
          "agm": "Meeting", "results_notice": "Results date / briefing", "broker_rating": "Broker view",
          "other": "Other"}


def classify(headline: str) -> tuple[str, float]:
    h = headline.lower()
    for cat, pat, rel in CATEGORIES:
        if re.search(pat, h):
            return cat, rel
    return "other", 0.2


@dataclass
class Announcement:
    date: date
    ticker: str
    headline: str
    url: str = ""
    price_sensitive: bool | None = None
    category: str = "other"
    relevance: float = 0.2
    reaction: float | None = None    # stock minus home index, close before -> close day after
    source: str = ""                 # NZX, ASX, News, or blank for your own file
    publisher: str = ""

    @property
    def label(self) -> str:
        return LABELS.get(self.category, self.category)


@dataclass
class Event:
    date: date
    ticker: str
    kind: str
    confirmed: bool = True
    note: str = ""


@dataclass
class JournalEntry:
    ticker: str
    thesis: str = ""
    added: date | None = None
    review_by: date | None = None
    break_if: list[str] = field(default_factory=list)
    watch_for: list[str] = field(default_factory=list)


def price_reaction(stock: Series, index: Series | None, on: date) -> float | None:
    """Return from the last close before `on` to the close on the trading day after `on`'s
    trading day, minus the home index over the same dates. Announcements have no time of day,
    so this window catches releases made before, during or after the session."""
    i0 = stock.index_on(on - timedelta(days=1))
    j = stock.index_on(on - timedelta(days=1)) + 1          # first trading day on/after `on`
    if i0 < 0 or j + 1 >= len(stock.dates):
        return None
    d0, d1 = stock.dates[i0], stock.dates[j + 1]
    if (d1 - on).days > 7 or (on - d0).days > 7:
        return None   # gap in the price data: no reliable reaction
    r = stock.values[j + 1] / stock.values[i0] - 1
    if index is not None and len(index):
        b0, b1 = index.on(d0, 5), index.on(d1, 5)
        if b0 and b1:
            r -= b1 / b0 - 1
    return r


# ---------------------------------------------------------------------------
# Journal rules: "dividend_cover < 1.2", "rank > 15", "rsi14 >= 80", "roe < 10%"
# ---------------------------------------------------------------------------

FACT_NAMES = ("rsi14", "vs_ma50", "vs_ma200", "volume_surge", "cash_yield", "after_tax_yield",
              "headline_yield", "share_count_growth", "short_pct")
RULE_NAMES = set(METRICS) | set(GROUPS) | {"composite", "rank"} | set(FACT_NAMES)
_RULE = re.compile(r"^\s*([a-z_0-9]+)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)\s*(%?)\s*$")
_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


class RuleError(ValueError):
    pass


def parse_rule(text: str) -> tuple[str, str, float]:
    m = _RULE.match(text.lower())
    if not m:
        raise RuleError(f"Can't read rule {text!r}. Write it like 'dividend_cover < 1.2' or 'roe < 10%'.")
    name, op, num, pct = m.groups()
    if name not in RULE_NAMES:
        raise RuleError(f"Unknown name {name!r} in rule {text!r}. Use one of: {', '.join(sorted(RULE_NAMES))}")
    return name, op, float(num) / (100 if pct else 1)


def rule_value(name: str, ctx: dict[str, Any]) -> float | None:
    for part in ("metrics", "groups", "facts", "top"):
        v = ctx.get(part, {}).get(name)
        if v is not None:
            return v
    return None


def check_rule(text: str, ctx: dict[str, Any]) -> tuple[str, float | None]:
    """'broken', 'ok' or 'unknown' (no data), plus the value seen."""
    name, op, num = parse_rule(text)
    v = rule_value(name, ctx)
    if v is None:
        return "unknown", None
    return ("broken" if _OPS[op](v, num) else "ok"), v


# ---------------------------------------------------------------------------
# Store: loads the research files once
# ---------------------------------------------------------------------------

def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(fh)]


class ResearchStore:
    def __init__(self, folder: str | Path, journal: str | Path | None = None):
        self.folder = Path(folder)
        self._ann: dict[str, list[Announcement]] = {}
        for r in _rows(self.folder / "announcements.csv"):
            cat, rel = (r["category"], dict((c, x) for c, _, x in CATEGORIES).get(r["category"], 0.2)) \
                if r.get("category") else classify(r["headline"])
            ps = r.get("price_sensitive", "")
            self._ann.setdefault(r["ticker"], []).append(Announcement(
                _d(r["date"]), r["ticker"], r["headline"], r.get("url", ""),
                None if ps == "" else _b(ps), cat, rel))
        for v in self._ann.values():
            v.sort(key=lambda a: a.date)
        self._events: dict[str, list[Event]] = {}
        for r in _rows(self.folder / "events.csv"):
            c = r.get("confirmed", "1")
            self._events.setdefault(r["ticker"], []).append(
                Event(_d(r["date"]), r["ticker"], (r.get("kind") or "other").lower(), c == "" or _b(c), r.get("note", "")))
        for v in self._events.values():
            v.sort(key=lambda e: e.date)
        self._shorts: dict[str, Series] = {}
        for r in _rows(self.folder / "shorts.csv"):
            s = self._shorts.setdefault(r["ticker"], Series(r["ticker"]))
            s.dates.append(_d(r["date"]))
            s.values.append(_f(r["short_pct"]) or 0.0)
        for s in self._shorts.values():
            pts = sorted(zip(s.dates, s.values))
            s.dates, s.values = [p[0] for p in pts], [p[1] for p in pts]
        self._news: dict[str, list[Announcement]] = {}
        self.profiles: dict[str, dict] = {}
        self.companies: dict[str, dict] = {}   # NZX company facts by company_id
        self.coverage: list[dict] = []
        jp = Path(journal) if journal else self.folder / "journal.yaml"
        self.journal: dict[str, JournalEntry] = load_journal(jp) if jp.exists() else {}

    def _tickers(self, ticker: str) -> list[str]:
        return getattr(self, "aliases", {}).get(ticker, [ticker])

    def announcements(self, ticker: str, until: date | None = None) -> list[Announcement]:
        """Official announcements for the company (all its listings; ASX re-posts of NZX ones merged)."""
        seen, out = set(), []
        for t in self._tickers(ticker):
            for x in self._ann.get(t, []):
                key = (x.date, re.sub(r"\W+", "", x.headline.lower())[:60])
                if (until is None or x.date <= until) and key not in seen:
                    seen.add(key)
                    out.append(x)
        return sorted(out, key=lambda a: a.date)

    def news(self, ticker: str, until: date | None = None) -> list[Announcement]:
        out = [x for t in self._tickers(ticker) for x in self._news.get(t, []) if until is None or x.date <= until]
        return sorted({x.headline: x for x in out}.values(), key=lambda a: a.date, reverse=True)

    def add_archive(self, archive: dict) -> None:
        """Load a feeds archive (sharepicker/feeds.py) on top of any files."""
        for r in archive.get("announcements", []):
            cat, rel = classify(r["headline"])
            self._ann.setdefault(r["ticker"], []).append(Announcement(
                date.fromisoformat(r["date"]), r["ticker"], r["headline"], r.get("url", ""),
                r.get("price_sensitive"), cat, rel, source=r.get("source", "")))
        for v in self._ann.values():
            v.sort(key=lambda a: a.date)
        for r in archive.get("news", []):
            cat, rel = classify(r["headline"])
            self._news.setdefault(r["ticker"], []).append(Announcement(
                date.fromisoformat(r["date"]), r["ticker"], r["headline"], r.get("url", ""), None,
                cat, min(rel, 0.6), source="News", publisher=r.get("publisher", "")))
        for r in archive.get("shorts", []):
            s = self._shorts.setdefault(r["ticker"], Series(r["ticker"]))
            s.dates.append(date.fromisoformat(r["date"]))
            s.values.append(float(r["short_pct"]))
        for s in self._shorts.values():
            pts = sorted(set(zip(s.dates, s.values)))
            s.dates, s.values = [x[0] for x in pts], [x[1] for x in pts]
        self.profiles.update(archive.get("profiles") or {})
        self.companies.update(archive.get("companies") or {})
        self.coverage = archive.get("coverage") or []

    def short_trend(self, ticker: str, on: date) -> tuple[float | None, float | None]:
        """Short interest now and about a month earlier."""
        s = self._shorts.get(ticker)
        if not s:
            return None, None
        return s.on(on, 14), s.on(on - timedelta(days=28), 14)

    def events(self, ticker: str) -> list[Event]:
        return self._events.get(ticker, [])

    def short_pct(self, ticker: str, on: date) -> float | None:
        s = self._shorts.get(ticker)
        return s.on(on, max_gap_days=14) if s else None

    def notes(self, ticker: str) -> list[Path]:
        d = self.folder / "research" / ticker
        return sorted(d.glob("*.md")) if d.exists() else []

    def has_any(self) -> bool:
        return bool(self._ann or self._events or self._shorts or self.journal)


def load_journal(path: Path) -> dict[str, JournalEntry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for ticker, v in (raw.get("shares") or raw).items():
        v = v or {}
        e = JournalEntry(ticker.upper(), str(v.get("thesis", "")).strip(), _date(v.get("added")),
                         _date(v.get("review_by")), [str(x) for x in v.get("break_if") or []],
                         [str(x) for x in v.get("watch_for") or []])
        for rule in e.break_if:
            parse_rule(rule)   # fail loudly on a typo rather than silently never triggering
        out[e.ticker] = e
    return out


def _date(v) -> date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


# ---------------------------------------------------------------------------
# Results calendar and results card
# ---------------------------------------------------------------------------

def _half_year_ends(latest: date, n: int = 6) -> list[date]:
    """Period ends after the latest annual balance date: half-year, then full-year, ..."""
    out = []
    for k in range(1, n + 1):
        d = add_months(latest.replace(day=1), 6 * k)
        out.append(add_months(d, 1) - timedelta(days=1) if (latest + timedelta(days=1)).day == 1 else
                   add_months(latest, 6 * k))
    return out


def next_results(listing: Listing, funds: list[Fundamentals], store: ResearchStore | None,
                 as_of: date) -> dict[str, Any] | None:
    """Next results release: confirmed date, else estimated from past releases, else the deadline."""
    events = store.events(listing.ticker) if store else []
    conf = [e for e in events if e.kind in ("results", "quarterly") and e.date >= as_of and e.confirmed]
    if conf:
        e = conf[0]
        return {"date": e.date, "basis": "confirmed", "kind": e.kind, "note": e.note}
    ends = [f.period_end for f in funds if f.period_end <= as_of]
    if not ends and listing.balance_date:
        # No financials in the data: work from the balance date alone (latest year end before as_of).
        mm, dd = (int(x) for x in listing.balance_date.split("-"))
        ye = date(as_of.year, mm, dd)
        ends = [ye if ye <= as_of else date(as_of.year - 1, mm, dd)]
    if not ends:
        return None
    latest = ends[-1]
    past = [a for a in (store.announcements(listing.ticker, as_of) if store else []) if a.category == "results"]
    lags = []
    for a in past:
        # Match each past release to the nearest period end before it.
        cands = [latest] + [add_months(latest, -6 * k) for k in range(1, 12)] + _half_year_ends(latest)
        before = [c for c in cands if c + timedelta(days=14) <= a.date and (a.date - c).days <= 100]
        if before:
            lags.append((a.date - max(before)).days)
    for pe in [latest] + _half_year_ends(latest):
        deadline = mr.results_deadline(pe, listing.exchange)
        if deadline < as_of:
            continue
        # Reported only if a release falls in this period's window, so a stray mention of
        # "results" just after the balance date doesn't hide the real release.
        if any(pe + timedelta(days=14) <= a.date <= deadline + timedelta(days=30) for a in past):
            continue  # this period has already been reported
        kind = "full-year" if pe.month == latest.month else "half-year"
        if lags:
            lag = statistics.median(lags)
            est = pe + timedelta(days=int(lag))
            if est >= as_of:
                return {"date": est, "basis": f"estimated: usually {int(lag)} days after balance date",
                        "kind": kind, "period_end": pe, "deadline": deadline}
        basis = "listing-rule deadline" + ("" if past else "; may already be out, no announcements in data")
        return {"date": deadline, "basis": basis, "kind": kind, "period_end": pe,
                "deadline": deadline}
    return None


def results_card(funds: list[Fundamentals], as_of: date) -> dict[str, Any] | None:
    f = [x for x in funds if x.available_date <= as_of]
    if len(f) < 2:
        return None
    cur, prev = f[-1], f[-2]

    def chg(a, b):
        return None if a is None or b in (None, 0) else (a - b) / abs(b)
    return {"period_end": cur.period_end, "currency": cur.currency,
            "revenue": cur.revenue, "revenue_change": chg(cur.revenue, prev.revenue),
            "net_income": cur.net_income, "net_income_change": chg(cur.net_income, prev.net_income),
            "eps": cur.eps, "eps_change": chg(cur.eps, prev.eps),
            "dps": cur.dps, "dps_change": chg(cur.dps, prev.dps),
            "cash_conversion": (cur.operating_cash_flow / cur.net_income
                                if cur.operating_cash_flow is not None and cur.net_income and cur.net_income > 0 else None)}


# ---------------------------------------------------------------------------
# History ("memory")
# ---------------------------------------------------------------------------

def save_history(res, folder: str | Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data = {"as_of": res.as_of.isoformat(), "weights": res.weights,
            "shares": {r.ticker: {"rank": r.rank, "composite": r.composite, "groups": r.group_scores,
                                  "red": [t for k, t in res.flags.get(r.ticker, []) if k == "red"]}
                       for r in res.ranked}}
    p = folder / f"screen-{res.as_of.isoformat()}.json"
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return p


def load_history(folder: str | Path, before: date | None = None) -> list[dict]:
    folder = Path(folder)
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("screen-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if before is None or date.fromisoformat(d["as_of"]) < before:
            out.append(d)
    return out


def _flag_key(text: str) -> str:
    return re.split(r"[(:0-9]", text, maxsplit=1)[0].strip()


def memory_for(ticker: str, history: list[dict]) -> dict[str, Any]:
    """Rank path and red flags that keep coming back, from saved screens (oldest first)."""
    path = [(h["as_of"], h["shares"][ticker]["rank"], h["shares"][ticker]["composite"])
            for h in history if ticker in h.get("shares", {})]
    sets = [{_flag_key(t) for t in (h.get("shares", {}).get(ticker) or {}).get("red", [])} for h in history]
    streaks: dict[str, int] = {}
    for k in (sets[-1] if sets else set()):
        n = 0
        for flags in reversed(sets):          # newest first; stop at the first screen without it
            if k not in flags:
                break
            n += 1
        streaks[k] = n
    persistent = {k: v for k, v in streaks.items() if v >= 3}
    return {"path": path, "persistent_flags": persistent}


# ---------------------------------------------------------------------------
# Research checklist (several independent lenses, never a single verdict)
# ---------------------------------------------------------------------------

def checklist(r, res, store: ResearchStore | None, funds: list[Fundamentals], as_of: date,
              history: list[dict] | None = None) -> dict[str, Any]:
    s = r.snap
    lst, f = s.listing, s.facts
    n = len(res.ranked) or 1
    out: dict[str, Any] = {}
    third = "top third" if r.rank <= n / 3 else "bottom third" if r.rank > 2 * n / 3 else "middle third"
    out["score"] = f"{third} (rank {r.rank} of {n}, {r.confidence} confidence)"

    # Thesis: your own words and rules
    entry = store.journal.get(lst.ticker) if store else None
    short = store.short_pct(lst.ticker, as_of) if store else None
    ctx = {"metrics": s.metrics, "groups": r.group_scores, "facts": {**f, "short_pct": short},
           "top": {"composite": r.composite, "rank": float(r.rank)}}
    rules = []
    if entry is None:
        out["thesis"] = "none written (add one to journal.yaml)"
    else:
        for rule in entry.break_if:
            st, v = check_rule(rule, ctx)
            rules.append({"rule": rule, "status": st, "value": v})
        broken = [x["rule"] for x in rules if x["status"] == "broken"]
        if broken:
            out["thesis"] = "BROKEN: " + "; ".join(broken)
        elif entry.review_by and entry.review_by <= as_of:
            out["thesis"] = f"review due (was {entry.review_by})"
        elif any(x["status"] == "unknown" for x in rules):
            out["thesis"] = "intact, but some rules have no data"
        else:
            out["thesis"] = "intact"

    # Technicals: context only
    rsi, ma200, surge = f.get("rsi14"), f.get("vs_ma200"), f.get("volume_surge")
    tech = []
    if rsi is not None and rsi >= 70:
        tech.append(f"stretched (RSI {rsi:.0f})")
    elif rsi is not None and rsi <= 30:
        tech.append(f"oversold (RSI {rsi:.0f})")
    if ma200 is not None and abs(ma200) >= 0.15:
        tech.append(f"{abs(ma200):.0%} {'above' if ma200 > 0 else 'below'} its 200-day average")
    if surge is not None and surge >= 2:
        tech.append(f"volume {surge:.1f}x normal this week")
    out["technicals"] = ", ".join(tech) if tech else "neutral"

    nr = next_results(lst, funds, store, as_of)
    out["next_results"] = (f"{nr['date']:%d %b %Y} ({nr['basis']})" if nr else "unknown")
    if nr and (nr["date"] - as_of).days <= 14:
        out["next_results"] = "SOON: " + out["next_results"]
    if short is not None:
        now, before = store.short_trend(lst.ticker, as_of) if store else (None, None)
        trend = ""
        if before is not None and now is not None and abs(now - before) >= 0.005:
            trend = f", {'up' if now > before else 'down'} from {before:.1%} a month ago"
        out["short_interest"] = f"{short:.1%} of shares sold short{trend}" + (" (high)" if short >= 0.05 else "")
    recent = [a for a in (store.announcements(lst.ticker, as_of) if store else [])
              if (as_of - a.date).days <= 30 and a.relevance >= 0.8]
    if recent:
        out["recent_news"] = "; ".join(f"{a.label} {a.date:%d %b}" for a in recent[-3:])
    press = [a for a in (store.news(lst.ticker, as_of) if store else []) if (as_of - a.date).days <= 30]
    if press:
        brokers = sum(1 for a in press if a.category == "broker_rating")
        out["media"] = f"{len(press)} headline{'s' if len(press) != 1 else ''} in 30 days" + \
            (f", {brokers} broker view{'s' if brokers != 1 else ''}" if brokers else "")
    if history:
        mem = memory_for(lst.ticker, history)
        if mem["path"]:
            first = mem["path"][0]
            out["trend"] = f"rank {first[1]} on {first[0]} -> {r.rank} now"
        if mem["persistent_flags"]:
            out["persistent"] = ", ".join(f"{k} ({v} screens)" for k, v in mem["persistent_flags"].items())
    return {"lenses": out, "rules": rules, "journal": entry, "next_results": nr, "short_pct": short}


# ---------------------------------------------------------------------------
# Primary-source links (built, never fetched)
# ---------------------------------------------------------------------------

def source_links(lst: Listing, website: str = "") -> list[tuple[str, str]]:
    code = lst.ticker.split(".")[0]
    links = []
    if lst.exchange == "NZX":
        links.append(("NZX announcements", f"https://www.nzx.com/markets/NZSX/securities/{code}"))
    if lst.exchange == "ASX":
        links.append(("ASX announcements",
                      f"https://www.asx.com.au/markets/trade-our-cash-market/announcements.{code.lower()}"))
        links.append(("ASIC short positions", "https://asic.gov.au/regulatory-resources/markets/short-selling/short-position-reports-table/"))
        links.append(("Market Index", f"https://www.marketindex.com.au/asx/{code.lower()}"))
    links.append(("Yahoo Finance", f"https://finance.yahoo.com/quote/{lst.ticker}"))
    if website:
        links.append(("Company investor centre", website))
    return links
