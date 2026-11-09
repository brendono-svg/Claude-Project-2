"""Rule engine: turns a Profile (and optional budget summary) into ranked recommendations.

The rules follow a common order: clear expensive debt, hold an emergency fund, claim
free money (KiwiSaver), fix tax settings, then grow wealth. Each rule is a small
function so it's easy to read, test and change.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from . import nz_rules as nz
from .budget_import import BudgetSummary
from .profile import Person, Profile
from .retirement import SimPerson, SimScheme, bisect, simulate
from .projections import (after_tax_return, future_value, months_to_repay, real_rate,
                          required_monthly, total_interest)

PRIORITY_LABELS = {1: "Do now", 2: "Do soon", 3: "Worth doing", 4: "Keep in mind"}


@dataclass
class Recommendation:
    priority: int
    category: str
    title: str
    why: str
    action: str
    numbers: dict[str, Any] = field(default_factory=dict)


@dataclass
class Analysis:
    profile: Profile
    on: date
    budget: BudgetSummary | None
    monthly_income: float | None
    monthly_spending: float | None
    pirs: dict[str, float]
    marginal_rates: dict[str, float]
    recommendations: list[Recommendation] = field(default_factory=list)
    retirement: dict[str, Any] = field(default_factory=dict)
    goals: list[dict[str, Any]] = field(default_factory=list)

    @property
    def monthly_surplus(self) -> float | None:
        if self.monthly_income is None or self.monthly_spending is None:
            return None
        return self.monthly_income - self.monthly_spending

    def add(self, *args, **kwargs) -> None:
        self.recommendations.append(Recommendation(*args, **kwargs))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def person_pir(p: Person, on: date) -> float:
    prior_pie = p.pie_income if p.prior_year_pie_income is None else p.prior_year_pie_income
    return nz.prescribed_investor_rate(p.taxable_income, p.pie_income,
                                       p.prior_year_taxable_income, prior_pie, on=on)


def owner_pir(a: Analysis, owner: str) -> float:
    # Joint PIE holdings are usually taxed at the higher of the owners' PIRs.
    return a.pirs[owner] if owner in a.pirs else max(a.pirs.values())


def fund_after_tax(a: Analysis, fund_type: str, fee: float, pir: float, real: bool = False) -> float:
    gross = a.profile.assumption("returns")[fund_type]
    ty = a.profile.assumption("taxable_yield")[fund_type]
    r = after_tax_return(gross, fee, pir, ty)
    return real_rate(r, a.profile.assumption("inflation")) if real else r


def retire_age(profile: Profile, p: Person) -> int:
    return profile.preferences.retirement_age if p.retirement_age is None else p.retirement_age


def super_age(a: "Analysis") -> int:
    return a.profile.assumption("nz_super_age") or nz.rule("nz_super", a.on)["age"]


def first_retirement_years(profile: Profile) -> int:
    """Years until the first person stops work: when saving stops and drawdown starts."""
    return min(max(retire_age(profile, p) - p.age, 0) for p in profile.people)


def years_to(age: int, target_age: int) -> int:
    return max(target_age - age, 0)


def m(x: float) -> str:
    """Dollars, rounded half up (the phone app formats the same way)."""
    return ("-$" if x < 0 else "$") + f"{math.floor(abs(x) + 0.5):,}"


def pct(x: float, dp: int = 1) -> str:
    scale = 10 ** dp
    return f"{math.floor(x * 100 * scale + 0.5 + 1e-9) / scale:.{dp}f}%"


@dataclass
class SchemeFlow:
    """Yearly money into one scheme."""
    scheme: Any
    member: float
    employer_gross: float
    employer_net: float
    govt: float

    @property
    def total(self) -> float:
        return self.member + self.employer_net + self.govt


def scheme_flows(p: Person, on: date) -> list[SchemeFlow]:
    """Annualise every contribution. Employer $ given after tax are grossed up; the ESCT
    band is set by salary plus all of this employer's super contributions."""
    gross = [sc.employer.annual(p.salary) for sc in p.schemes]  # start from the stated figures
    for _ in range(5):  # settle the shared ESCT band across schemes
        total = sum(gross)
        for i, sc in enumerate(p.schemes):
            e = sc.employer
            if e.unit != "pct" and e.after_tax:
                gross[i] = nz.esct_gross_up(e.annual(p.salary), p.salary, total - gross[i], on)
    rate = nz.esct_rate(p.salary, sum(gross), on)
    flows = []
    for sc, g in zip(p.schemes, gross):
        member = sc.member.annual(p.salary) + sc.voluntary_annual
        govt = nz.kiwisaver_govt_contribution(member, p.taxable_income, p.age, on) if sc.kind == "kiwisaver" else 0.0
        flows.append(SchemeFlow(sc, member, g, g * (1 - rate), govt))
    # Only one KiwiSaver account can earn the government contribution.
    seen = False
    for f in flows:
        if f.govt and seen:
            f.govt = 0.0
        seen = seen or bool(f.govt)
    return flows


