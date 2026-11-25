"""Extra detail for research briefs: accounts over four years, financial strength tests,
returns against the index, dividend history and valuation against the company's own past."""
from __future__ import annotations

from datetime import date

from .data import add_months
from .models import Dividend, Fundamentals, Listing, Series


def _p(v, d=1):
    return "–" if v is None else f"{v * 100:.{d}f}%"


def _m(v):
    if v is None:
        return "–"
    a = abs(v)
    s = f"{a / 1e9:,.2f}b" if a >= 1e9 else f"{a / 1e6:,.0f}m" if a >= 1e6 else f"{a:,.0f}"
    return ("-" if v < 0 else "") + s


def financials_table(funds: list[Fundamentals], as_of: date) -> list[str]:
    f = [x for x in funds if x.period_end <= as_of][-4:]
    if not f:
        return []
    cur = f[-1].currency
    head = "| " + cur + " | " + " | ".join(f"FY{x.period_end:%y} ({x.period_end:%b})" for x in f) + " |"
    rows = [head, "|---|" + "---|" * len(f)]

    def row(label, fn):
        rows.append(f"| {label} | " + " | ".join(fn(x) for x in f) + " |")
    row("Revenue", lambda x: _m(x.revenue))
    row("EBITDA", lambda x: _m(x.ebitda))
    row("Net profit", lambda x: _m(x.net_income))
    row("EPS", lambda x: "–" if x.eps is None else f"{x.eps:.3f}")
    row("Operating cash flow", lambda x: _m(x.operating_cash_flow))
    row("Free cash flow", lambda x: _m(x.free_cash_flow if x.free_cash_flow is not None else
                                       (x.operating_cash_flow - (x.capex or 0)) if x.operating_cash_flow is not None else None))
    row("Net debt", lambda x: _m(None if x.debt is None else x.debt - (x.cash or 0)))
    row("Return on equity", lambda x: _p(x.net_income / x.equity) if x.net_income is not None and x.equity else "–")
    row("Dividends paid", lambda x: _m(x.dividends_paid))
    if any(x.rnd for x in f):
        row("R&D", lambda x: _m(x.rnd))
    return rows


def strength_lines(facts: dict) -> list[str]:
    tests = facts.get("f_score_tests")
    if not tests or all(ok is None for _, ok in tests):
        return []
    out = [f"**Financial strength {facts.get('f_score_text', 'n/a')}** (Piotroski tests with data):", ""]
    for name, ok in tests:
        out.append(f"- {'✅' if ok else '❌' if ok is False else '➖ no data / not applicable'} {name}")
    extras = []
    if facts.get("current_ratio") is not None:
        extras.append(f"current ratio {facts['current_ratio']:.2f}")
    if facts.get("payout_ratio") is not None:
        extras.append(f"payout {facts['payout_ratio']:.0%} of profit")
    if facts.get("rnd_pct"):
        extras.append(f"R&D {facts['rnd_pct']:.1%} of revenue")
    if facts.get("asset_turnover") is not None:
        extras.append(f"asset turnover {facts['asset_turnover']:.2f}x")
    if extras:
        out += ["", "Also: " + " · ".join(extras) + "."]
    return out


PERIODS = (("1 month", 1), ("3 months", 3), ("6 months", 6), ("1 year", 12), ("3 years", 36), ("5 years", 60))


def performance_table(stock: Series, index: Series | None, as_of: date) -> list[str]:
    p0 = stock.on(as_of, 10)
    if not p0:
        return []
    rows = ["| Period | Share price | Index | Difference |", "|---|---|---|---|"]
    i0 = index.on(as_of, 10) if index is not None and len(index) else None
    for label, months in PERIODS:
        d = add_months(as_of, -months)
        p = stock.on(d, 10)
        if not p:
            continue
        r = p0 / p - 1
        b = (i0 / index.on(d, 10) - 1) if i0 and index.on(d, 10) else None
        yrs = months / 12
        ann = f" ({(1 + r) ** (1 / yrs) - 1:+.1%} a year)" if yrs >= 3 and r > -1 else ""
        rows.append(f"| {label} | {r:+.1%}{ann} | {'–' if b is None else f'{b:+.1%}'} | "
                    f"{'–' if b is None else f'{r - b:+.1%}'} |")
    rows += ["", "Price only (dividends excluded); the index is the home exchange's."]
    return rows


def dividend_history(divs: list[Dividend], as_of: date, currency: str) -> list[str]:
    by_year: dict[int, float] = {}
    for d in divs:
        if d.ex_date <= as_of:
            by_year[d.ex_date.year] = by_year.get(d.ex_date.year, 0.0) + d.amount
    if not by_year:
        return ["No dividends in the price history."]
    years = list(range(max(min(by_year), as_of.year - 9), as_of.year + 1))
    rows = ["| Year | " + " | ".join(str(y) + ("*" if y == as_of.year else "") for y in years) + " |",
            "|---|" + "---|" * len(years),
            f"| {currency} per share | " + " | ".join(f"{by_year.get(y, 0):.3f}" for y in years) + " |"]
    full = [y for y in years if y < as_of.year]
    streak = 0
    for y in reversed(full):
        if by_year.get(y):
            streak += 1
        else:
            break
    notes = [f"Paid in each of the last {streak} full years" if streak else "No dividend in the last full year"]
    if len(full) >= 6 and by_year.get(full[-6]) and by_year.get(full[-1]):
        notes.append(f"5-year growth {((by_year[full[-1]] / by_year[full[-6]]) ** 0.2 - 1):+.1%} a year")
    cuts = [y for a, y in zip(full, full[1:]) if by_year.get(a) and by_year.get(y, 0) < by_year[a] * 0.9]
    if cuts:
        notes.append("cut by more than 10% in " + ", ".join(map(str, cuts)))
    return rows + ["", "* this year so far. " + "; ".join(notes) + "."]


def valuation_history(funds: list[Fundamentals], stock: Series, lst: Listing, fx_rate, as_of: date) -> list[str]:
    """P/E and price/book on the day each year's results became available, and now."""
    rows = ["| Date | Price | P/E | Price / book |", "|---|---|---|---|"]
    pts = [(x.available_date, x) for x in funds if x.available_date <= as_of][-4:]
    latest = [x for x in funds if x.available_date <= as_of]
    if latest:
        pts.append((as_of, latest[-1]))
    pes = []
    for d, f in pts:
        p = stock.on(d, 10)
        if not p or not f.shares_out:
            continue
        rate = fx_rate(lst.currency, f.currency, d) or 1.0
        price_rep = p * rate * lst.units_per_share
        pe = price_rep / f.eps if f.eps and f.eps > 0 else None
        pb = price_rep * f.shares_out / f.equity if f.equity and f.equity > 0 else None
        if pe and d != as_of:
            pes.append(pe)
        rows.append(f"| {'**now**' if d == as_of else d.isoformat()} | {lst.currency} {p:.2f} | "
                    f"{'–' if pe is None else f'{pe:.1f}'} | {'–' if pb is None else f'{pb:.2f}'} |")
    if len(rows) <= 2:
        return []
    if pes:
        rows += ["", f"Average P/E at past result dates: {sum(pes) / len(pes):.1f} (uses each year's own earnings)."]
    return rows


def shorts_line(now: float | None, month_ago: float | None) -> str | None:
    if now is None:
        return None
    trend = ""
    if month_ago is not None:
        ch = now - month_ago
        trend = f", {'up' if ch > 0 else 'down'} from {month_ago:.2%} a month earlier" if abs(ch) >= 0.0005 else ", steady over the month"
    return f"Short interest (ASIC): {now:.2%} of shares{trend}."

