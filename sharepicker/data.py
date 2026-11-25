"""Data sources. All share one small interface, so the screen doesn't care where data comes from.

- CsvSource: local CSV files (the offline default; tests and the sample data use it)
- YahooSource: prices, dividends and FX from Yahoo Finance's public chart API
  (best effort, cached on disk). Yahoo has no fundamentals or franking data, so
  those stay missing unless a CSV supplies them.
- LayeredSource: take each kind of data from the first source that has it.

CSV formats are documented in README.md (section "sharepicker data files").
"""
from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from .models import Dividend, Fundamentals, Listing, Series


class DataSource(Protocol):
    name: str

    def universe(self) -> list[Listing]: ...
    def prices(self, ticker: str) -> Series: ...
    def fundamentals(self, company_id: str) -> list[Fundamentals]: ...
    def dividends(self, ticker: str) -> list[Dividend]: ...
    def fx(self, pair: str) -> Series: ...


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _d(v: str | None) -> date | None:
    v = (v or "").strip()
    return date.fromisoformat(v[:10]) if v else None


def _f(v: str | None) -> float | None:
    v = (v or "").strip().replace(",", "")
    if not v:
        return None
    return float(v)


def _b(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "y")


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [{k.strip(): (v or "") for k, v in r.items() if k} for r in csv.DictReader(fh)]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

class CsvSource:
    """Reads listings.csv, prices.csv, fundamentals.csv, dividends.csv and fx.csv from a folder."""

    def __init__(self, folder: str | Path, reporting_lag_months: int = 3):
        self.folder = Path(folder)
        self.name = f"csv:{self.folder}"
        self.lag = reporting_lag_months
        self._prices: dict[str, Series] | None = None
        self._fx: dict[str, Series] | None = None
        self._fund: dict[str, list[Fundamentals]] | None = None
        self._divs: dict[str, list[Dividend]] | None = None

    def universe(self) -> list[Listing]:
        out = []
        for r in _rows(self.folder / "listings.csv"):
            out.append(Listing(
                ticker=r["ticker"].strip(), exchange=r.get("exchange", "NZX").strip().upper(),
                company_id=(r.get("company_id") or r["ticker"]).strip(), name=r.get("name", r["ticker"]).strip(),
                sector=r.get("sector", "").strip() or "Other", currency=(r.get("currency") or "NZD").strip().upper(),
                domicile=(r.get("domicile") or "NZ").strip().upper(),
                security_type=(r.get("security_type") or "company").strip().lower(),
                franking_account=_b(r.get("franking_account")),
                all_ords_from=_d(r.get("all_ords_from")), all_ords_to=_d(r.get("all_ords_to")),
                listed_from=_d(r.get("listed_from")), listed_to=_d(r.get("listed_to")),
                balance_date=(r.get("balance_date") or "").strip(), credit_pct=_f(r.get("credit_pct")),
                units_per_share=_f(r.get("units_per_share")) or 1.0,
                source=self.name))
        return out

    def _load_prices(self) -> dict[str, Series]:
        if self._prices is None:
            raw: dict[str, list[tuple[date, float, float]]] = {}
            for r in _rows(self.folder / "prices.csv"):
                c = _f(r.get("close"))
                if c is None:
                    continue
                raw.setdefault(r["ticker"].strip(), []).append((_d(r["date"]), c, _f(r.get("volume")) or 0.0))
            self._prices = {}
            for t, pts in raw.items():
                pts.sort()
                self._prices[t] = Series(t, [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts], self.name)
        return self._prices

    def prices(self, ticker: str) -> Series:
        return self._load_prices().get(ticker, Series(ticker, source=self.name))

    def fundamentals(self, company_id: str) -> list[Fundamentals]:
        if self._fund is None:
            self._fund = {}
            for r in _rows(self.folder / "fundamentals.csv"):
                pe = _d(r["period_end"])
                cid = r["company_id"].strip()
                self._fund.setdefault(cid, []).append(Fundamentals(
                    company_id=cid, period_end=pe,
                    available_date=_d(r.get("available_date")) or add_months(pe, self.lag),
                    currency=(r.get("currency") or "NZD").strip().upper(),
                    **{k: _f(r.get(k)) for k in ("revenue", "gross_profit", "ebit", "net_income", "equity", "debt",
                                                 "cash", "interest_expense", "operating_cash_flow", "capex",
                                                 "shares_out", "dps")},
                    source=self.name))
            for v in self._fund.values():
                v.sort(key=lambda f: f.period_end)
        return self._fund.get(company_id, [])

    def dividends(self, ticker: str) -> list[Dividend]:
        if self._divs is None:
            self._divs = {}
            for r in _rows(self.folder / "dividends.csv"):
                t = r["ticker"].strip()
                self._divs.setdefault(t, []).append(Dividend(
                    ticker=t, ex_date=_d(r["ex_date"]), amount=_f(r["amount"]) or 0.0,
                    currency=(r.get("currency") or "NZD").strip().upper(),
                    imputed_pct=_f(r.get("imputed_pct")), franked_pct=_f(r.get("franked_pct")),
                    nz_credit_pct=_f(r.get("nz_credit_pct")), source=self.name))
            for v in self._divs.values():
                v.sort(key=lambda x: x.ex_date)
        return self._divs.get(ticker, [])

    def fx(self, pair: str) -> Series:
        if self._fx is None:
            raw: dict[str, list[tuple[date, float]]] = {}
            for r in _rows(self.folder / "fx.csv"):
                raw.setdefault(r["pair"].strip().upper(), []).append((_d(r["date"]), float(r["rate"])))
            self._fx = {p: Series(p, [x[0] for x in sorted(v)], [x[1] for x in sorted(v)], source=self.name)
                        for p, v in raw.items()}
        return self._fx.get(pair.upper(), Series(pair, source=self.name))