def scheme_tax(a: "Analysis", p: Person, sc) -> float:
    return a.pirs[p.name] if sc.pie else 0.28


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


HIGH_FEE, LOW_FEE = 0.006, 0.0035


def rule_expensive_debt(a: Analysis) -> None:
    for d in a.profile.debts:
        if d.rate >= 0.08 and d.balance > 0:
            months = months_to_repay(d.balance, d.rate, d.monthly_payment) if d.monthly_payment else None
            a.add(1, "Debt", f"Clear the {d.name} ({pct(d.rate)})",
                  f"Paying this off is a guaranteed {pct(d.rate)} return with no tax. "
                  "No managed fund reliably beats that after fees and tax.",
                  "Put spare cash here before investing beyond the KiwiSaver minimum.",
                  {"balance": d.balance, "months_left_at_current_payment": months})


def rule_emergency_fund(a: Analysis) -> None:
    if a.monthly_spending is None:
        return
    target_months = a.profile.assumption("emergency_fund_months")
    target = a.monthly_spending * target_months
    cash = sum(c.balance for c in a.profile.cash if c.is_emergency_fund)
    mort = a.profile.mortgage
    redraw = max(mort.revolving_limit - mort.balance, 0) if mort.kind == "revolving" else 0
    available = cash + redraw
    if available < target:
        a.add(1 if available < target / 2 else 2, "Safety net",
              "Build the emergency fund",
              f"You have about {available / a.monthly_spending:.1f} months of spending in "
              f"accessible cash; the aim is {target_months} months.",
              "Automate a weekly transfer to an on-call or PIE cash fund until it's full. "
              "An offset or revolving mortgage can hold this money and save interest at the same time.",
              {"target": round(target), "available": round(available),
               "shortfall": round(target - available)})
    elif available > target * 2.5 and a.profile.liquid_wealth > 0:
        a.add(3, "Safety net", "You may be holding too much cash",
              f"Accessible cash covers {available / a.monthly_spending:.0f} months. After tax "
              "and inflation, cash usually loses buying power.",
              "Keep the emergency fund, then put the extra on the mortgage or into your "
              "investment plan (cash needed within ~3 years can stay in term deposits).",
              {"target": round(target), "available": round(available),
               "excess": round(available - target)})


def rule_idle_cash(a: Analysis) -> None:
    low = [c for c in a.profile.cash if c.balance >= 5_000 and c.interest_rate < 0.02]
    if not low:
        return
    total = sum(c.balance for c in low)
    top = max(a.marginal_rates.values())
    extra = total * 0.03
    why = (f"{m(total)} is earning under 2%. An on-call savings account or cash fund paying around 3% "
           f"more would add about {m(extra)} a year before tax.")
    if top > 0.28:
        why += " A PIE cash fund is taxed at no more than 28%, below your income tax rate."
    mort = a.profile.mortgage
    action = "Move it to a higher-interest on-call account or a PIE cash fund."
    if mort.balance > 0:
        action += (f" While the mortgage lasts, an offset account does better still: it saves {pct(mort.rate, 2)} "
                   "interest, tax-free, and the money stays available.")
    a.add(3, "Safety net", "Your cash is earning very little", why, action,
          {"low_interest_cash": round(total), "extra_interest": round(extra)})


