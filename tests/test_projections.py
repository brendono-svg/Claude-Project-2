import pytest

from kiwiwealth.projections import (after_tax_return, future_value, months_to_repay,
                                    required_monthly, total_interest)


def test_future_value_zero_rate():
    assert future_value(1_000, 100, 0.0, 1) == 2_200


def test_future_value_and_required_monthly_roundtrip():
    need = required_monthly(100_000, 10_000, 0.05, 10)
    assert future_value(10_000, need, 0.05, 10) == pytest.approx(100_000)


def test_required_monthly_already_there():
    assert required_monthly(10_000, 20_000, 0.05, 5) == 0


def test_months_to_repay():
    assert months_to_repay(0, 0.05, 1_000) == 0
    assert months_to_repay(100_000, 0.06, 400) is None  # interest-only or worse
    n = months_to_repay(68_000, 0.0495, 3_200)
    assert 22 <= n <= 23
    assert total_interest(68_000, 0.0495, 3_200) == pytest.approx(3_250, rel=0.1)


def test_after_tax_return():
    # cash: whole return taxed
    assert after_tax_return(0.04, 0.0, 0.28) == pytest.approx(0.0288)
    # PIE growth fund: only the taxable yield is taxed
    assert after_tax_return(0.07, 0.004, 0.28, 0.048) == pytest.approx(0.07 - 0.004 - 0.01344)


def test_simulation_basics():
    from kiwiwealth.retirement import SimPerson, SimScheme, simulate
    args = dict(invested=500_000, monthly_invest=0, mortgages=[],
                r_grow=0.0, r_draw=0.0, super_age=65, super_per_person=25_000, plan_age=95)
    # Retired at 65 with $500k at 0% real: $25k Super + $20k from savings lasts 25 years, to age 90.
    r = simulate([SimPerson(65, 65, 0)], target=45_000, **args)
    assert r.first_short == 25 * 12 and r.short_total == pytest.approx(20_000 * 5, rel=1e-6)
    # Locked KiwiSaver can't fund the years before 65; unlocked it can.
    young = [SimPerson(60, 60, 0, [SimScheme("kiwisaver", 400_000, 0.0, 0)])]
    args["invested"] = 50_000
    assert simulate(young, target=40_000, **args).first_short == 15
    assert simulate(young, target=40_000, lock=False, **args).first_short > 15 * 12


def test_loan_schedule_offset():
    from kiwiwealth.projections import loan_schedule
    # Fully offset: no interest, repayments are all principal.
    assert loan_schedule(200_000, 0.0495, 1_324, offset=200_000) == (152, 0.0)
    # A plain table loan pays interest on the whole balance.
    months, interest = loan_schedule(92_000, 0.0495, 1_092)
    assert months == 104 and interest == pytest.approx(21_268, abs=5)
    # Half offset costs less interest than none.
    assert loan_schedule(100_000, 0.05, 1_000, offset=50_000)[1] < loan_schedule(100_000, 0.05, 1_000)[1]
    assert loan_schedule(100_000, 0.06, 400) == (None, None)
