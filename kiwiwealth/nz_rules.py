"""New Zealand tax, KiwiSaver and investment rules.

Every rule is a list of (effective_from, value) entries plus a source URL, so a
rule change means adding a new dated entry rather than editing an old one.
`rule(name, on)` returns the value in force on a given date.

Figures are the latest confirmed values known when written (late 2026). NZ Super,
ACC and student-loan figures change every 1 April. Check the source links before
making large decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    history: tuple[tuple[date, Any], ...]  # sorted oldest -> newest
    source: str
    note: str = ""

    def at(self, on: date) -> Any:
        value = None
        for start, v in self.history:
            if start <= on:
                value = v
        if value is None:
            raise LookupError(f"No rule in force on {on}")
        return value


RULES: dict[str, Rule] = {
    # Personal income tax brackets: (upper limit of band, rate). None = no upper limit.
    "income_tax_brackets": Rule(
        history=(
            (date(2024, 7, 31), ((15_600, 0.105), (53_500, 0.175), (78_100, 0.30),
                                 (180_000, 0.33), (None, 0.39))),
        ),
        source="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/tax-codes-and-tax-rates-for-individuals/tax-rates-for-individuals",
    ),
    # ACC earners' levy per $1 of liable earnings, and the cap on liable earnings.
    "acc_earners_levy": Rule(
        history=(
            (date(2025, 4, 1), {"rate": 0.0167, "max_earnings": 152_790}),
            (date(2026, 4, 1), {"rate": 0.0175, "max_earnings": 156_641}),
        ),
        source="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/acc-clients-and-carers/acc-earners-levy",
        note="Set by ACC levy consultation; check the current year's figures.",
    ),
    "student_loan": Rule(
        history=((date(2025, 4, 1), {"rate": 0.12, "threshold": 24_128}),),
        source="https://www.ird.govt.nz/student-loans/living-in-nz/repayments",
    ),
    # Employer superannuation contribution tax (ESCT). Band = salary + employer contributions.
    "esct_brackets": Rule(
        history=(
            (date(2025, 4, 1), ((18_720, 0.105), (64_200, 0.175), (93_720, 0.30),
                                (216_000, 0.33), (None, 0.39))),
        ),
        source="https://www.ird.govt.nz/kiwisaver/kiwisaver-employers/making-kiwisaver-deductions/employer-superannuation-contribution-tax-esct",
    ),
    "kiwisaver": Rule(
        history=(
            (date(2025, 7, 1), {
                "default_employee_rate": 0.03, "employer_min_rate": 0.03,
                "employee_rates": (0.03, 0.04, 0.06, 0.08, 0.10),
                "govt_rate": 0.25, "govt_max": 260.72, "govt_full_at": 1_042.86, "govt_income_cap": 180_000,
                "govt_min_age": 16,
            }),
            (date(2026, 4, 1), {
                "default_employee_rate": 0.035, "employer_min_rate": 0.035,
                "employee_rates": (0.035, 0.04, 0.06, 0.08, 0.10),
                "govt_rate": 0.25, "govt_max": 260.72, "govt_full_at": 1_042.86, "govt_income_cap": 180_000,
                "govt_min_age": 16,
            }),
            (date(2028, 4, 1), {
                "default_employee_rate": 0.04, "employer_min_rate": 0.04,
                "employee_rates": (0.04, 0.06, 0.08, 0.10),
                "govt_rate": 0.25, "govt_max": 260.72, "govt_full_at": 1_042.86, "govt_income_cap": 180_000,
                "govt_min_age": 16,
            }),
        ),
        source="https://budget.govt.nz/budget/pdfs/releases/l28a-factsheet-kiwisaver-changes.pdf",
        note="Govt contribution year runs 1 July - 30 June. Members can apply for a "
             "temporary rate reduction back to 3%.",
    ),
    # Prescribed investor rate thresholds: (max taxable income, max taxable+PIE income, PIR).
    "pir_thresholds": Rule(
        history=((date(2025, 4, 1), ((15_600, 53_500, 0.105), (53_500, 78_100, 0.175))),),
        source="https://www.ird.govt.nz/roles/portfolio-investment-entities/using-prescribed-investor-rates",
        note="Use the lower PIR from either of the last two income years. Otherwise 28%.",
    ),
    "fif": Rule(
        history=((date(2007, 4, 1), {"de_minimis_cost": 50_000, "fdr_rate": 0.05}),),
        source="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/types-of-individual-income/overseas-income/foreign-investment-funds-fifs",
        note="Threshold is per person, on cost. Does not apply to trusts. PIE funds "
             "handle FIF internally. Most ASX-listed Australian-resident shares are exempt.",
    ),
    "bright_line_years": Rule(
        history=((date(2024, 7, 1), 2),),
        source="https://www.ird.govt.nz/property/buying-and-selling-residential-property/the-bright-line-property-rule",
        note="The main home is usually excluded.",
    ),
    "nz_super": Rule(
        history=((date(2025, 4, 1), {"age": 65, "couple_annual_net": 41_409,
                                      "single_living_alone_annual_net": 26_916}),),
        source="https://www.workandincome.govt.nz/eligibility/seniors/superannuation/payment-rates.html",
        note="Net rates at M tax code; adjusted every 1 April.",
    ),
}


def rule(name: str, on: date | None = None) -> Any:
    return RULES[name].at(on or date.today())


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------


def _round_cents(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _progressive(amount: float, brackets) -> float:
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        top = amount if upper is None else min(amount, upper)
        if top > lower:
            tax += (top - lower) * rate
        if upper is None or amount <= upper:
            break
        lower = upper
    return round(tax, 2)


def _band_rate(amount: float, brackets) -> float:
    for upper, rate in brackets:
        if upper is None or amount <= upper:
            return rate
    raise AssertionError("unreachable")


def income_tax(income: float, on: date | None = None) -> float:
    return _progressive(max(income, 0.0), rule("income_tax_brackets", on))


def marginal_tax_rate(income: float, on: date | None = None) -> float:
    return _band_rate(max(income, 0.0), rule("income_tax_brackets", on))


def acc_levy(earnings: float, on: date | None = None) -> float:
    r = rule("acc_earners_levy", on)
    return round(min(max(earnings, 0.0), r["max_earnings"]) * r["rate"], 2)


def student_loan_repayment(income: float, on: date | None = None) -> float:
    r = rule("student_loan", on)
    return round(max(income - r["threshold"], 0.0) * r["rate"], 2)


def esct_rate(salary: float, employer_contribution: float, on: date | None = None) -> float:
    return _band_rate(salary + employer_contribution, rule("esct_brackets", on))


def esct_gross_up(net: float, salary: float, other_employer_gross: float = 0.0,
                  on: date | None = None) -> float:
    """Gross employer contribution from an after-ESCT figure (e.g. a fund statement).

    The ESCT band depends on the gross itself, so iterate to a fixed point.
    """
    gross = net
    for _ in range(20):
        rate = esct_rate(salary, other_employer_gross + gross, on)
        new = net / (1 - rate)
        if abs(new - gross) < 0.005:
            break
        gross = new
    return round(gross, 2)


def take_home_pay(salary: float, kiwisaver_rate: float = 0.0, student_loan: bool = False,
                  on: date | None = None) -> dict[str, float]:
    tax = income_tax(salary, on)
    acc = acc_levy(salary, on)
    sl = student_loan_repayment(salary, on) if student_loan else 0.0
    ks = round(salary * kiwisaver_rate, 2)
    return {"gross": salary, "income_tax": tax, "acc_levy": acc, "student_loan": sl,
            "kiwisaver": ks, "net": round(salary - tax - acc - sl - ks, 2)}


def kiwisaver_govt_contribution(member_contributions: float, income: float, age: int,
                                on: date | None = None) -> float:
    """Annual govt contribution for a 1 July - 30 June year."""
    r = rule("kiwisaver", on)
    nz_super_age = rule("nz_super", on)["age"]
    if income > r["govt_income_cap"] or age < r["govt_min_age"] or age >= nz_super_age:
        return 0.0
    if member_contributions >= r["govt_full_at"]:
        return r["govt_max"]
    return _round_cents(member_contributions * r["govt_rate"])


def kiwisaver_contribution_for_max_govt(on: date | None = None) -> float:
    return rule("kiwisaver", on)["govt_full_at"]


def prescribed_investor_rate(taxable_income: float, pie_income: float = 0.0,
                             prior_taxable_income: float | None = None,
                             prior_pie_income: float = 0.0, on: date | None = None) -> float:
    """Lowest PIR that either of the last two income years qualifies for."""
    thresholds = rule("pir_thresholds", on)

    def for_year(taxable: float, pie: float) -> float:
        for max_taxable, max_total, rate in thresholds:
            if taxable <= max_taxable and taxable + pie <= max_total:
                return rate
        return 0.28

    rates = [for_year(taxable_income, pie_income)]
    if prior_taxable_income is not None:
        rates.append(for_year(prior_taxable_income, prior_pie_income))
    return min(rates)


def fif_status(direct_offshore_cost: float, opening_market_value: float, marginal_rate: float,
               on: date | None = None) -> dict[str, Any]:
    """FIF position for one person. Only pass direct offshore holdings (not PIE funds,
    not exempt ASX shares)."""
    r = rule("fif", on)
    over = direct_offshore_cost > r["de_minimis_cost"]
    fdr_income = round(opening_market_value * r["fdr_rate"], 2) if over else 0.0
    return {
        "over_threshold": over,
        "headroom": round(max(r["de_minimis_cost"] - direct_offshore_cost, 0.0), 2),
        "fdr_income": fdr_income,
        "estimated_tax": round(fdr_income * marginal_rate, 2),
    }