def rule_kiwisaver(a: Analysis) -> None:
    ks_rule = nz.rule("kiwisaver", a.on)
    full = nz.kiwisaver_contribution_for_max_govt(a.on)
    risk = a.profile.preferences.risk_tolerance
    sup_age = super_age(a)
    min_rate = ks_rule["default_employee_rate"]
    for p in a.profile.people:
        retire = retire_age(a.profile, p)
        flows = scheme_flows(p, a.on)
        ks_flows = [f for f in flows if f.scheme.kind == "kiwisaver"]
        if not ks_flows:
            if 16 <= p.age < 65:
                a.add(1, "KiwiSaver", f"{p.name}: join KiwiSaver",
                      "Employer contributions and the government contribution are free money.",
                      "Join through your employer or a provider and pick a fund that suits your timeframe.")
        for f in ks_flows:
            ks, contrib = f.scheme, f.member
            eligible = p.taxable_income <= ks_rule["govt_income_cap"] and 16 <= p.age < 65
            if eligible and contrib < full:
                a.add(1, "KiwiSaver", f"{p.name}: top up KiwiSaver to {m(full)} by 30 June",
                      f"The government adds 25c per $1 you contribute, up to "
                      f"{m(ks_rule['govt_max'])} a year. You're on track for about {m(f.govt)}.",
                      f"Make a voluntary payment of about {m(full - contrib)} before 30 June each year.",
                      {"contributing": round(contrib), "govt_contribution": f.govt,
                       "top_up": round(full - contrib, 2)})
            if p.taxable_income > ks_rule["govt_income_cap"]:
                a.add(4, "KiwiSaver", f"{p.name}: no government contribution above {m(ks_rule['govt_income_cap'])}",
                      "Since 1 July 2025 people earning over $180,000 don't get the government contribution.",
                      "Contributions above your employer match lock money up until 65; for "
                      "flexible goals, invest outside KiwiSaver instead.")
            if p.salary > 0:
                member_rate = ks.member.annual(p.salary) / p.salary
                employer_rate = f.employer_gross / p.salary
                if member_rate < min_rate - 0.002:
                    a.add(2, "KiwiSaver", f"{p.name}: check your KiwiSaver contribution rate",
                          f"You're contributing about {pct(member_rate)} of salary; the minimum is now "
                          f"{pct(min_rate)} unless you applied for a temporary rate reduction.",
                          "Check your payslip and talk to payroll.")
                if 0 < employer_rate < ks_rule["employer_min_rate"] - 0.002:
                    a.add(3, "KiwiSaver", f"{p.name}: employer KiwiSaver is about {pct(employer_rate)}, below the {pct(min_rate)} minimum",
                          f"Employers must pay at least {pct(min_rate)} of gross pay from 1 April 2026. "
                          "Some employers count contributions to a workplace scheme toward this, or your "
                          "figures may be from before the change or based on base pay only.",
                          "Check your payslip or employment agreement with payroll.",
                          {"employer_rate": round(employer_rate, 4)})
                if retire < sup_age and member_rate > min_rate + 0.005:
                    excess = (member_rate - min_rate) * p.salary
                    a.add(2, "KiwiSaver", f"{p.name}: KiwiSaver contributions above {pct(min_rate)} are locked until {sup_age}",
                          f"You plan to stop work at {retire}, but you're putting about {pct(member_rate)} "
                          f"into KiwiSaver, which you can't touch until {sup_age}. About {m(excess)}/yr "
                          "could go somewhere you can reach earlier.",
                          f"Consider dropping to {pct(min_rate)} and sending the difference to your workplace "
                          "scheme or your own investments. First check that your employer's contribution doesn't "
                          "depend on your rate.", {"excess_annual": round(excess)})
        for f in flows:
            sc = f.scheme
            access_age = sup_age if sc.kind == "kiwisaver" else retire
            horizon = years_to(p.age, access_age)
            label = "KiwiSaver" if sc.kind == "kiwisaver" else sc.name
            t = scheme_tax(a, p, sc)
            if horizon > 10 and sc.fund_type in ("cash", "conservative") and risk != "conservative":
                gap = future_value(sc.balance, f.total / 12, fund_after_tax(a, "growth", sc.fee_pct, t, True), horizon) - \
                      future_value(sc.balance, f.total / 12, fund_after_tax(a, sc.fund_type, sc.fee_pct, t, True), horizon)
                a.add(2, "KiwiSaver" if sc.kind == "kiwisaver" else "Super", f"{p.name}: {label} fund may be too cautious",
                      f"{horizon} years until you can use it, in a {sc.fund_type} fund. Over long periods, growth "
                      "funds have tended to end well ahead, despite bigger ups and downs along the way.",
                      "Review the fund type with your provider (use the Sorted Fund Finder to compare).",
                      {"extra_todays_dollars": round(gap)})
            if sc.fee_pct > HIGH_FEE and horizon > 0:
                cost = future_value(sc.balance, f.total / 12, fund_after_tax(a, sc.fund_type, LOW_FEE, t, True), horizon) - \
                       future_value(sc.balance, f.total / 12, fund_after_tax(a, sc.fund_type, sc.fee_pct, t, True), horizon)
                a.add(3, "Fees", f"{p.name}: {label} fee of {pct(sc.fee_pct, 2)} could cost about {m(cost)}",
                      f"Low-cost funds of the same type charge around {pct(LOW_FEE, 2)}. Over the {horizon} years "
                      f"until you can use this money, the difference compounds to about {m(cost)} in today's dollars.",
                      "Compare fees and returns for the same fund type on Sorted's Smart Investor, "
                      "then switch if a cheaper fund gives the same exposure.", {"fee_cost": round(cost)})