# ---------------------------------------------------------------------------
# Yahoo Finance (public chart endpoint)
# ---------------------------------------------------------------------------

class YahooSource:
    """Daily prices, volumes and dividends from Yahoo's chart API, cached as JSON.

    It has no universe and no fundamentals: pair it with a CsvSource in a LayeredSource.
    Failures are recorded in `errors` and give an empty series rather than an exception.
    """

    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?{q}"

    def __init__(self, cache_dir: str | Path = "my-data/cache/yahoo", cache_days: float = 1.0,
                 years: int = 10, timeout: float = 20.0, opener=None):
        self.name = "yahoo"
        self.cache = Path(cache_dir)
        self.cache_days = cache_days
        self.years = years
        self.timeout = timeout
        self.errors: dict[str, str] = {}
        self._open = opener or urllib.request.urlopen
        self._mem: dict[str, dict] = {}
        self._funds: dict[str, list[Fundamentals]] = {}
        self.symbols: dict[str, str] = {}   # company_id -> ticker for fundamentals (set by open_source)
        self.reporting_lag_months = 3

    def _get(self, url: str, cache_name: str, key: str) -> dict | None:
        path = self.cache / cache_name
        if path.exists() and (time.time() - path.stat().st_mtime) < self.cache_days * 86400:
            return json.loads(path.read_text())
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sharepicker)"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.errors[key] = str(e)
            # Stale cache beats nothing; the report flags the data's age.
            return json.loads(path.read_text()) if path.exists() else None
        self.cache.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def _chart(self, symbol: str) -> dict | None:
        if symbol in self._mem:
            return self._mem[symbol]
        safe = symbol.replace('^', '_').replace('=', '_')
        q = urllib.parse.urlencode({"range": f"{self.years}y", "interval": "1d", "events": "div,splits"})
        data = self._get(self.URL.format(sym=urllib.parse.quote(symbol), q=q), f"{safe}.json", symbol)
        if data is None:
            return None
        try:
            result = data["chart"]["result"][0]
        except (KeyError, IndexError, TypeError):
            self.errors[symbol] = f"unexpected response: {str(data)[:200]}"
            return None
        self._mem[symbol] = result
        return result

    def universe(self) -> list[Listing]:
        return []

    def prices(self, ticker: str) -> Series:
        r = self._chart(ticker)
        s = Series(ticker, source=self.name)
        if not r or "timestamp" not in r:
            return s
        quote = r["indicators"]["quote"][0]
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        for i, ts in enumerate(r["timestamp"]):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            # Split-adjusted close (not dividend-adjusted, so dividends can be added separately).
            s.dates.append(datetime.fromtimestamp(ts, timezone.utc).date())
            s.values.append(float(c))
            s.volumes.append(float(vols[i] or 0) if i < len(vols) else 0.0)
        return s

    def dividends(self, ticker: str) -> list[Dividend]:
        r = self._chart(ticker)
        if not r:
            return []
        cur = (r.get("meta") or {}).get("currency", "NZD")
        divs = ((r.get("events") or {}).get("dividends") or {}).values()
        # Yahoo carries no franking or imputation: left as None so the report flags it.
        return sorted((Dividend(ticker, datetime.fromtimestamp(v["date"], timezone.utc).date(), float(v["amount"]),
                                cur, source=self.name) for v in divs), key=lambda x: x.ex_date)

    # Yahoo's annual statement series. Values are as restated today, not as first reported,
    # so they suit a live screen but not a backtest (backtest.py leaves them out).
    FUND_URL = "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}?{q}"
    FUND_FIELDS = {"annualTotalRevenue": "revenue", "annualGrossProfit": "gross_profit", "annualEBIT": "ebit",
                   "annualNetIncome": "net_income", "annualStockholdersEquity": "equity",
                   "annualTotalDebt": "debt", "annualCashAndCashEquivalents": "cash",
                   "annualInterestExpense": "interest_expense", "annualOperatingCashFlow": "operating_cash_flow",
                   "annualCashFlowFromContinuingOperatingActivities": "operating_cash_flow",
                   "annualCapitalExpenditure": "capex", "annualOrdinarySharesNumber": "shares_out"}

    def fundamentals(self, company_id: str) -> list[Fundamentals]:
        if company_id in self._funds:
            return self._funds[company_id]
        sym = self.symbols.get(company_id)
        out: list[Fundamentals] = []
        if sym:
            q = urllib.parse.urlencode({"type": ",".join(self.FUND_FIELDS), "period1": 1262304000,
                                        "period2": int(time.time()) + 86400})
            data = self._get(self.FUND_URL.format(sym=urllib.parse.quote(sym), q=q),
                             f"{sym.replace('^', '_')}.fundamentals.json", f"{sym} fundamentals")
            out = self._parse_fundamentals(company_id, data)
        self._funds[company_id] = out
        return out

    def _parse_fundamentals(self, company_id: str, data: dict | None) -> list[Fundamentals]:
        rows: dict[date, dict] = {}
        cur: dict[date, str] = {}
        for res in ((data or {}).get("timeseries") or {}).get("result") or []:
            kind = ((res.get("meta") or {}).get("type") or [None])[0]
            field = self.FUND_FIELDS.get(kind)
            for pt in res.get(kind) or []:
                if not pt or "reportedValue" not in pt:
                    continue
                d = date.fromisoformat(pt["asOfDate"])
                v = float(pt["reportedValue"]["raw"])
                if field == "capex":
                    v = abs(v)          # Yahoo reports spending as a negative cash flow
                rows.setdefault(d, {}).setdefault(field, v)
                cur[d] = pt.get("currencyCode") or cur.get(d, "NZD")
        return [Fundamentals(company_id, d, add_months(d, self.reporting_lag_months), cur.get(d, "NZD"),
                             source="yahoo (restated)", **vals)
                for d, vals in sorted(rows.items()) if vals.get("net_income") is not None or vals.get("revenue") is not None]

    def fx(self, pair: str) -> Series:
        s = self.prices(f"{pair.upper()}=X")
        s.name = pair.upper()
        return s


class LayeredSource:
    """Use the first source that has data for each request (e.g. Yahoo prices, CSV fundamentals)."""

    def __init__(self, *sources):
        self.sources = sources
        self.name = "+".join(s.name for s in sources)

    def universe(self) -> list[Listing]:
        for s in self.sources:
            u = s.universe()
            if u:
                return u
        return []

    def _first(self, method: str, key: str):
        last = None
        for s in self.sources:
            v = getattr(s, method)(key)
            if len(v):
                return v
            last = v
        return last

    def prices(self, ticker: str) -> Series:
        return self._first("prices", ticker)

    def fundamentals(self, company_id: str) -> list[Fundamentals]:
        return self._first("fundamentals", company_id) or []

    def dividends(self, ticker: str) -> list[Dividend]:
        return self._first("dividends", ticker) or []

    def fx(self, pair: str) -> Series:
        return self._first("fx", pair)

    @property
    def errors(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for s in self.sources:
            out.update(getattr(s, "errors", {}))
        return out


def open_source(cfg: dict) -> DataSource:
    """Build the source named in a screen config's `data:` section."""
    d = cfg.get("data", {}) or {}
    folder = d.get("folder", "my-data/sample")
    lag = int(d.get("reporting_lag_months", 3))
    csv_src = CsvSource(folder, lag)
    if d.get("source", "csv") == "yahoo":
        y = YahooSource(d.get("cache_dir", str(Path(folder) / "cache")), float(d.get("cache_days", 1)),
                        int(d.get("years", 10)))
        y.reporting_lag_months = lag
        # Statements come per company: fetch them with the first listing in listings.csv.
        for lst in csv_src.universe():
            y.symbols.setdefault(lst.company_id, lst.ticker)
        src = _PreferCsvFundamentals(y, csv_src)
        src.listings = {lst.ticker: lst for lst in csv_src.universe()}
        return src
    return csv_src


def staleness(last: date | None, as_of: date) -> int | None:
    return None if last is None else (as_of - last).days


class _PreferCsvFundamentals(LayeredSource):
    """Yahoo for prices, dividends and FX; your CSV statements first, Yahoo's as the fallback."""

    def fundamentals(self, company_id: str) -> list[Fundamentals]:
        y, c = self.sources
        return c.fundamentals(company_id) or y.fundamentals(company_id)

    def dividends(self, ticker: str) -> list[Dividend]:
        """Yahoo dividends carry no imputation or franking: fill in the listing's typical
        `credit_pct` where you've given one (left unknown otherwise, and flagged)."""
        divs = super().dividends(ticker)
        lst = getattr(self, "listings", {}).get(ticker)
        if lst is None or lst.credit_pct is None:
            return divs
        out = []
        for d in divs:
            if lst.domicile == "NZ" and d.imputed_pct is None:
                d = replace(d, imputed_pct=lst.credit_pct)
            elif lst.domicile == "AU" and d.franked_pct is None:
                d = replace(d, franked_pct=lst.credit_pct, nz_credit_pct=d.nz_credit_pct or 0.0)
            out.append(d)
        return out
