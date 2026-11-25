"""`brief`: one share's research file. `digest`: what changed since the last saved screen.

Both are built from local data only. `--for-claude` adds a prompt you can paste into Claude
(or any assistant) together with the primary sources: no API is called from here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from .detail import dividend_history, financials_table, performance_table, shorts_line, strength_lines, valuation_history
from .factors import FX, METRICS
from .models import Series
from .report import DISCLAIMER, FIF_LABEL, _money, _n, _p, why
from .research import (Announcement, ResearchStore, _flag_key, checklist, load_history, memory_for, price_reaction,
                       results_card, source_links)
from .screen import ScreenResult


@dataclass
class Context:
    """What the research commands need besides the screen itself."""
    source: Any
    store: ResearchStore | None
    benchmarks: dict[str, Series]
    history: list[dict] = field(default_factory=list)
    websites: dict[str, str] = field(default_factory=dict)


def announcements_with_reactions(ctx: Context, ticker: str, exchange: str, as_of: date,
                                 days: int = 365) -> list[Announcement]:
    if not ctx.store:
        return []
    stock = ctx.source.prices(ticker)
    index = ctx.benchmarks.get(exchange)
    out = []
    for a in ctx.store.announcements(ticker, as_of):
        if (as_of - a.date).days > days:
            continue
        # Work on a copy: the store keeps base relevance, so repeated calls give the same answer.
        reaction = price_reaction(stock.until(as_of), index, a.date)
        bump = 0.1 if a.price_sensitive else 0.0
        bump += min(0.2, abs(reaction) * 2) if reaction is not None else 0.0
        out.append(replace(a, reaction=reaction, relevance=min(1.0, a.relevance + bump)))
    return out


def research_for(r, res: ScreenResult, ctx: Context) -> dict[str, Any]:
    lst = r.snap.listing
    funds = ctx.source.fundamentals(lst.company_id)
    return checklist(r, res, ctx.store, funds, res.as_of, ctx.history)


def _find(res: ScreenResult, ticker: str):
    t = ticker.upper()
    r = next((x for x in res.ranked if x.ticker.upper() == t), None)
    if r is None:
        alias = {o: m for m, os in res.other_listings.items() for o in os}
        if t in alias:
            r = next(x for x in res.ranked if x.ticker == alias[t])
    return r


def render_brief(res: ScreenResult, ticker: str, ctx: Context, for_claude: bool = False,
                 marginal_rate: float = 0.33) -> str | None:
    r = _find(res, ticker)
    if r is None:
        return None
    s, lst, m, f = r.snap, r.snap.listing, r.snap.metrics, r.snap.facts
    as_of = res.as_of
    rs = research_for(r, res, ctx)
    funds = ctx.source.fundamentals(lst.company_id)
    card = results_card(funds, as_of)
    anns = announcements_with_reactions(ctx, lst.ticker, lst.exchange, as_of)
    L: list[str] = []
    a = L.append
    a(f"# {lst.name} ({lst.ticker}): research brief, {as_of:%d %B %Y}")
    a("")
    a(DISCLAIMER)
    a("")
    other = res.other_listings.get(lst.ticker)
    prof = (ctx.store.profiles.get(lst.ticker) if ctx.store else None) or {}
    if prof.get("name") or prof.get("industry"):
        a(f"**{prof.get('name') or lst.name}** · {prof.get('industry') or lst.sector}")
        a("")
    comp = (ctx.store.companies.get(lst.company_id) if ctx.store else None) or {}
    facts_line = [x for x in (
        f"listed since {comp['first_listed'][:4]}" if comp.get("first_listed") else "",
        f"year end {comp['year_end']}" if comp.get("year_end") else "",
        f"auditor {comp['auditor']}" if comp.get("auditor") else "") if x]
    if facts_line:
        a(" · ".join(facts_line).capitalize())
        a("")
    a(f"{lst.exchange} · {lst.sector} · {lst.domicile}-resident · price {lst.currency} {_n(s.price, 2)} · "
      f"market cap NZ{_money(s.market_cap_nzd)}" + (f" · also listed as {', '.join(other)}" if other else ""))
    a("")
    a("## Research checklist")
    a("")
    a("Separate lenses, deliberately not combined into one verdict. Disagreement between them is "
      "where the research is.")
    a("")
    for k, v in rs["lenses"].items():
        a(f"- **{k.replace('_', ' ').capitalize()}:** {v}")
    a("")
    a("## Why it scores where it does")
    a("")
    a(why(r))
    a("")
    a("| Factor | Score | Points added |")
    a("|---|---|---|")
    for g, v in r.group_scores.items():
        a(f"| {g} | {_n(v)} | {r.contributions.get(g, 0):+.1f} |")
    a("")

    entry = rs["journal"]
    a("## Your thesis")
    a("")
    if entry is None:
        a("No thesis yet. Add one to `journal.yaml` in the data folder: what you expect, and the "
          "rules that would prove you wrong (for example `dividend_cover < 1.2`).")
    else:
        a(f"> {entry.thesis}")
        a("")
        if entry.added:
            a(f"Written {entry.added}" + (f" · review by {entry.review_by}" if entry.review_by else ""))
            a("")
        for x in rs["rules"]:
            icon = {"broken": "🔴 broken", "ok": "🟢 ok", "unknown": "⚪ no data"}[x["status"]]
            val = "" if x["value"] is None else (f" (now {x['value']:.1%})" if "%" in x["rule"] else f" (now {x['value']:.3g})")
            a(f"- `{x['rule']}`: {icon}{val}")
        for w in entry.watch_for:
            a(f"- Watch for: {w}")
    notes = ctx.store.notes(lst.ticker) if ctx.store else []
    if notes:
        a("")
        a("Your notes: " + ", ".join(f"`{p.name}`" for p in notes))
    a("")

    a("## Results")
    a("")
    nr = rs["next_results"]
    if nr:
        extra = f", deadline {nr['deadline']:%d %b %Y}" if nr.get("deadline") and not nr["basis"].startswith("listing-rule deadline") else ""
        a(f"**Next:** {nr.get('kind', 'results')} on {nr['date']:%d %b %Y} ({nr['basis']}{extra}).")
        a("")
    if card:
        a(f"Latest full year to {card['period_end']:%d %b %Y} ({card['currency']}):")
        a("")
        a("| | Amount | Change |")
        a("|---|---|---|")
        a(f"| Revenue | {_money(card['revenue'])} | {_p(card['revenue_change'])} |")
        a(f"| Net profit | {_money(card['net_income'])} | {_p(card['net_income_change'])} |")
        a(f"| EPS | {_n(card['eps'], 3)} | {_p(card['eps_change'])} |")
        a(f"| Dividend per share | {_n(card['dps'], 3)} | {_p(card['dps_change'])} |")
        a(f"| Cash conversion (operating cash / profit) | {_n(card['cash_conversion'], 2)} | |")
        a("")
    res_anns = [x for x in anns if x.category in ("results", "quarterly") and x.reaction is not None]
    if res_anns:
        a("Share price reaction to recent results (vs the index, close before to day after): "
          + ", ".join(f"{x.date:%b %Y} {x.reaction:+.1%}" for x in res_anns[-4:]))
        a("")
    if not card and not nr:
        a("No financial statements in the data. Add them to fundamentals.csv (see README).")
        a("")

    upcoming = [e for e in (ctx.store.events(lst.ticker) if ctx.store else [])
                if as_of <= e.date <= as_of + timedelta(days=120)]
    if upcoming:
        a("## Coming up")
        a("")
        for e in upcoming:
            a(f"- {e.date:%d %b %Y}: {e.kind.replace('_', ' ')}{' (unconfirmed)' if not e.confirmed else ''}"
              f"{' – ' + e.note if e.note else ''}")
        a("")

    a("## Announcements, last 12 months (most relevant first)")
    a("")
    if anns:
        a("| Date | Type | Headline | Reaction vs index |")
        a("|---|---|---|---|")
        # (source: NZX/ASX feeds, your announcements.csv)
        top = sorted(anns, key=lambda x: (-x.relevance, x.date))[:12]
        for x in sorted(top, key=lambda x: x.date, reverse=True):
            head = f"[{x.headline}]({x.url})" if x.url else x.headline
            a(f"| {x.date:%d %b %Y} | {x.label} | {head} | {_p(x.reaction) if x.reaction is not None else '–'} |")
    else:
        a("None in the data. Export them from the NZX or ASX announcements page into announcements.csv.")
    a("")

    fin = financials_table(funds, as_of)
    if fin:
        a("## Accounts, last four years")
        a("")
        L.extend(fin)
        src = next((x.source for x in reversed(funds) if x.period_end <= as_of), "")
        a("")
        a(f"Source: {src or 'your data'}." + (" Figures are as restated, not as first reported." if "restated" in src else ""))
        a("")
    st = strength_lines(f)
    if st:
        a("## Financial strength")
        a("")
        L.extend(st)
        a("")
    stock = ctx.source.prices(lst.ticker).until(as_of)
    perf = performance_table(stock, ctx.benchmarks.get(lst.exchange), as_of)
    if perf:
        a("## Returns against the index")
        a("")
        L.extend(perf)
        a("")
    a("## Dividend history")
    a("")
    L.extend(dividend_history(ctx.source.dividends(lst.ticker), as_of, lst.currency))
    a("")
    val = valuation_history(funds, stock, lst, FX(ctx.source).rate, as_of)
    if val:
        a("## Valuation against its own history")
        a("")
        L.extend(val)
        a("")
    news = (ctx.store.news(lst.ticker, as_of) if ctx.store else [])[:8]
    if news:
        a("## In the news")
        a("")
        for x in news:
            head = f"[{x.headline}]({x.url})" if x.url.startswith("https://") else x.headline
            tag = " (broker view)" if x.category == "broker_rating" else ""
            a(f"- {x.date:%d %b}: {head}{' – ' + x.publisher if x.publisher else ''}{tag}")
        a("")
        a("Headlines from Google News; open them for the full story. Media and broker views are opinions.")
        a("")
    people = comp.get("people") or []
    if people:
        a("## Board and management (NZX)")
        a("")
        for x in people[:12]:
            a(f"- {x['name']}: {x['role']}")
        a("")
    a("## Numbers")
    a("")
    keys = ["earnings_yield", "ebit_ev", "fcf_yield", "roe", "roic", "debt_to_equity", "interest_cover",
            "revenue_growth", "eps_growth", "momentum_12_1", "volatility", "beta"]
    a("| Metric | Value | Percentile |")
    a("|---|---|---|")
    for k in keys:
        v, sc = m.get(k), r.metric_scores.get(k)
        val = _n(v, 2) if k in ("debt_to_equity", "interest_cover", "beta") else _p(v)
        a(f"| {METRICS[k][2]} | {val} | {_n(sc)} |")
    a("")
    a("## Dividends and tax")
    a("")
    if f.get("dividends_counted"):
        a(f"Last 12 months: cash yield {_p(f.get('cash_yield'))}, grossed-up for an NZ resident "
          f"{_p(m.get('gross_yield_nz'))}, after tax at {marginal_rate:.0%} {_p(f.get('after_tax_yield'))}"
          + (f". Australian headline {_p(f.get('headline_yield'))} includes franking you can't use." if lst.domicile == "AU" else "."))
    else:
        a("No dividends in the last 12 months.")
    t = res.fif.get(lst.ticker)
    if t:
        a("")
        a(f"{FIF_LABEL.get(t[0], t[0])}: {t[1]}.")
    a("")
    a("## Technical context (not scored)")
    a("")
    a(f"RSI(14) {_n(f.get('rsi14'))} · vs 50-day average {_p(f.get('vs_ma50'))} · vs 200-day average "
      f"{_p(f.get('vs_ma200'))} · volume this week {_n(f.get('volume_surge'), 1)}x normal"
      )
    if ctx.store:
        sl = shorts_line(*ctx.store.short_trend(lst.ticker, as_of))
        if sl:
            a("")
            a(sl)
    a("")
    mem = memory_for(lst.ticker, ctx.history)
    if mem["path"]:
        a("## Score history (saved screens)")
        a("")
        a("| Screen | Rank | Score |")
        a("|---|---|---|")
        for d, rk, sc in mem["path"][-8:]:
            a(f"| {d} | {rk} | {_n(sc)} |")
        a(f"| **{as_of}** | **{r.rank}** | **{_n(r.composite)}** |")
        a("")
    for flag_kind, text in res.flags.get(lst.ticker, []):
        if flag_kind == "red":
            a(f"- 🔴 {text}")
    a("")
    a("## Primary sources (open these yourself)")
    a("")
    for label, url in source_links(lst, ctx.websites.get(lst.ticker, "")):
        a(f"- [{label}]({url})")
    a("")
    if for_claude:
        a(claude_pack(lst, r, rs, anns, card))
    a(DISCLAIMER)
    return "\n".join(L) + "\n"


def claude_pack(lst, r, rs, anns, card) -> str:
    """A prompt to paste into Claude with this brief and the latest result documents."""
    news = "\n".join(f"- {x.date}: {x.headline} ({x.label}"
                     + (f", reaction {x.reaction:+.1%})" if x.reaction is not None else ")")
                     for x in sorted(anns, key=lambda x: -x.relevance)[:8])
    thesis = rs["journal"].thesis if rs["journal"] else "(none written yet)"
    rules = ", ".join(x["rule"] for x in rs["rules"]) or "(none)"
    lenses = "\n".join(f"- {k}: {v}" for k, v in rs["lenses"].items())
    return f"""## Prompt to paste into Claude

