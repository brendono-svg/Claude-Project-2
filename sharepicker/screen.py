"""The screen: build the universe on a date, filter it, merge dual listings, score, flag."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import market_rules as mr
from .data import DataSource
from .factors import FX, Snapshot, snapshot
from .models import Series
from .scoring import Scored, resolve_weights, score

DEFAULT_BENCHMARKS = {"NZX": "^NZ50", "ASX": "^AXJO"}

DEFAULT_FILTERS: dict[str, Any] = {
    "exchanges": ["NZX", "ASX"],
    "min_market_cap_nzd": 100e6,
    "min_traded_value_nzd": 50_000,   # median daily traded value
    "min_years_listed": 3,
    "exclude_sectors": [],
    "exclude_tickers": [],
    "security_types": ["company"],
}


@dataclass
class ScreenResult:
    as_of: date
    weights: dict[str, float]
    ranked: list[Scored]
    excluded: dict[str, str] = field(default_factory=dict)
    other_listings: dict[str, list[str]] = field(default_factory=dict)  # chosen ticker -> other tickers
    flags: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # ticker -> [(red|green, text)]
    fif: dict[str, tuple[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    stale_days: int = 10
    source_name: str = ""


def load_benchmarks(source: DataSource, names: dict[str, str]) -> dict[str, Series]:
    return {ex: source.prices(t) for ex, t in names.items()}


def run_screen(cfg: dict, source: DataSource, as_of: date, *, benchmarks: dict[str, Series] | None = None,
               fx: FX | None = None, price_cache: dict[str, Series] | None = None,
               with_flags: bool = True) -> ScreenResult:
    filters = {**DEFAULT_FILTERS, **(cfg.get("filters") or {})}
    weights = resolve_weights(cfg.get("weights", "balanced"))
    marginal = float(cfg.get("marginal_tax_rate", 0.33))
    fx = fx or FX(source)
    bench = benchmarks if benchmarks is not None else load_benchmarks(source, {**DEFAULT_BENCHMARKS, **(cfg.get("benchmarks") or {})})
    res = ScreenResult(as_of, weights, [], source_name=source.name)
    res.stale_days = int(cfg.get("stale_after_days", 10))

    exchanges = {e.upper() for e in filters["exchanges"]}
    ex_sectors = {s.lower() for s in filters["exclude_sectors"]}
    ex_tickers = {t.upper() for t in filters["exclude_tickers"]}
    types = {t.lower() for t in filters["security_types"]}

    snaps: list[Snapshot] = []
    for lst in source.universe():
        if not lst.listed_on(as_of):
            continue
        if lst.exchange not in exchanges:
            continue
        why = None
        if lst.ticker.upper() in ex_tickers:
            why = "excluded by you"
        elif lst.sector.lower() in ex_sectors:
            why = f"sector {lst.sector} excluded"
        elif lst.security_type not in types:
            why = f"security type {lst.security_type} not screened"
        if why:
            res.excluded[lst.ticker] = why
            continue
        px = price_cache.get(lst.ticker) if price_cache is not None else None
        snaps.append(snapshot(lst, source, as_of, fx, bench.get(lst.exchange), marginal, prices=px))

    # Dual listings: keep the most liquid listing of each company.
    by_company: dict[str, list[Snapshot]] = {}
    for s in snaps:
        by_company.setdefault(s.listing.company_id, []).append(s)
    chosen: list[Snapshot] = []
    for group in by_company.values():
        group.sort(key=lambda s: -(s.traded_value_nzd or 0))
        chosen.append(group[0])
        if len(group) > 1:
            res.other_listings[group[0].listing.ticker] = [g.listing.ticker for g in group[1:]]
            for g in group[1:]:
                res.excluded[g.listing.ticker] = f"dual listing: scored on the more liquid {group[0].listing.ticker}"

    keep = []
    for s in chosen:
        t = s.listing.ticker
        if s.price is None:
            res.excluded[t] = "no recent price"
        elif s.market_cap_nzd is not None and s.market_cap_nzd < filters["min_market_cap_nzd"]:
            res.excluded[t] = f"market cap NZ${s.market_cap_nzd / 1e6:,.0f}m below minimum"
        elif s.traded_value_nzd is not None and s.traded_value_nzd < filters["min_traded_value_nzd"]:
            res.excluded[t] = f"illiquid: median NZ${s.traded_value_nzd:,.0f}/day traded"
        elif s.traded_value_nzd is None and filters["min_traded_value_nzd"] > 0:
            res.excluded[t] = "no volume data to check liquidity"
        elif _years_listed(s) < filters["min_years_listed"]:
            res.excluded[t] = f"listed under {filters['min_years_listed']} years"
        else:
            keep.append(s)

    res.ranked = score(keep, weights)
    for r in res.ranked:
        lst = r.snap.listing
        res.fif[lst.ticker] = mr.fif_class(lst.domicile, lst.security_type, lst.in_all_ords(as_of), lst.franking_account)
        if with_flags:
            res.flags[lst.ticker] = flags_for(r, as_of, res.stale_days)
    if not any(lst.listed_to for lst in source.universe()):
        res.warnings.append("No delisted shares in the data: backtests will look better than reality (survivorship bias).")
    errs = getattr(source, "errors", {})
    if errs:
        res.warnings.append(f"{len(errs)} data requests failed: " + "; ".join(f"{k}: {v[:60]}" for k, v in list(errs.items())[:5]))
    return res


def _years_listed(s: Snapshot) -> float:
    lf = s.listing.listed_from
    if lf:
        return (s.as_of - lf).days / 365.25
    return s.years_of_prices


def flags_for(r: Scored, as_of: date, stale_days: int = 10) -> list[tuple[str, str]]:
    s, m, f = r.snap, r.snap.metrics, r.snap.facts
    out: list[tuple[str, str]] = []
    red = lambda t: out.append(("red", t))      # noqa: E731
    green = lambda t: out.append(("green", t))  # noqa: E731
    cover = m.get("dividend_cover")
    if cover is not None and cover < 1:
        red(f"Dividend not covered by earnings (cover {cover:.1f}x): it may be cut")
    ic = m.get("interest_cover")
    if ic is not None and ic < 3:
        red(f"Low interest cover ({ic:.1f}x): sensitive to higher rates")
    if f.get("negative_equity"):
        red("Negative shareholders' equity")
    if f.get("fcf") is not None and f["fcf"] < 0 and "fcf_yield" not in s.not_applicable:
        red("Negative free cash flow in the latest year")
    g = f.get("share_count_growth")
    if g is not None and g > 0.05:
        red(f"Share count growing {g:.0%} a year: existing holders are being diluted")
    if s.price_date and (as_of - s.price_date).days > stale_days:
        red(f"Price is {(as_of - s.price_date).days} days old")
    age = f.get("fundamentals_age_days")
    if age is not None and age > 548:
        red(f"Latest financials are {age // 30} months old")
    if "fundamentals" in s.missing:
        red("No financial statements available: value, quality and growth not scored")
    if f.get("tax_data_missing"):
        red("Imputation/franking data missing: after-tax yield is a lower bound")
    if s.listing.domicile == "AU" and f.get("headline_yield", 0) - m.get("gross_yield_nz", 0) > 0.005:
        red(f"Headline grossed-up yield {f['headline_yield']:.1%} includes franking credits "
            f"you can't use; your grossed-up yield is {m['gross_yield_nz']:.1%}")
    if (m.get("roic") or 0) > 0.15 and (m.get("debt_to_equity") is not None and m["debt_to_equity"] < 0.5):
        green(f"High return on capital ({m['roic']:.0%}) with modest debt")
    if (m.get("earnings_consistency") or 0) >= 0.8:
        green("Earnings grew in most recent years")
    if cover is not None and cover >= 1.5 and (m.get("gross_yield_nz") or 0) >= 0.04:
        green(f"Well-covered dividend ({cover:.1f}x) with a {m['gross_yield_nz']:.1%} grossed-up yield")
    if (m.get("near_52w_high") or 0) >= 0.95 and (m.get("momentum_12_1") or 0) > 0.1:
        green("Strong price trend, near its 52-week high")
    return out

