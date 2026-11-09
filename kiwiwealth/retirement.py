"""Month-by-month retirement simulation in today's dollars.

The phone app (app/kiwiwealth.html, `simulate`) mirrors this function line for line;
tests/parity_cases.json keeps the two in step. Change both together.

Model, per month until the youngest person reaches `plan_age`:
  * Each person works until their own retirement age. While working, their schemes
    receive contributions (KiwiSaver contributions stop at NZ Super age).
  * A workplace scheme moves to the accessible pot when its owner stops work.
    KiwiSaver moves there when its owner reaches NZ Super age.
  * Until the first person retires, regular investing continues, plus the old
    mortgage repayment once the mortgage is paid off.
  * From the first retirement, the household draws `target` a year from the accessible
    pot, less the take-home pay of anyone still working and less NZ Super for each
    person over the qualifying age. Any mortgage repayments still owing are added.
    Pay above the target is not saved (a cautious simplification).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimScheme:
    kind: str            # "kiwisaver" | "workplace"
    balance: float
    rate: float          # real yearly return after fees and tax
    contribution: float  # yearly total going in while the owner works


@dataclass
class SimPerson:
    age: int
    retire_age: int
    net_pay: float       # yearly take-home while working, after scheme contributions
    schemes: list[SimScheme] = field(default_factory=list)


@dataclass
class SimResult:
    draw_start: int                 # month the first person retires
    at_retirement: dict[str, float]  # balances at draw_start: ks, wp, acc
    first_short: int | None         # first month the accessible pot can't cover the need
    short_total: float
    end_balance: float
    series: list[dict[str, float]]  # yearly: {year_index, ks, wp, acc}


def simulate(people: list[SimPerson], invested: float, monthly_invest: float,
             mortgage_months: int | None, mortgage_repayment: float, target: float,
             r_grow: float, r_draw: float, super_age: int, super_per_person: float,
             plan_age: int = 95, lock: bool = True, extra_monthly: float = 0.0) -> SimResult:
    total_months = max(plan_age - min(p.age for p in people), 0) * 12
    retire_m = [max(p.retire_age - p.age, 0) * 12 for p in people]
    super_m = [max(super_age - p.age, 0) * 12 for p in people]
    draw_start = min(retire_m)
    bal = [[s.balance for s in p.schemes] for p in people]
    acc = invested
    first_short, short_total = None, 0.0
    at_ret = {"ks": 0.0, "wp": 0.0, "acc": 0.0}
    series = []

    def pots():
        ks = sum(b for p, bs in zip(people, bal) for s, b in zip(p.schemes, bs) if s.kind == "kiwisaver")
        wp = sum(b for p, bs in zip(people, bal) for s, b in zip(p.schemes, bs) if s.kind != "kiwisaver")
        return ks, wp

    for t in range(total_months + 1):
        working = [t < rm for rm in retire_m]
        over = [t >= sm for sm in super_m]
        if t == draw_start:  # snapshot before anything is released on this date
            ks, wp = pots()
            at_ret = {"ks": ks, "wp": wp, "acc": acc}
        # Release schemes that have become accessible.
        for i, p in enumerate(people):
            for j, s in enumerate(p.schemes):
                if s.kind == "kiwisaver":
                    free = over[i] or (not lock and not working[i])
                else:
                    free = not working[i]
                if free and bal[i][j]:
                    acc += bal[i][j]
                    bal[i][j] = 0.0
        if t % 12 == 0:
            ks, wp = pots()
            series.append({"year_index": t // 12, "ks": ks, "wp": wp, "acc": acc})
        if t == total_months:
            break
        # Schemes grow and receive contributions.
        for i, p in enumerate(people):
            for j, s in enumerate(p.schemes):
                if s.kind == "kiwisaver":
                    locked = not (over[i] or (not lock and not working[i]))
                    paying = working[i] and not over[i]
                else:
                    locked = working[i]
                    paying = working[i]
                if locked:
                    bal[i][j] = bal[i][j] * (1 + s.rate / 12) + (s.contribution / 12 if paying else 0.0)
        mortgage_open = mortgage_months is None or t < mortgage_months
        if t < draw_start:
            acc = acc * (1 + r_grow / 12) + monthly_invest + extra_monthly
            if not mortgage_open:
                acc += mortgage_repayment
        else:
            acc *= 1 + r_draw / 12
            need = target / 12 + (mortgage_repayment if mortgage_open else 0.0)
            need -= sum(p.net_pay / 12 for p, w in zip(people, working) if w)
            need -= sum(super_per_person / 12 for o in over if o)
            if need > 0:
                if acc + 1e-6 >= need:   # tolerance for float rounding
                    acc = max(acc - need, 0.0)
                else:
                    short_total += need - acc
                    acc = 0.0
                    if first_short is None:
                        first_short = t
    return SimResult(draw_start, at_ret, first_short, short_total, acc, series)


def bisect(ok, lo: float, hi: float, iterations: int = 40) -> float:
    """Largest x in [lo, hi] with ok(x) true, assuming ok is true at lo and monotonic."""
    for _ in range(iterations):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo
