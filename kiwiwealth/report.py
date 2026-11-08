"""Markdown report."""
from __future__ import annotations

from .advice import PRIORITY_LABELS, Analysis, m, pct
from .nz_rules import RULES

DISCLAIMER = (
    "> **General information, not financial advice.** This is a planning tool built for our own "
    "household. It uses simplified NZ tax rules and assumed returns, and investment returns are "
    "not guaranteed. Check big decisions with a licensed financial adviser (FAP), and check tax with IRD "
    "or an accountant."
)


def render(a: Analysis) -> str:
    p = a.profile
    out = [f"# Financial plan - {p.household}", f"_Prepared {a.on:%d %B %Y}_", "", DISCLAIMER, ""]

    out += ["## Where we are now", "", "| | |", "|---|---|",
            f"| Home | {m(p.home_value)} |",
            f"| KiwiSaver | {m(p.total_kiwisaver)} |",
            f"| Investments | {m(p.total_investments)} |",
            f"| Cash | {m(p.total_cash)} |"]
    if p.other_property or p.vehicles_and_other:
        out.append(f"| Other assets | {m(p.other_property + p.vehicles_and_other)} |")
    out += [f"| Mortgage & debts | -{m(p.total_debt)} |",
            f"| **Net worth** | **{m(p.net_worth)}** |",
            f"| Wealth outside the house & KiwiSaver | {m(p.liquid_wealth)} |", ""]

    if a.monthly_income is not None:
        src = (f"from {a.budget.transactions} transactions over {a.budget.months} months"
               if a.budget else p.budget.source)
        out += [f"**Cashflow** ({src}): income {m(a.monthly_income)}/month"]
        if a.monthly_spending is not None:
            rate = a.monthly_surplus / a.monthly_income if a.monthly_income else 0
            out[-1] += (f", spending {m(a.monthly_spending)}/month, surplus "
                        f"{m(a.monthly_surplus)}/month (savings rate {pct(rate, 0)}).")
        cats = a.budget.categories if a.budget else p.budget.categories
        if cats:
            out += ["", "| Top spending categories | per month |", "|---|---|"]
            out += [f"| {k} | {m(v)} |" for k, v in list(cats.items())[:8]]
        out.append("")

    out += ["**Tax settings:** " + "; ".join(
        f"{x.name} - marginal {pct(a.marginal_rates[x.name])}, PIR {pct(a.pirs[x.name])}"
        for x in p.people), ""]

    out += ["## Action plan", ""]
    current = None
    n = 0
    for r in a.recommendations:
        if r.priority != current:
            current = r.priority
            out += [f"### {PRIORITY_LABELS[r.priority]}", ""]
        n += 1
        out += [f"**{n}. {r.title}** _({r.category})_  ", f"{r.why}  ", f"→ {r.action}", ""]

    if a.goals:
        out += ["## Goals", "", "| Goal | Target | Year | Saved | Suggested home | Needed/month |",
                "|---|---|---|---|---|---|"]
        out += [f"| {g['name']} | {m(g['target'])} | {g['year']} | {m(g['saved'])} | "
                f"{g['suggested_fund']} | {m(g['monthly_needed'])} |" for g in a.goals]
        out.append("")

    if a.retirement:
        r = a.retirement
        out += ["## Retirement projection (today's dollars)", "",
                f"- Years to retirement: {r['years_to_retirement']}",
                f"- KiwiSaver at retirement: {m(r['kiwisaver_projected'])}",
                f"- Other investments (incl. redirected mortgage payment): {m(r['investments_projected'])}",
                f"- NZ Super: {m(r['nz_super_annual'])}/yr",
                f"- Sustainable income: about {m(r['sustainable_income'])}/yr vs target {m(r['income_target'])}/yr",
                f"- Assumes {pct(r['real_return_used'])} real return after fees and tax, "
                f"{pct(r['inflation'])} inflation, {pct(p.assumption('safe_withdrawal_rate'))} withdrawal rate", ""]

    out += ["## Sources for NZ rules", ""]
    out += [f"- {name.replace('_', ' ')}: {rule.source}" for name, rule in RULES.items()]
    out.append("")
    return "\n".join(out)