def rule_workplace(a: Analysis) -> None:
    for p in a.profile.people:
        for f in scheme_flows(p, a.on):
            sc = f.scheme
            if sc.kind != "workplace":
                continue
            rate = f.employer_gross / p.salary if p.salary else 0
            why = (f"{m(sc.balance)} that you can take when you leave your job. Your employer adds about "
                   f"{m(f.employer_net)}/yr after tax ({pct(rate)} of salary before tax) and you add {m(f.member)}/yr. "
                   "Unlike KiwiSaver it isn't locked to 65, so it can fund the years before NZ Super.")
            if sc.vested_pct < 1:
                why += (f" Only {pct(sc.vested_pct, 0)} is vested now, so leaving early could forfeit about "
                        f"{m(sc.balance * (1 - sc.vested_pct))} of employer money.")
            if not sc.pie:
                why += " As a non-PIE scheme it's taxed at 28% regardless of your PIR."
            a.add(3, "Super", f"{p.name}: plan for the {sc.name} payout", why,
                  "When you leave, move it into a PIE fund of the same risk level instead of taking "
                  "cash, unless it's earmarked for a goal. Check whether you can stay in the scheme "
                  "after leaving, or transfer to its personal section.",
                  {"balance": sc.balance, "employer_net_annual": round(f.employer_net),
                   "member_annual": round(f.member)})


def rule_pir(a: Analysis) -> None:
    for p in a.profile.people:
        correct = a.pirs[p.name]
        if p.current_pir is None:
            a.add(3, "Tax", f"{p.name}: confirm your PIR is {pct(correct)}",
                  "PIE funds (including KiwiSaver) tax you at your prescribed investor rate.",
                  f"Check that every KiwiSaver/managed fund provider has {pct(correct)}.")
        elif abs(p.current_pir - correct) > 1e-9:
            too_low = p.current_pir < correct
            a.add(1, "Tax", f"{p.name}: PIR is set to {pct(p.current_pir)} but should be {pct(correct)}",
                  "Too low means a tax bill at year end. Too high means you overpay, and "
                  "you don't get that back." if too_low else
                  "You're overpaying tax on your funds, and IRD won't refund it.",
                  f"Update your PIR with each provider to {pct(correct)}.")


def rule_mortgage(a: Analysis) -> None:
    mort = a.profile.mortgage
    if mort.balance <= 0:
        a.add(3, "Mortgage", "Keep paying yourself the old mortgage payment",
              "With no mortgage, the money that used to go to the bank is your biggest "
              "wealth-building lever.",
              "Set an automatic monthly investment equal to your old repayment.")
        return
    pir = max(a.pirs.values())
    invest_r = fund_after_tax(a, a.profile.preferences.risk_tolerance, 0.004, pir)
    months = months_to_repay(mort.balance, mort.rate, mort.monthly_repayment) if mort.monthly_repayment else None
    interest = total_interest(mort.balance, mort.rate, mort.monthly_repayment) if mort.monthly_repayment else None
    nums = {"balance": mort.balance, "rate": mort.rate, "months_left": months,
            "interest_left": interest, "invest_after_tax_return": round(invest_r, 4)}

    if mort.rate >= invest_r:
        verdict = (f"Your mortgage costs {pct(mort.rate, 2)}. Paying it down is a guaranteed, tax-free return at that rate. "
                   f"A {a.profile.preferences.risk_tolerance} fund might return about {pct(invest_r)} after fees and "
                   "tax, and that isn't guaranteed. Paying off the mortgage wins.")
        action = "Put spare cash on the mortgage until it's gone (watch fixed-rate break fees and lump-sum limits)."
    elif mort.rate >= invest_r - 0.015:
        verdict = (f"It's close: the mortgage costs {pct(mort.rate, 2)} (guaranteed), and a "
                   f"{a.profile.preferences.risk_tolerance} fund might return about {pct(invest_r)} after fees and "
                   "tax (not guaranteed). Adjusted for risk, paying it off is at least as good, and it's nearly done.")
        action = ("Put lump sums on the mortgage at refix time (or into an offset account), while keeping "
                  "your regular fund contributions going so the habit is in place.")
    else:
        verdict = (f"Investing might return about {pct(invest_r)} after tax vs a {pct(mort.rate, 2)} "
                   "mortgage, but that edge isn't guaranteed. With a small balance left, "
                   "paying it off still makes sense for many families.")
        action = ("Consider splitting the spare cash: some on the mortgage, and some starting your "
                  "investment habit now.")
    a.add(2, "Mortgage", "Finish the mortgage, then redirect the repayment", verdict, action, nums)

    if mort.kind == "table" and mort.balance < 150_000:
        a.add(3, "Mortgage", "Consider an offset or revolving portion",
              "Spare cash and your emergency fund sitting against the loan cut interest "
              "and can still be accessed.",
              "Ask your bank about moving part of the loan to offset/revolving when the fixed term ends.",
              {"fixed_until": mort.fixed_until})

    if months is not None and mort.monthly_repayment:
        horizon = first_retirement_years(a.profile)
        invest_years = horizon - months / 12
        if invest_years > 0:
            real = fund_after_tax(a, a.profile.preferences.risk_tolerance, 0.004, pir, True)
            fv = future_value(0, mort.monthly_repayment, real, invest_years)
            a.add(1, "Investing", "Plan now where the mortgage payment goes once it's paid off",
                  f"The mortgage should be gone in about {months} months. Investing "
                  f"{m(mort.monthly_repayment)}/month after that could grow to about {m(fv)} "
                  f"in today's dollars by retirement.",
                  "Set up the automatic investment for the day after the last repayment, so the "
                  "money isn't absorbed into everyday spending.",
                  {"monthly": mort.monthly_repayment, "years_investing": round(invest_years, 1),
                   "value_at_retirement_todays_dollars": round(fv)})


