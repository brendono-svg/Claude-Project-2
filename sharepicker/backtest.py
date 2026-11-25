"""Monthly rebalance backtest of the top-N shares by composite score.

Honesty rules:
- At each month end the screen sees only data available then (see factors.py).
- Returns include dividends after NZ tax, brokerage, FX spread and AUD/NZD moves.
- A share that delists mid-month exits at its last traded price.
- Without delisted shares in the data, results suffer survivorship bias; the result says so.
FIF tax (5% FDR) on non-exempt ASX holdings is not modelled.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date

from . import market_rules as mr
from .data import DataSource
from .factors import FX
from .models import Series
from .screen import DEFAULT_BENCHMARKS, run_screen


@dataclass
class BacktestResult:
    start: date
    end: date
    top_n: int
    months: list[date] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    bench: dict[str, list[float | None]] = field(default_factory=dict)  # aligned with months; None = no data
    holdings: list[list[str]] = field(default_factory=list)
    turnover: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def stats(self, rets: list[float] | None = None) -> dict[str, float | None]:
        r = [x for x in (self.returns if rets is None else rets) if x is not None]
        if not r:
            return {"cagr": None, "volatility": None, "max_drawdown": None, "total": None}
        growth, peak, mdd = 1.0, 1.0, 0.0
        for x in r:
            growth *= 1 + x
            peak = max(peak, growth)
            mdd = min(mdd, growth / peak - 1)
        yrs = len(r) / 12
        return {"cagr": growth ** (1 / yrs) - 1 if growth > 0 else -1.0,
                "volatility": statistics.pstdev(r) * math.sqrt(12) if len(r) > 1 else None,
                "max_drawdown": mdd, "total": growth - 1}

    def hit_rate(self, bench: str) -> float | None:
        b = self.bench.get(bench)
        pairs = [(x, y) for x, y in zip(self.returns, b or []) if y is not None]
        if not pairs:
            return None
        return sum(1 for x, y in pairs if x > y) / len(pairs)

    @property
    def annual_turnover(self) -> float:
        return statistics.fmean(self.turnover) * 12 if self.turnover else 0.0


def _month_end_dates(series: Series, start: date, end: date) -> list[date]:
    out = []
    w = series.window(start, end)
    for i, d in enumerate(w.dates):
        if i + 1 == len(w.dates) or w.dates[i + 1].month != d.month:
            out.append(d)
    return out


def run_backtest(cfg: dict, source: DataSource, start: date, end: date) -> BacktestResult:
    bt = cfg.get("backtest") or {}
    top_n = int(bt.get("top_n", 8))
    capital = float(bt.get("capital_nzd", 50_000))
    broker = bt.get("broker", "sharesies")
    brokers = cfg.get("brokers")
    marginal = float(cfg.get("marginal_tax_rate", 0.33))
    res = BacktestResult(start, end, top_n)

    fx = FX(source)
    bench_names = {**DEFAULT_BENCHMARKS, **(cfg.get("benchmarks") or {})}
    benches = {ex: source.prices(t) for ex, t in bench_names.items()}
    universe = source.universe()
    cache = {lst.ticker: source.prices(lst.ticker) for lst in universe}
    by_ticker = {lst.ticker: lst for lst in universe}
    cal = next((b for b in benches.values() if len(b)), None)
    if cal is None:
        cal = max(cache.values(), key=len)
    dates = _month_end_dates(cal, start, end)
    if len(dates) < 2:
        res.warnings.append("Not enough months of data for a backtest.")
        return res

    held: list[str] = []
    for d0, d1 in zip(dates, dates[1:]):
        scr = run_screen(cfg, source, d0, benchmarks=benches, fx=fx, price_cache=cache, with_flags=False)
        picks = [r.ticker for r in scr.ranked if r.composite is not None][:top_n]
        if not picks:
            continue
        changed = len(set(picks) ^ set(held)) / 2 if held else len(picks)
        turnover = changed / len(picks)
        cost = 0.0
        parcel = capital / len(picks)
        for t in set(picks) ^ set(held) if held else picks:
            lst = by_ticker.get(t)
            if lst is None:
                continue
            ex = lst.exchange if lst.exchange in ("NZX", "ASX") else "ASX"
            cost += mr.trade_cost(parcel, ex, broker, brokers=brokers) / capital
        held = picks

        rets = []
        for t in picks:
            lst = by_ticker[t]
            s = cache[t]
            p0 = s.on(d0, 10)
            p1 = s.on(d1, 40) or (s.values[s.index_on(d1)] if s.index_on(d1) >= 0 else None)
            if not p0 or not p1:
                continue
            divs = 0.0
            fif = mr.fif_class(lst.domicile, lst.security_type, lst.in_all_ords(d0), lst.franking_account)[0] == mr.FIF
            for dv in source.dividends(t):
                if d0 < dv.ex_date <= d1:
                    rate = fx.rate(dv.currency, lst.currency, dv.ex_date) or 1.0
                    divs += mr.dividend_for_nz_resident(dv.amount * rate, lst.domicile, imputed_pct=dv.imputed_pct,
                                                        franked_pct=dv.franked_pct, nz_credit_pct=dv.nz_credit_pct,
                                                        marginal_rate=marginal, is_fif=fif, on=dv.ex_date)["net"]
            local = (p1 + divs) / p0
            f0, f1 = fx.rate(lst.currency, "NZD", d0), fx.rate(lst.currency, "NZD", d1)
            rets.append(local * ((f1 / f0) if f0 and f1 else 1.0) - 1)
        if not rets:
            continue
        res.months.append(d1)
        res.returns.append(statistics.fmean(rets) - cost)
        res.costs.append(cost)
        res.turnover.append(turnover)
        res.holdings.append(picks)
        for ex, b in benches.items():
            if not len(b):
                continue
            b0, b1 = b.on(d0, 10), b.on(d1, 10)
            r = None
            if b0 and b1:
                cur = "AUD" if ex == "ASX" else "NZD"
                f0, f1 = fx.rate(cur, "NZD", d0), fx.rate(cur, "NZD", d1)
                r = b1 / b0 * ((f1 / f0) if f0 and f1 else 1) - 1
            # Pad so every benchmark list lines up month by month with the strategy's returns.
            res.bench.setdefault(bench_names[ex], [None] * (len(res.returns) - 1)).append(r)
    if len(res.bench) > 1:
        res.bench["50/50 blend"] = [None if any(v is None for v in x) else statistics.fmean(x)
                                    for x in zip(*res.bench.values())]
    if not any(lst.listed_to for lst in universe):
        res.warnings.append("No delisted shares in the data: survivorship bias flatters these results.")
    res.warnings.append("Benchmarks are the index series supplied; if they are price indexes (no dividends), "
                        "the comparison favours the strategy.")
    return res

