"""Markdown and JSON output for screens, backtests and the watchlist."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from . import market_rules as mr
from .backtest import BacktestResult
from .factors import GROUPS, METRICS
from .portfolio import WatchResult
from .scoring import Scored
from .screen import ScreenResult

DISCLAIMER = ("> General information for our own use, not regulated financial advice. Offering it to "
              "others as personalised advice would need a Financial Advice Provider licence (NZ) or an "
              "AFSL (Australia). Scores describe the past and the data supplied; they do not predict returns.")

FIF_LABEL = {mr.NZ_LISTED: "NZ (no FIF)", mr.ASX_EXEMPT: "AU exempt", mr.FIF: "FIF"}
GROUP_WORDS = {"value": "cheap on its numbers", "quality": "a high-quality business", "growth": "growing fast",
               "momentum": "a strong price trend", "income": "a solid dividend",
               "risk": "a steady share price"}
GROUP_WEAK = {"value": "expensive", "quality": "weak on quality (returns, debt or consistency)",
              "growth": "slow growth", "momentum": "a weak price trend", "income": "little or poorly covered income",
              "risk": "a volatile share price"}


def _p(v: float | None, d: int = 1) -> str:
    return "–" if v is None else f"{v * 100:.{d}f}%"


def _n(v: float | None, d: int = 0) -> str:
    return "–" if v is None else f"{v:.{d}f}"


def _money(v: float | None) -> str:
    if v is None:
        return "–"
    if abs(v) >= 1e9:
        return f"${v / 1e9:,.1f}b"
    if abs(v) >= 1e6:
        return f"${v / 1e6:,.0f}m"
    return f"${v:,.0f}"


def why(r: Scored) -> str:
    c = sorted(r.contributions.items(), key=lambda kv: -kv[1])
    strengths = [g for g, v in c if v > 1][:2]
    weak = [g for g, v in reversed(c) if v < -1][:1]
    parts = []
    if strengths:
        parts.append("Ranks well for " + " and ".join(GROUP_WORDS[g] for g in strengths))
    if weak:
        parts.append(("but " if parts else "Held back by ") + GROUP_WEAK[weak[0]])
    if not parts:
        parts.append("Middle of the pack on every factor")
    # Best and worst single metrics, in plain English
    ms = [(k, v) for k, v in r.metric_scores.items() if v is not None]
    if ms:
        best = max(ms, key=lambda kv: kv[1])
        worst = min(ms, key=lambda kv: kv[1])
        parts_s = ", ".join(parts) + "."
        if best[1] >= 70:
            parts_s += f" Best: {METRICS[best[0]][2]} ({best[1]:.0f}/100)."
        if worst[1] <= 30:
            parts_s += f" Weakest: {METRICS[worst[0]][2]} ({worst[1]:.0f}/100)."
        return parts_s
    return ", ".join(parts) + "."


def render_screen(res: ScreenResult, top_n: int = 10, marginal_rate: float = 0.33) -> str:
    L: list[str] = []
    a = L.append
    a(f"# Share screen: NZX and ASX, {res.as_of:%d %B %Y}")
    a("")
    a(DISCLAIMER)
    a("")
    w = ", ".join(f"{g} {res.weights[g]:g}" for g in GROUPS if res.weights.get(g))
    a(f"**Data:** {res.source_name} · **Weights:** {w} · **Scored:** {len(res.ranked)} companies "
      f"· **Excluded:** {len(res.excluded)}")
    a("")
    for wn in res.warnings:
        a(f"> ⚠️ {wn}")
    if res.warnings:
        a("")
    a(f"## Top {min(top_n, len(res.ranked))}")
    a("")
    a("| # | Share | Sector | Score | Conf. | Value | Quality | Growth | Mom. | Income | Risk | NZ gross yield | Tax |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in res.ranked[:top_n]:
        lst = r.snap.listing
        g = r.group_scores
        a(f"| {r.rank} | **{lst.ticker}** {lst.name} | {lst.sector} | **{_n(r.composite)}** | {r.confidence} | "
          + " | ".join(_n(g.get(x)) for x in GROUPS)
          + f" | {_p(r.snap.metrics.get('gross_yield_nz'))} | {FIF_LABEL.get(res.fif.get(lst.ticker, ('', ''))[0], '')} |")
    a("")
    a("Scores are percentiles within this screen (50 = middle). Value and quality are also "
      "compared within the sector.")
    a("")
    a("## Why each one ranks where it does")
    a("")
    for r in res.ranked[:top_n]:
        lst, s, f = r.snap.listing, r.snap, r.snap.facts
        a(f"### {r.rank}. {lst.name} ({lst.ticker})")
        a("")
        a(why(r))
        a("")
        contrib = ", ".join(f"{g} {v:+.1f}" for g, v in sorted(r.contributions.items(), key=lambda kv: -kv[1]))
        a(f"- **Score {_n(r.composite, 1)}** = 50 {contrib} (points from each factor)")
        a(f"- Price {lst.currency} {_n(s.price, 2)} on {s.price_date} · market cap NZ{_money(s.market_cap_nzd)} · "
          f"trades NZ{_money(s.traded_value_nzd)} a day")
        m = s.metrics
        a(f"- Earnings yield {_p(m.get('earnings_yield'))} · ROE {_p(m.get('roe'))} · debt/equity "
          f"{_n(m.get('debt_to_equity'), 2)} · revenue growth {_p(m.get('revenue_growth'))} a year · "
          f"12-1 momentum {_p(m.get('momentum_12_1'))}")
        if f.get("dividends_counted"):
            line = (f"- Dividends (last 12 months): cash yield {_p(f.get('cash_yield'))}, grossed-up for an NZ resident "
                    f"{_p(m.get('gross_yield_nz'))}, after tax at {marginal_rate:.0%} {_p(f.get('after_tax_yield'))}")
            if lst.domicile == "AU":
                line += f". Australian headline (with franking) {_p(f.get('headline_yield'))}"
            a(line)
        else:
            a("- No dividends in the last 12 months")
        ft = res.fif.get(lst.ticker)
        if ft:
            a(f"- Tax: {ft[1]}")
        if lst.ticker in res.other_listings:
            a(f"- Also listed as {', '.join(res.other_listings[lst.ticker])}")
        for kind, text in res.flags.get(lst.ticker, []):
            a(f"- {'🔴' if kind == 'red' else '🟢'} {text}")
        a("")
    if len(res.ranked) > top_n:
        a("## Full ranking")
        a("")
        a("| # | Share | Exchange | Sector | Score | Conf. | Flags |")
        a("|---|---|---|---|---|---|---|")
        for r in res.ranked:
            fl = res.flags.get(r.ticker, [])
            reds = sum(1 for k, _ in fl if k == "red")
            greens = len(fl) - reds
            a(f"| {r.rank} | {r.ticker} {r.snap.listing.name} | {r.snap.listing.exchange} | {r.snap.listing.sector} | "
              f"{_n(r.composite)} | {r.confidence} | {'🔴' * reds}{'🟢' * greens} |")
        a("")
    if res.excluded:
        a("## Not scored")
        a("")
        for t, reason in sorted(res.excluded.items()):
            a(f"- {t}: {reason}")
        a("")
    a("## How this works and its limits")
    a("")
    a("- Only data available on the screen date is used. Financials count from 3 months after the "
      "period ends unless the data gives a release date.")
    a("- Australian franking credits can't be used by NZ residents, so ASX yields use the cash dividend "
      "less 15% withholding on the unfranked part. NZ imputation credits are added back for NZ companies.")
    a("- Dual-listed companies are scored once, on the listing that trades the most.")
    a("- Missing data is left out, not counted as zero; it lowers the confidence label instead.")
    a("- Free data (Yahoo) has no financial statements or franking: supply those in CSV files for full scores.")
    a("")
    a(DISCLAIMER)
    return "\n".join(L) + "\n"


def screen_json(res: ScreenResult) -> dict[str, Any]:
    out = {"as_of": res.as_of.isoformat(), "source": res.source_name, "weights": res.weights,
           "warnings": res.warnings, "excluded": res.excluded, "metrics": {k: {"group": g, "higher_is_better": h, "label": lbl}
                                                                         for k, (g, h, lbl) in METRICS.items()},
           "shares": []}
    for r in res.ranked:
        lst, s = r.snap.listing, r.snap
        out["shares"].append({
            "rank": r.rank, "ticker": lst.ticker, "name": lst.name, "exchange": lst.exchange, "sector": lst.sector,
            "currency": lst.currency, "domicile": lst.domicile, "price": s.price,
            "price_date": s.price_date.isoformat() if s.price_date else None,
            "market_cap_nzd": s.market_cap_nzd, "traded_value_nzd": s.traded_value_nzd,
            "composite": r.composite, "confidence": r.confidence, "groups": r.group_scores,
            "contributions": r.contributions, "metrics": s.metrics, "metric_scores": r.metric_scores,
            "facts": {k: v for k, v in s.facts.items() if isinstance(v, (int, float, str, list, type(None)))},
            "flags": [{"kind": k, "text": t} for k, t in res.flags.get(lst.ticker, [])],
            "fif": {"type": res.fif.get(lst.ticker, ("", ""))[0], "reason": res.fif.get(lst.ticker, ("", ""))[1]},
            "other_listings": res.other_listings.get(lst.ticker, []), "why": why(r)})
    return out


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=1, default=lambda o: o.isoformat() if isinstance(o, date) else str(o))


def render_backtest(bt: BacktestResult, cfg: dict) -> str:
    L = [f"# Backtest: top {bt.top_n} by score, monthly rebalance", "", DISCLAIMER, ""]
    a = L.append
    if not bt.returns:
        a("Not enough data to run a backtest.")
        return "\n".join(L) + "\n"
    a(f"{bt.months[0]:%b %Y} to {bt.months[-1]:%b %Y} ({len(bt.returns)} months), in NZD, after brokerage, "
      f"FX spread and tax on dividends at {float(cfg.get('marginal_tax_rate', 0.33)):.0%}.")
    a("")
    for w in bt.warnings:
        a(f"> ⚠️ {w}")
    a("")
    a("| | CAGR | Volatility | Max drawdown | Total | Months ahead |")
    a("|---|---|---|---|---|---|")
    s = bt.stats()
    a(f"| **Strategy** | {_p(s['cagr'])} | {_p(s['volatility'])} | {_p(s['max_drawdown'])} | {_p(s['total'], 0)} | |")
    for name, rets in bt.bench.items():
        b = bt.stats(rets)
        a(f"| {name} | {_p(b['cagr'])} | {_p(b['volatility'])} | {_p(b['max_drawdown'])} | {_p(b['total'], 0)} | "
          f"{_p(bt.hit_rate(name), 0)} |")
    a("")
    a(f"Turnover {bt.annual_turnover:.0%} a year · costs {_p(sum(bt.costs) / (len(bt.costs) / 12), 2)} a year.")
    a("")
    a("Latest picks: " + ", ".join(bt.holdings[-1]))
    a("")
    a("A backtest shows how a rule would have done on this data, not what it will do. Small samples, "
      "a few big winners, and data quality can all make a rule look better than it is.")
    return "\n".join(L) + "\n"


def render_watch(w: WatchResult, as_of: date) -> str:
    L = [f"# Your shares, {as_of:%d %B %Y}", "", DISCLAIMER, ""]
    a = L.append
    a(f"Total NZ{_money(w.total_value)} across {len(w.positions)} holdings.")
    a("")
    a("| Share | Owner | Value (NZD) | Dividends, 12 mo (after tax) | Rank | Score | Tax | Note |")
    a("|---|---|---|---|---|---|---|---|")
    for p in sorted(w.positions, key=lambda p: -(p.value_nzd or 0)):
        divs = f"{_money(p.dividends_nzd)} ({_money(p.dividends_after_tax_nzd)})" if p.dividends_nzd else "–"
        a(f"| {p.ticker} {p.name} | {p.owner} | {_money(p.value_nzd)} | {divs} | {p.rank or '–'} | {_n(p.composite)} | "
          f"{FIF_LABEL.get(p.fif_type, '')} | {p.note} |")
    a("")
    if w.alerts:
        a("## Things to look at")
        a("")
        for x in w.alerts:
            a(f"- {x}")
        a("")
    if w.total_value:
        a("## Mix")
        a("")
        a("By sector: " + ", ".join(f"{k} {v / w.total_value:.0%}" for k, v in sorted(w.by_sector.items(), key=lambda kv: -kv[1])))
        a("")
        a("By company home: " + ", ".join(f"{k} {v / w.total_value:.0%}" for k, v in sorted(w.by_country.items(), key=lambda kv: -kv[1])))
        a("")
    if w.fif:
        a("## FIF (per person, on cost)")
        a("")
        a("| Person | FIF cost | Headroom to $50k | Over? | Estimated FDR tax |")
        a("|---|---|---|---|---|")
        for person, f in w.fif.items():
            a(f"| {person} | {_money(f['cost'])} | {_money(f['headroom'])} | {'yes' if f['over_threshold'] else 'no'} | "
              f"{_money(f['estimated_tax'])} |")
        a("")
    if w.satellite:
        s = w.satellite
        a("## Room in your share satellite")
        a("")
        other = f" (including NZ{_money(s['other_direct'])} of other overseas shares from your profile)" if s.get("other_direct") else ""
        a(f"Your kiwiwealth plan puts {1 - s['core_pct']:.0%} of investments (NZ{_money(s['target'])}) in "
          f"individual shares. You hold NZ{_money(s['current'])}{other}, leaving **NZ{_money(max(s['room'], 0))}**.")
        a("")
    if w.ideas:
        swaps = any(i.get("swap_for") for i in w.ideas)
        if swaps:
            a("## Swap ideas (no room for new money)")
            a("")
            a("Your satellite is full, so these pair a higher-ranked share with one of your lowest-ranked "
              "holdings. Selling has costs and may have tax: only swap if the reasons hold up.")
        else:
            a("## Ideas that fit your plan")
            a("")
            a("High-ranked shares you don't hold, sized to your single-share and sector limits. "
              "Read the screen's reasons before buying anything.")
        a("")
        a("| Share | Rank | Score | Sector | Parcel | Round-trip cost | Tax |" + (" Instead of |" if swaps else ""))
        a("|---|---|---|---|---|---|---|" + ("---|" if swaps else ""))
        for i in w.ideas:
            a(f"| {i['ticker']} {i['name']} | {i['rank']} | {_n(i['composite'])} | {i['sector']} | "
              f"NZ{_money(i['parcel_nzd'])} | {_p(i['round_trip_cost_pct'], 2)} | {FIF_LABEL.get(i['fif_type'], '')} |"
              + (f" {i['swap_for']} ({i['swap_note']}) |" if swaps else ""))
        a("")
    a(DISCLAIMER)
    return "\n".join(L) + "\n"