def rule_concentration(a: Analysis) -> None:
    p = a.profile
    if p.total_assets <= 0 or p.home_value <= 0:
        return
    share = p.home_value / p.total_assets
    if share < 0.6:
        return
    a.add(2, "Diversification", f"The house is {pct(share, 0)} of your wealth",
          "A home is somewhere to live, but it doesn't pay you an income and you can't sell part of it. "
          "Most of your wealth depends on one property in one local market.",
          "Make building investments outside the house the main goal for the next 5-10 years.",
          {"home_share": round(share, 3), "liquid_wealth": round(p.liquid_wealth)})
    # Downsizing scenario for a large house.
    release = p.home_value * 0.25
    costs = p.home_value * 0.04
    net = release - costs
    horizon = first_retirement_years(p)
    real = fund_after_tax(a, p.preferences.risk_tolerance, 0.004, max(a.pirs.values()), True)
    fv = future_value(net, 0, real, horizon)
    swr = p.assumption("safe_withdrawal_rate")
    a.add(4, "Diversification", "Scenario: downsizing the large house one day",
          f"Moving to a home worth 25% less might free about {m(net)} after roughly 4% "
          "costs (agent, legal, moving). No bright-line tax applies to your main home.",
          f"Invested, that could be about {m(fv)} in today's dollars by retirement, which is "
          f"about {m(fv * swr)}/year of income. It's a lifestyle decision; this is only the money side.",
          {"equity_released": round(net), "value_at_retirement": round(fv),
           "annual_income": round(fv * swr)})


def rule_savings_rate(a: Analysis) -> None:
    if a.monthly_income is None or a.monthly_spending is None or a.monthly_income <= 0:
        return
    rate = a.monthly_surplus / a.monthly_income
    cats = a.budget.categories if a.budget else a.profile.budget.categories
    top = ", ".join(f"{k} {m(v)}" for k, v in list(cats.items())[:3]) if cats else ""
    nums = {"savings_rate": round(rate, 3), "monthly_surplus": round(a.monthly_surplus)}
    if rate < 0:
        a.add(1, "Cashflow", "You're spending more than you earn",
              f"On average you're {m(-a.monthly_surplus)}/month behind." +
              (f" Biggest categories: {top}." if top else ""),
              "Pick two categories to cut and set spending limits in your budgeting app.", nums)
    elif rate < 0.10:
        a.add(2, "Cashflow", f"Savings rate is {pct(rate, 0)}; aim for 15-20%+",
              "Your surplus is what pays for every goal and investment." +
              (f" Biggest categories: {top}." if top else ""),
              "Automate savings on payday so you save first and spend what's left.", nums)
    else:
        a.add(4, "Cashflow", f"Savings rate is {pct(rate, 0)}: good habit",
              "A savings rate this high puts long-term goals within reach." +
              (f" Biggest categories: {top}." if top else ""),
              "Make sure the surplus is actually invested on autopay and not left building up in the everyday account.", nums)


