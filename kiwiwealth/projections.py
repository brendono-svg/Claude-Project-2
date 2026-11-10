"""Growth, goal and mortgage maths. Monthly compounding throughout."""
from __future__ import annotations

import math


def future_value(present: float, monthly: float, annual_rate: float, years: float) -> float:
    n = round(years * 12)
    r = annual_rate / 12
    if n <= 0:
        return present
    if r == 0:
        return present + monthly * n
    g = (1 + r) ** n
    return present * g + monthly * (g - 1) / r


def required_monthly(target: float, present: float, annual_rate: float, years: float) -> float:
    """Monthly saving needed to reach `target` from `present`."""
    n = round(years * 12)
    if n <= 0:
        return max(target - present, 0.0)
    r = annual_rate / 12
    shortfall = target - present * (1 + r) ** n
    if shortfall <= 0:
        return 0.0
    return shortfall / n if r == 0 else shortfall * r / ((1 + r) ** n - 1)


def months_to_repay(balance: float, annual_rate: float, monthly_payment: float) -> int | None:
    """None if the payment never clears the loan."""
    if balance <= 0:
        return 0
    r = annual_rate / 12
    if r == 0:
        return math.ceil(balance / monthly_payment) if monthly_payment > 0 else None
    if monthly_payment <= balance * r:
        return None
    return math.ceil(-math.log(1 - r * balance / monthly_payment) / math.log(1 + r))


def total_interest(balance: float, annual_rate: float, monthly_payment: float) -> float | None:
    n = months_to_repay(balance, annual_rate, monthly_payment)
    if n is None:
        return None
    r = annual_rate / 12
    paid, b = 0.0, balance
    for _ in range(n):
        interest = b * r
        pay = min(monthly_payment, b + interest)
        paid += pay
        b = b + interest - pay
    return round(paid - balance, 2)


def loan_schedule(balance: float, annual_rate: float, monthly_payment: float,
                  offset: float = 0.0) -> tuple[int | None, float | None]:
    """(months to repay, total interest) for a loan. Interest is charged only on the part
    of the balance not covered by offset cash, which is held constant (repayments are
    assumed to come from income). (None, None) if the payment never clears the loan."""
    if balance <= 0:
        return 0, 0.0
    if monthly_payment <= 0:
        return None, None
    r, b, paid, n = annual_rate / 12, balance, 0.0, 0
    while b > 0.005:
        interest = max(b - offset, 0.0) * r
        if monthly_payment <= interest or n >= 1200:
            return None, None
        pay = min(monthly_payment, b + interest)
        paid += pay
        b = b + interest - pay
        n += 1
    return n, round(paid - balance, 2)


def after_tax_return(gross: float, fee: float, tax_rate: float,
                     taxable_yield: float | None = None) -> float:
    """Expected yearly return after fees and tax.

    taxable_yield is the part of the return taxed each year (e.g. ~5% under FDR for
    global shares in a PIE). None means the whole return is taxable (cash, bonds).
    """
    taxed = gross if taxable_yield is None else min(taxable_yield, gross)
    return gross - fee - tax_rate * taxed


def real_rate(nominal: float, inflation: float) -> float:
    return (1 + nominal) / (1 + inflation) - 1


def invest_vs_repay(lump_sum: float, mortgage_rate: float, invest_after_tax: float,
                    years: float) -> dict[str, float]:
    """Compare putting a lump sum on the mortgage (a guaranteed tax-free return,
    because home-loan interest isn't deductible) with investing it."""
    repay = future_value(lump_sum, 0, mortgage_rate, years)
    invest = future_value(lump_sum, 0, invest_after_tax, years)
    return {"repay_value": round(repay, 2), "invest_value": round(invest, 2),
            "difference": round(invest - repay, 2)}