Paste everything below, then attach the latest result announcement, investor presentation and
annual report from the primary-source links above.

```text
I'm a New Zealand-resident, long-term investor researching {lst.name} ({lst.ticker}, {lst.exchange}).
I am not asking whether to buy or sell. Help me test my thinking.

My thesis: {thesis}
It breaks if: {rules}

What my screen says (percentiles within NZX/ASX, 50 = middle): composite {r.composite:.0f},
{', '.join(f'{g} {v:.0f}' for g, v in r.group_scores.items() if v is not None)}.
Checklist:
{lenses}

Recent announcements:
{news or '- none in my data'}

Using ONLY the attached documents and the facts above (say "not in the documents" otherwise):
1. Summarise the latest result in five bullet points: revenue, margins, cash flow, debt, dividend.
2. What did management say about the outlook, and how did that compare with the prior result?
3. The strongest bull case and the strongest bear case, each in three points.
4. Which of my break-if rules is closest to triggering, and what in the documents could trigger it?
5. Three questions I should ask at the next AGM or results call.
6. Anything NZ-specific I should check (imputation or franking, FIF status, NZD/AUD exposure).
Quote the page or section for every number you use.
```
"""


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------

def render_digest(res: ScreenResult, ctx: Context, history_dir: str, watch: list[str] | None = None,
                  move: int = 3) -> str:
    as_of = res.as_of
    prev_all = load_history(history_dir, before=as_of)
    prev = prev_all[-1] if prev_all else None
    L: list[str] = [f"# What changed: {as_of:%d %B %Y}", "", DISCLAIMER, ""]
    a = L.append
    journal = set(ctx.store.journal) if ctx.store else set()
    focus = set(watch or []) | journal
    if prev is None:
        a("No earlier saved screen to compare with. Run `screen --save` (or `digest --save`) "
          "regularly to build the history.")
        a("")
    else:
        a(f"Compared with the screen saved for **{prev['as_of']}**.")
        a("")
        old = prev["shares"]
        movers = []
        for r in res.ranked:
            o = old.get(r.ticker)
            if o and abs(o["rank"] - r.rank) >= move:
                movers.append((o["rank"] - r.rank, r, o))
        new_in = [r for r in res.ranked if r.ticker not in old]
        gone = [t for t in old if t not in {r.ticker for r in res.ranked}]
        a("## Rank movers")
        a("")
        if movers:
            for d, r, o in sorted(movers, key=lambda x: -abs(x[0])):
                a(f"- {'▲' if d > 0 else '▼'} **{r.ticker}** {r.snap.listing.name}: {o['rank']} → {r.rank} "
                  f"(score {_n(o['composite'])} → {_n(r.composite)})")
        else:
            a(f"- No share moved {move} or more places.")
        for r in new_in:
            a(f"- New in the screen: **{r.ticker}** at rank {r.rank}")
        for t in gone:
            a(f"- Left the screen: {t} ({res.excluded.get(t, 'not in data')})")
        a("")
        a("## New red flags")
        a("")
        any_new = False
        for r in res.ranked:
            before = {_flag_key(t) for t in (old.get(r.ticker) or {}).get("red", [])}
            for k, t in res.flags.get(r.ticker, []):
                if k == "red" and _flag_key(t) not in before and (r.ticker in focus or r.rank <= 10):
                    a(f"- **{r.ticker}**: {t}")
                    any_new = True
        if not any_new:
            a("- None among your journal shares or the top 10.")
        a("")

    since = date.fromisoformat(prev["as_of"]) if prev else as_of - timedelta(days=30)
    a("## Results due in the next 21 days")
    a("")
    due = []
    for r in res.ranked:
        nr = next_results_for(r, res, ctx)
        if nr and as_of <= nr["date"] <= as_of + timedelta(days=21):
            due.append((nr["date"], r, nr))
    for d, r, nr in sorted(due, key=lambda x: x[0]):
        star = " ⭐" if r.ticker in focus else ""
        a(f"- {d:%a %d %b}: **{r.ticker}** {r.snap.listing.name} ({nr['basis']}){star}")
    if not due:
        a("- None found.")
    a("")
    a(f"## Important announcements since {since:%d %b %Y}")
    a("")
    found = False
    for r in res.ranked:
        lst = r.snap.listing
        for x in announcements_with_reactions(ctx, lst.ticker, lst.exchange, as_of, days=(as_of - since).days):
            if x.date > since and (x.relevance >= 0.8 or lst.ticker in focus) and x.category not in ("dividend", "agm"):
                rx = f", {x.reaction:+.1%} vs index" if x.reaction is not None else ""
                a(f"- {x.date:%d %b} **{lst.ticker}** {x.label}: {x.headline}{rx}")
                found = True
    if not found:
        a("- None.")
    a("")
    if ctx.store and ctx.store.journal:
        a("## Your theses")
        a("")
        by_t = {r.ticker: r for r in res.ranked}
        for t, e in ctx.store.journal.items():
            r = by_t.get(t)
            if r is None:
                a(f"- **{t}**: not scored ({res.excluded.get(t, 'not in data')})")
                continue
            lens = research_for(r, res, ctx)["lenses"]["thesis"]
            a(f"- **{t}**: {lens}")
        a("")
    a(DISCLAIMER)
    return "\n".join(L) + "\n"


def next_results_for(r, res, ctx: Context):
    from .research import next_results
    return next_results(r.snap.listing, ctx.source.fundamentals(r.snap.listing.company_id), ctx.store, res.as_of)