def rule_investment_plan(a: Analysis) -> None:
    p = a.profile
    prefs = p.preferences
    holdings = p.holdings
    total = p.total_investments
    high_rate = [x.name for x in p.people if a.marginal_rates[x.name] > 0.28]

    core = 0.85 if "shares" in prefs.preferred_vehicles else 1.0
    lines = [f"Core ({pct(core, 0)}): low-fee diversified PIE funds, either global and NZ/Australian share index "
             f"funds or a single {prefs.risk_tolerance} multi-asset fund."]
    if core < 1:
        lines.append(f"Satellite ({pct(1 - core, 0)}): individual shares you pick, with no single "
                     f"company over {pct(prefs.max_single_share_pct, 0)} of the portfolio.")
    lines.append("Invest a fixed amount every week or month. Regular investing beats trying to time the market.")
    why = ("This plan matches your preference for managed funds and shares and keeps fees and "
           "tax low.")
    if high_rate:
        why += (f" {', '.join(high_rate)} pay tax above 28%. PIE funds cap tax at 28%, which beats "
                "holding term deposits or bonds in your own name. Direct NZ shares (imputation credits, "
                "no tax on gains) and direct overseas shares under the $50k FIF threshold (only dividends "
                "taxed) can still be cheaper on tax than a PIE fund, which pays tax on 5% FDR. The "
                "reason to use PIE funds for the core is diversification and low fees.")
    a.add(2, "Investing", "Your investment plan: core funds + a small share satellite", why,
          " ".join(lines), {"core_pct": core})

    if total > 0:
        direct = sum(h.value for h in holdings if h.is_direct_shares)
        if core < 1 and direct / total > (1 - core) + 0.10:
            a.add(2, "Diversification", f"Direct shares are {pct(direct / total, 0)} of your investments vs a {pct(1 - core, 0)} plan",
                  "Picking shares is fine as a satellite. As the main part of the portfolio, your "
                  "results rest on a small number of companies and on the timing of your trades.",
                  "Point new contributions at the core funds until the mix is back on target, "
                  "instead of selling (which can trigger costs and tax).",
                  {"direct_shares": round(direct), "target_pct": round(1 - core, 2)})
        for h in holdings:
            if h.is_direct_shares and h.single_company and h.value / total > prefs.max_single_share_pct:
                a.add(2, "Diversification", f"{h.name} is {pct(h.value / total, 0)} of your investments",
                      "One company doing badly could knock a big hole in your plan.",
                      "Direct new money elsewhere until it's back under your limit.",
                      {"value": h.value})
            if h.fee_pct > 0.01:
                a.add(3, "Fees", f"{h.name}: fee of {pct(h.fee_pct, 2)} is high",
                      "Index funds in NZ commonly charge 0.2-0.5%.",
                      "Check whether a cheaper fund gives you the same exposure.")


def rule_fif(a: Analysis) -> None:
    people = a.profile.people
    for p in people:
        cost = value = 0.0
        for h in a.profile.holdings:
            if h.type != "direct_offshore":
                continue
            share = 1.0 if h.owner == p.name else (1 / len(people) if h.owner == "joint" else 0.0)
            cost += h.cost * share
            value += h.value * share
        if cost == 0:
            continue
        status = nz.fif_status(cost, value, a.marginal_rates[p.name], a.on)
        if status["over_threshold"]:
            a.add(2, "Tax", f"{p.name}: FIF rules apply to your overseas shares",
                  f"Your direct overseas shares cost more than $50,000, so you're taxed on 5% of their "
                  f"opening value each year (about {m(status['estimated_tax'])} tax) whether or not you sell.",
                  "Include FIF income in your tax return. Each year, compare the FDR and "
                  "comparative value methods. Consider putting new offshore money in PIE funds.",
                  status)
        elif status["headroom"] < 10_000:
            a.add(3, "Tax", f"{p.name}: close to the $50,000 FIF threshold",
                  f"Only {m(status['headroom'])} of headroom (measured on purchase cost, per person).",
                  "Buy new overseas shares in your partner's name, or get global exposure via NZ PIE funds.",
                  status)


def rule_ownership(a: Analysis) -> None:
    if len(a.profile.people) < 2:
        return
    rates = a.marginal_rates
    low = min(rates, key=rates.get)
    high = max(rates, key=rates.get)
    if rates[high] - rates[low] < 0.05:
        return
    taxable = [h for h in a.profile.holdings
               if h.owner in (high, "joint") and h.type in ("nz_listed", "direct_offshore", "term_deposit")]
    cash_interest = sum(c.balance * c.interest_rate for c in a.profile.cash)
    if taxable or cash_interest > 500:
        a.add(3, "Tax", f"Hold taxable investments in {low}'s name",
              f"Interest and dividends are taxed at the owner's rate: {pct(rates[low])} for {low} vs "
              f"{pct(rates[high])} for {high}.",
              f"Put new term deposits, savings and direct shares in {low}'s name, or use PIE "
              "funds, which are capped at 28%. Consider relationship property implications.",
              {"low_rate_person": low, "high_rate_person": high})


