"""Factor metrics for one listing, as at a date, using only data available on that date.

Point-in-time rule: fundamentals count only from their `available_date`, prices and
dividends only up to `as_of`. That is what keeps the backtest free of look-ahead bias.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from . import market_rules as mr
from .data import DataSource, add_months
from .models import Fundamentals, Listing, Series

# Metric -> (factor group, higher_is_better, plain-English label)
METRICS: dict[str, tuple[str, bool, str]] = {
    "earnings_yield":   ("value", True, "earnings yield"),
    "ebit_ev":          ("value", True, "EBIT / enterprise value"),
    "book_to_price":    ("value", True, "book value / price"),
    "fcf_yield":        ("value", True, "free-cash-flow yield"),
    "roe":              ("quality", True, "return on equity"),
    "roic":             ("quality", True, "return on invested capital"),
    "margin_stability": ("quality", True, "stable gross margins"),
    "debt_to_equity":   ("quality", False, "low debt / equity"),
    "interest_cover":   ("quality", True, "interest cover"),
    "earnings_consistency": ("quality", True, "consistent earnings growth"),
    "f_score":          ("quality", True, "financial strength (Piotroski tests passed)"),
    "net_debt_ebitda":  ("quality", False, "low net debt / EBITDA"),
    "revenue_growth":   ("growth", True, "revenue growth (CAGR)"),
    "eps_growth":       ("growth", True, "EPS growth (CAGR)"),
    "momentum_12_1":    ("momentum", True, "12-1 month price momentum"),
    "near_52w_high":    ("momentum", True, "closeness to 52-week high"),
    "gross_yield_nz":   ("income", True, "grossed-up yield (NZ resident)"),
    "dividend_cover":   ("income", True, "dividend cover (EPS / DPS)"),
    "fcf_cover":        ("income", True, "dividend cover from free cash flow"),
    "volatility":       ("risk", False, "low volatility"),
    "max_drawdown":     ("risk", True, "small max drawdown"),
    "beta":             ("risk", False, "low beta to home index"),
}
GROUPS = ("value", "quality", "growth", "momentum", "income", "risk")

# Banks and insurers: deposits and policy funds look like "debt", and they have no EBIT or
# gross margin in the usual sense. These metrics are left out for them (not scored as weak),
# so they are judged on earnings yield, book value, ROE, consistency, growth and income.
FINANCIAL_SECTORS = {"financials", "banks", "insurance"}
NOT_FOR_FINANCIALS = {"ebit_ev", "fcf_yield", "roic", "margin_stability", "debt_to_equity",
                      "interest_cover", "fcf_cover", "net_debt_ebitda"}


class FX:
    """Converts between NZD and AUD (and anything else with a `<FROM><TO>` series)."""

    def __init__(self, source: DataSource):
        self.source = source
        self._cache: dict[str, Series] = {}

    def rate(self, frm: str, to: str, on: date) -> float | None:
        frm, to = frm.upper(), to.upper()
        if frm == to:
            return 1.0
        for pair, invert in ((frm + to, False), (to + frm, True)):
            if pair not in self._cache:
                self._cache[pair] = self.source.fx(pair)
            s = self._cache[pair]
            if s is not None and len(s):
                v = s.on(on, max_gap_days=10)
                if v:
                    return 1 / v if invert else v
        return None


@dataclass
class Snapshot:
    """Everything the screen knows about one listing on one date."""
    listing: Listing
    as_of: date
    price: float | None = None
    price_date: date | None = None
    market_cap_nzd: float | None = None
    traded_value_nzd: float | None = None      # median daily, last 90 days
    years_of_prices: float = 0.0
    metrics: dict[str, float | None] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)  # extra context for flags and the report
    missing: list[str] = field(default_factory=list)
    not_applicable: set[str] = field(default_factory=set)   # metrics that don't fit this kind of company


def _cagr(first: float | None, last: float | None, years: float) -> float | None:
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def monthly_returns(s: Series, start: date, end: date) -> list[tuple[date, float]]:
    w = s.window(start, end)
    out, prev = [], None
    for i, d in enumerate(w.dates):
        if i + 1 == len(w.dates) or w.dates[i + 1].month != d.month:
            if prev is not None and prev > 0:
                out.append((date(d.year, d.month, 1), w.values[i] / prev - 1))
            prev = w.values[i]
    return out


def _beta(stock: list[tuple[date, float]], bench: list[tuple[date, float]]) -> float | None:
    b = dict(bench)
    pairs = [(r, b[m]) for m, r in stock if m in b]
    if len(pairs) < 18:
        return None
    xs = [p[1] for p in pairs]
    ys = [p[0] for p in pairs]
    vx = statistics.pvariance(xs)
    if vx == 0:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs) / vx


def _max_drawdown(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    peak, mdd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd


def snapshot(listing: Listing, source: DataSource, as_of: date, fx: FX,
             benchmark: Series | None = None, marginal_rate: float = 0.33,
             prices: Series | None = None) -> Snapshot:
    snap = Snapshot(listing, as_of)
    px_all = prices if prices is not None else source.prices(listing.ticker)
    px = px_all.until(as_of)
    m = snap.metrics
    cur = listing.currency

    # --- prices -----------------------------------------------------------
    price = px.on(as_of, max_gap_days=10)
    if price is None:
        snap.missing.append("price")
    else:
        snap.price, snap.price_date = price, px.last_date
        snap.years_of_prices = (px.last_date - px.dates[0]).days / 365.25
        recent = px.window(as_of - timedelta(days=90), as_of)
        tv = [c * v for c, v in zip(recent.values, recent.volumes) if v]
        to_nzd = fx.rate(cur, "NZD", as_of)
        if tv and to_nzd:
            snap.traded_value_nzd = statistics.median(tv) * to_nzd
        p1 = px.on(add_months(as_of, -1), 10)
        p12 = px.on(add_months(as_of, -12), 10)
        m["momentum_12_1"] = (p1 / p12 - 1) if p1 and p12 else None
        year = px.window(as_of - timedelta(days=365), as_of)
        m["near_52w_high"] = price / max(year.values) if year.values else None
        snap.facts.update(technicals(px))
        rets = monthly_returns(px, add_months(as_of, -36), as_of)
        if len(rets) >= 18:
            m["volatility"] = statistics.pstdev([r for _, r in rets]) * math.sqrt(12)
            m["max_drawdown"] = _max_drawdown(px.window(add_months(as_of, -36), as_of).values)
            if benchmark is not None and len(benchmark):
                m["beta"] = _beta(rets, monthly_returns(benchmark, add_months(as_of, -36), as_of))

    # --- fundamentals (point in time) ---------------------------------------
    funds = [f for f in source.fundamentals(listing.company_id) if f.available_date <= as_of]
    funds = funds[-6:]
    if not funds:
        snap.missing.append("fundamentals")
    else:
        f = funds[-1]
        snap.facts["fundamentals_period"] = f.period_end.isoformat()
        snap.facts["fundamentals_age_days"] = (as_of - f.period_end).days
        snap.facts["fundamentals_source"] = f.source
        rep_rate = fx.rate(cur, f.currency, as_of) if price is not None else None
        # Price is per listed unit; statements count shares (CDIs can be a fraction of a share).
        mcap = (price * listing.units_per_share * rep_rate * f.shares_out
                if price is not None and rep_rate and f.shares_out else None)
        nzd = fx.rate(f.currency, "NZD", as_of)
        snap.market_cap_nzd = mcap * nzd if mcap and nzd else None
        financial = listing.sector.lower() in FINANCIAL_SECTORS
        _value_quality(m, snap.facts, funds, mcap)
        _strength(m, snap.facts, funds, financial)
        if financial:
            snap.not_applicable = set(NOT_FOR_FINANCIALS)

    # --- dividends ---------------------------------------------------------
    _income(snap, source, funds, fx, marginal_rate)

    for k in METRICS:
        m.setdefault(k, None)
    for k in snap.not_applicable:
        m[k] = None
    return snap


def _value_quality(m: dict, facts: dict, funds: list[Fundamentals], mcap: float | None) -> None:
    f = funds[-1]
    fcf = (f.operating_cash_flow - (f.capex or 0.0)) if f.operating_cash_flow is not None else None
    facts["fcf"] = fcf
    ev = mcap + (f.debt or 0.0) - (f.cash or 0.0) if mcap else None
    if mcap:
        m["earnings_yield"] = f.net_income / mcap if f.net_income is not None else None
        m["book_to_price"] = f.equity / mcap if f.equity is not None else None
        m["fcf_yield"] = fcf / mcap if fcf is not None else None
        m["ebit_ev"] = f.ebit / ev if f.ebit is not None and ev and ev > 0 else None
    if f.equity and f.equity > 0:
        m["roe"] = f.net_income / f.equity if f.net_income is not None else None
        m["debt_to_equity"] = (f.debt or 0.0) / f.equity
    elif f.equity is not None:
        m["debt_to_equity"] = 10.0   # negative equity: treat as very highly geared
        facts["negative_equity"] = True
    invested = (f.equity or 0.0) + (f.debt or 0.0) - (f.cash or 0.0)
    if f.ebit is not None and invested > 0:
        m["roic"] = f.ebit * (1 - 0.28) / invested
    if f.ebit is not None and f.interest_expense is not None:
        m["interest_cover"] = min(f.ebit / f.interest_expense, 50.0) if f.interest_expense > 0 else 50.0
    facts["interest_cover"] = m.get("interest_cover")

    margins = [x.gross_profit / x.revenue for x in funds[-5:] if x.gross_profit is not None and x.revenue]
    if len(margins) >= 3:
        m["margin_stability"] = -statistics.pstdev(margins)
    eps = [x.eps for x in funds[-6:]]
    steps = [(a, b) for a, b in zip(eps, eps[1:]) if a is not None and b is not None]
    if len(steps) >= 2:
        m["earnings_consistency"] = sum(1 for a, b in steps if b > a) / len(steps)

    hist = funds[-6:]
    if len(hist) >= 3:
        yrs = (hist[-1].period_end - hist[0].period_end).days / 365.25
        m["revenue_growth"] = _cagr(hist[0].revenue, hist[-1].revenue, yrs)
        m["eps_growth"] = _cagr(hist[0].eps, hist[-1].eps, yrs)
    older = [x for x in funds if (f.period_end - x.period_end).days >= 3 * 360]
    if older and older[-1].shares_out and f.shares_out:
        yrs = (f.period_end - older[-1].period_end).days / 365.25
        facts["share_count_growth"] = _cagr(older[-1].shares_out, f.shares_out, yrs)
    facts["eps"] = f.eps
    facts["currency"] = f.currency


def _income(snap: Snapshot, source: DataSource, funds: list[Fundamentals], fx: FX, marginal_rate: float) -> None:
    lst, m, d = snap.listing, snap.metrics, snap.as_of
    divs = [x for x in source.dividends(lst.ticker) if d - timedelta(days=365) < x.ex_date <= d]
    if snap.price is None:
        return
    is_fif = mr.fif_class(lst.domicile, lst.security_type, lst.in_all_ords(d), lst.franking_account)[0] == mr.FIF
    cash = gross = net = headline = 0.0
    missing: set[str] = set()
    for x in divs:
        rate = fx.rate(x.currency, lst.currency, x.ex_date) or 1.0
        t = mr.dividend_for_nz_resident(x.amount * rate, lst.domicile, imputed_pct=x.imputed_pct,
                                        franked_pct=x.franked_pct, nz_credit_pct=x.nz_credit_pct,
                                        marginal_rate=marginal_rate, is_fif=is_fif, on=d)
        cash += t["cash"]
        gross += t["gross_nz"]
        net += t["net"]
        headline += mr.australian_headline_gross(x.amount * rate, x.franked_pct, d) if lst.domicile == "AU" else t["gross_nz"]
        missing.update(t["missing"])
    snap.facts.update(dps_ttm=cash, cash_yield=cash / snap.price, after_tax_yield=net / snap.price,
                      headline_yield=headline / snap.price, tax_data_missing=sorted(missing),
                      dividends_counted=len(divs))
    m["gross_yield_nz"] = gross / snap.price
    if funds and cash > 0:
        f = funds[-1]
        to_rep = fx.rate(lst.currency, f.currency, d) or 1.0
        dps = cash * to_rep * lst.units_per_share   # per share, like EPS
        # Capped at 5x: beyond that, more cover says nothing more about the dividend's safety.
        if f.eps is not None:
            m["dividend_cover"] = min(f.eps / dps, 5.0)
        fcf = snap.facts.get("fcf")
        if fcf is not None and f.shares_out:
            m["fcf_cover"] = min((fcf / f.shares_out) / dps, 5.0)
    snap.facts["dividend_cover"] = m.get("dividend_cover")


def technicals(px: Series) -> dict[str, float | None]:
    """Context only, never scored: RSI(14), distance from 50/200-day averages, volume surge."""
    v = px.values
    out: dict[str, float | None] = {"rsi14": None, "vs_ma50": None, "vs_ma200": None, "volume_surge": None}
    if len(v) >= 15:
        gains = losses = 0.0
        # Wilder's smoothing over the whole available history (last 250 days).
        window = v[-251:]
        diffs = [b - a for a, b in zip(window, window[1:])]
        avg_g = sum(max(d, 0) for d in diffs[:14]) / 14
        avg_l = sum(max(-d, 0) for d in diffs[:14]) / 14
        for d in diffs[14:]:
            avg_g = (avg_g * 13 + max(d, 0)) / 14
            avg_l = (avg_l * 13 + max(-d, 0)) / 14
        gains, losses = avg_g, avg_l
        out["rsi14"] = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
    if len(v) >= 50:
        out["vs_ma50"] = v[-1] / (sum(v[-50:]) / 50) - 1
    if len(v) >= 200:
        out["vs_ma200"] = v[-1] / (sum(v[-200:]) / 200) - 1
    vol = px.volumes
    if len(vol) >= 120:
        base = sum(vol[-120:]) / 120
        out["volume_surge"] = (sum(vol[-5:]) / 5) / base if base else None
    return out


# Piotroski's nine tests, as far as the data allows. Banks and insurers skip the leverage,
# liquidity and gross-margin tests, which don't mean the same for them.
F_TESTS = ("Profitable (ROA > 0)", "Operating cash flow positive", "ROA improved",
           "Cash flow above profit (earnings quality)", "Long-term debt / assets fell",
           "Current ratio improved", "No new shares issued", "Gross margin improved", "Asset turnover improved")


def _strength(m: dict, facts: dict, funds: list[Fundamentals], financial: bool) -> None:
    f = funds[-1]
    prev = funds[-2] if len(funds) >= 2 else None
    prev2 = funds[-3] if len(funds) >= 3 else None

    def div(a, b):
        return a / b if a is not None and b not in (None, 0) else None
    # Ratios worth seeing even when not scored
    facts["current_ratio"] = None if financial else div(f.current_assets, f.current_liabilities)
    facts["payout_ratio"] = div(f.dividends_paid, f.net_income) if (f.net_income or 0) > 0 else None
    facts["rnd_pct"] = div(f.rnd, f.revenue)
    facts["asset_turnover"] = div(f.revenue, f.total_assets)
    if not financial and f.ebitda and f.ebitda > 0 and f.debt is not None:
        m["net_debt_ebitda"] = (f.debt - (f.cash or 0.0)) / f.ebitda
    facts["net_debt"] = (f.debt - (f.cash or 0.0)) if f.debt is not None else None

    if prev is None:
        return
    roa = div(f.net_income, prev.total_assets)              # ROA on beginning-of-year assets
    roa_prev = div(prev.net_income, prev2.total_assets) if prev2 else None
    lev = div(f.long_term_debt, f.total_assets)
    lev_prev = div(prev.long_term_debt, prev.total_assets)
    cr, cr_prev = div(f.current_assets, f.current_liabilities), div(prev.current_assets, prev.current_liabilities)
    gm, gm_prev = div(f.gross_profit, f.revenue), div(prev.gross_profit, prev.revenue)
    at, at_prev = div(f.revenue, prev.total_assets), (div(prev.revenue, prev2.total_assets) if prev2 else None)

    def cmp(a, b, better_if_higher=True):
        if a is None or b is None:
            return None
        return a > b if better_if_higher else a < b
    tests = [
        None if roa is None else roa > 0,
        None if f.operating_cash_flow is None else f.operating_cash_flow > 0,
        cmp(roa, roa_prev),
        None if f.operating_cash_flow is None or f.net_income is None else f.operating_cash_flow > f.net_income,
        None if financial else cmp(lev, lev_prev, better_if_higher=False),
        None if financial else cmp(cr, cr_prev),
        None if not (f.shares_out and prev.shares_out) else f.shares_out <= prev.shares_out * 1.005,
        None if financial else cmp(gm, gm_prev),
        cmp(at, at_prev),
    ]
    have = [t for t in tests if t is not None]
    facts["f_score_tests"] = [(name, t) for name, t in zip(F_TESTS, tests)]
    if len(have) >= 5:
        m["f_score"] = sum(have) / len(have)
        facts["f_score_text"] = f"{sum(have)}/{len(have)}"