def rule_goals(a: Analysis) -> None:
    year = a.on.year
    surplus = a.monthly_surplus
    risk_order = ["cash", "conservative", "balanced", "growth", "aggressive"]
    cap = risk_order.index(a.profile.preferences.risk_tolerance)
    total_needed = 0.0
    pir = max(a.pirs.values())
    for g in sorted(a.profile.goals, key=lambda g: (g.priority, g.target_year)):
        years = max(g.target_year - year, 0)
        fund = "cash" if years < 3 else ("balanced" if years < 7 else "growth")
        fund = risk_order[min(risk_order.index(fund), cap)]
        r = fund_after_tax(a, fund, 0.003 if fund == "cash" else 0.004, pir)
        need = required_monthly(g.target_amount, g.saved_so_far, r, years)
        total_needed += need
        a.goals.append({"name": g.name, "target": g.target_amount, "year": g.target_year,
                        "saved": g.saved_so_far, "years": years, "suggested_fund": fund,
                        "monthly_needed": round(need), "monthly_exact": need, "priority": g.priority})
    if not a.goals:
        return
    if surplus is not None and total_needed > surplus:
        a.add(1, "Goals", "Your goals need more than your current surplus",
              f"The goals need about {m(total_needed)}/month; your surplus is about {m(surplus)}.",
              "Push back lower-priority goals, trim spending, or count on the mortgage repayment freeing up.",
              {"monthly_needed": round(total_needed), "monthly_surplus": round(surplus)})
    else:
        a.add(3, "Goals", "Fund each goal from its own account or fund",
              f"Your goals need about {m(total_needed)}/month in total. Match the investment to the "
              "timeframe: cash under 3 years, balanced for 3-7, growth for longer.",
              "Name a separate account or fund for each goal so progress is easy to see.",
              {"monthly_needed": round(total_needed)})


def rule_idle_surplus(a: Analysis) -> None:
    """Surplus that isn't already assigned to goals or regular investing."""
    surplus = a.monthly_surplus
    if surplus is None or surplus <= 0:
        return
    committed = sum(g["monthly_exact"] for g in a.goals)
    committed += sum(h.monthly_contribution for h in a.profile.holdings)
    idle = surplus - committed
    if idle < 200:
        return
    p = a.profile
    horizon = first_retirement_years(p)
    real = fund_after_tax(a, p.preferences.risk_tolerance, 0.004, max(a.pirs.values()), True)
    fv = future_value(0, idle, real, horizon)
    a.add(1, "Investing", f"About {m(idle)}/month of surplus has no job",
          f"After goal savings and current regular investments, about {m(idle)}/month is unallocated. "
          f"Invested, that's about {m(fv)} in today's dollars by retirement. Otherwise it tends to get spent.",
          "Split it on payday by automatic transfer: top up the emergency fund first, then the mortgage "
          "(while it lasts) or the core funds.",
          {"idle_monthly": round(idle), "value_at_retirement": round(fv)})


def sim_inputs(a: Analysis) -> dict[str, Any]:
    """Everything the retirement simulation needs, in today's dollars."""
    p = a.profile
    prefs = p.preferences
    pir = max(a.pirs.values())
    people = []
    for person in p.people:
        flows = scheme_flows(person, a.on)
        net = nz.take_home_pay(person.salary, 0, person.student_loan, a.on)["net"] - sum(f.member for f in flows)
        people.append(SimPerson(
            age=person.age, retire_age=retire_age(p, person), net_pay=net,
            schemes=[SimScheme(f.scheme.kind, f.scheme.balance,
                               fund_after_tax(a, f.scheme.fund_type, f.scheme.fee_pct,
                                              scheme_tax(a, person, f.scheme), True), f.total)
                     for f in flows]))
    r_grow = fund_after_tax(a, prefs.risk_tolerance, 0.004, pir, True)
    r_draw = min(r_grow, fund_after_tax(a, "balanced", 0.004, pir, True))
    mort = p.mortgage
    if mort.balance <= 0:
        months, repayment = 0, 0.0   # nothing to redirect: the mortgage rule covers the old payment
    else:
        months = months_to_repay(mort.balance, mort.rate, mort.monthly_repayment) if mort.monthly_repayment else None
        repayment = mort.monthly_repayment
    sup = nz.rule("nz_super", a.on)
    per_person = sup["couple_annual_net"] / 2 if len(p.people) >= 2 else sup["single_living_alone_annual_net"]
    return dict(people=people, invested=p.total_investments,
                monthly_invest=sum(h.monthly_contribution for h in p.holdings),
                mortgage_months=months, mortgage_repayment=repayment,
                target=prefs.retirement_income_target, r_grow=r_grow, r_draw=r_draw,
                super_age=super_age(a), super_per_person=per_person,
                plan_age=p.assumption("plan_to_age"))


def rule_retirement(a: Analysis) -> None:
    p = a.profile
    target = p.preferences.retirement_income_target
    inputs = sim_inputs(a)
    plan_age = inputs["plan_age"]
    run = lambda **kw: simulate(**{**inputs, **kw})  # noqa: E731
    base = run()
    ok = lambda r: r.short_total < 1  # noqa: E731
    sustainable = bisect(lambda x: ok(run(target=x)), 0.0, 1_000_000.0)
    first = min(p.people, key=lambda x: max(retire_age(p, x) - x.age, 0))
    years = base.draw_start // 12
    at = base.at_retirement
    total = at["ks"] + at["wp"] + at["acc"]

    def when(month: int) -> str:
        return f"{a.on.year + month // 12} (when {first.name} is {first.age + month // 12})"

    extra = None
    if not ok(base) and base.draw_start > 0:
        if ok(run(extra_monthly=50_000.0)):
            extra = 50_000.0 - bisect(lambda x: ok(run(extra_monthly=50_000.0 - x)), 0.0, 50_000.0)
    locked_problem = not ok(base) and ok(run(lock=False))

    a.retirement = {
        "years_to_retirement": years, "first_to_retire": first.name,
        "kiwisaver_projected": round(at["ks"]), "workplace_projected": round(at["wp"]),
        "investments_projected": round(at["acc"]), "total_projected": round(total),
        "nz_super_annual": round(inputs["super_per_person"] * len(p.people)),
        "income_target": target, "sustainable_income": round(sustainable),
        "shortfall_year": None if base.first_short is None else a.on.year + base.first_short // 12,
        "locked_problem": locked_problem,
        "extra_monthly_needed": None if extra is None else round(extra),
        "plan_to_age": plan_age, "real_return_used": round(inputs["r_grow"], 4),
        "drawdown_return_used": round(inputs["r_draw"], 4), "inflation": p.assumption("inflation"),
        "series": [{"year": a.on.year + x["year_index"], "ks": round(x["ks"]), "wp": round(x["wp"]),
                    "acc": round(x["acc"])} for x in base.series],
    }
    if ok(base):
        a.add(3, "Retirement", "Retirement target looks achievable on current settings",
              f"Projected savings of {m(total)} (today's dollars) when {first.name} stops work in {years} years, "
              f"plus NZ Super from 65, cover {m(target)}/yr until age {plan_age}. They could support "
              f"about {m(sustainable)}/yr.",
              "Stick with the plan and check every year. Extra savings give you the option "
              "of retiring earlier or spending more.", a.retirement)
    elif locked_problem:
        a.add(2, "Retirement", "The money you can reach runs out before KiwiSaver opens",
              f"Your total savings are enough for {m(target)}/yr, but too much is locked in KiwiSaver. "
              f"On current settings the money outside KiwiSaver runs out around {when(base.first_short)}.",
              (f"Put about {m(extra)}/month more into investments outside KiwiSaver, " if extra else "Build more savings outside KiwiSaver, ")
              + "keep KiwiSaver at the minimum rate, or retire later.", a.retirement)
    else:
        a.add(2, "Retirement", f"On track for about {m(sustainable)}/yr vs a {m(target)} target",
              f"Projected savings of {m(total)} (today's dollars) when {first.name} stops work, plus NZ Super, "
              f"would run out around {when(base.first_short)} if you spend {m(target)}/yr.",
              (f"Invest about {m(extra)}/month more until then, " if extra else "Save more, ")
              + "or adjust the target or retirement age.", a.retirement)


def rule_protection(a: Analysis) -> None:
    p = a.profile
    if p.has_wills is not True:
        a.add(2, "Protection", "Make or update your wills and enduring powers of attorney",
              "Without these, your family and assets may not be looked after the way you want.",
              "See a lawyer or Public Trust. Review after big life changes.")
    if p.dependants and p.has_income_protection is not True:
        a.add(2, "Protection", "Check life and income-protection cover",
              "Your plan relies on your incomes. ACC only covers accidents, not illness.",
              "Get quotes or speak to an insurance adviser. Cover should fall as debt falls and wealth grows.")


RULES: list[Callable[[Analysis], None]] = [
    rule_expensive_debt, rule_emergency_fund, rule_idle_cash, rule_kiwisaver, rule_workplace, rule_pir, rule_mortgage,
    rule_savings_rate, rule_concentration, rule_investment_plan, rule_fif, rule_ownership,
    rule_goals, rule_idle_surplus, rule_retirement, rule_protection,
]


def analyse(profile: Profile, budget: BudgetSummary | None = None, on: date | None = None) -> Analysis:
    on = on or date.today()
    pirs = {p.name: person_pir(p, on) for p in profile.people}
    marg = {p.name: nz.marginal_tax_rate(p.taxable_income, on) for p in profile.people}

    if budget is not None:
        income, spending = budget.monthly_income, budget.monthly_spending
    else:
        income = profile.budget.monthly_net_income
        spending = profile.budget.monthly_spending
        if income is None and any(p.salary for p in profile.people):
            income = sum(nz.take_home_pay(p.salary, 0, p.student_loan, on)["net"]
                         - sum(f.member for f in scheme_flows(p, on)) for p in profile.people) / 12

    a = Analysis(profile, on, budget, income, spending, pirs, marg)
    for r in RULES:
        r(a)
    a.recommendations.sort(key=lambda r: r.priority)
    return a
