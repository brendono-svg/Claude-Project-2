from datetime import date

import pytest

from kiwiwealth import nz_rules as nz

ON = date(2026, 11, 8)


@pytest.mark.parametrize("income,tax", [
    (0, 0), (15_600, 1_638.00), (53_500, 8_270.50), (78_100, 15_650.50),
    (100_000, 22_877.50), (180_000, 49_277.50), (200_000, 57_077.50),
])
def test_income_tax(income, tax):
    assert nz.income_tax(income, ON) == pytest.approx(tax)


def test_marginal_rate():
    assert nz.marginal_tax_rate(60_000, ON) == 0.30
    assert nz.marginal_tax_rate(180_001, ON) == 0.39


def test_govt_contribution_25c_per_dollar_capped():
    assert nz.kiwisaver_contribution_for_max_govt(ON) == 1_042.86
    assert nz.kiwisaver_govt_contribution(1_042.86, 60_000, 40, ON) == 260.72
    assert nz.kiwisaver_govt_contribution(1_042.85, 60_000, 40, ON) == 260.71
    assert nz.kiwisaver_govt_contribution(500, 60_000, 40, ON) == 125.00
    assert nz.kiwisaver_govt_contribution(5_000, 60_000, 40, ON) == 260.72


def test_govt_contribution_exclusions():
    assert nz.kiwisaver_govt_contribution(2_000, 185_000, 40, ON) == 0  # income cap
    assert nz.kiwisaver_govt_contribution(2_000, 60_000, 65, ON) == 0   # NZ Super age
    assert nz.kiwisaver_govt_contribution(2_000, 10_000, 15, ON) == 0   # under 16
    assert nz.kiwisaver_govt_contribution(2_000, 10_000, 16, ON) == 260.72


def test_kiwisaver_rates_change_by_date():
    assert nz.rule("kiwisaver", date(2026, 3, 31))["default_employee_rate"] == 0.03
    assert nz.rule("kiwisaver", date(2026, 4, 1))["default_employee_rate"] == 0.035
    assert nz.rule("kiwisaver", date(2028, 4, 1))["default_employee_rate"] == 0.04


@pytest.mark.parametrize("taxable,pie,prior,expected", [
    (14_000, 5_000, None, 0.105),
    (14_000, 45_000, None, 0.175),   # total over 53,500
    (50_000, 10_000, None, 0.175),
    (50_000, 30_000, None, 0.28),    # total over 78,100
    (120_000, 0, None, 0.28),
    (120_000, 0, 40_000, 0.175),     # lower of last two years
])
def test_pir(taxable, pie, prior, expected):
    assert nz.prescribed_investor_rate(taxable, pie, prior, on=ON) == expected


def test_fif():
    under = nz.fif_status(49_000, 60_000, 0.33, ON)
    assert not under["over_threshold"] and under["headroom"] == 1_000 and under["estimated_tax"] == 0
    over = nz.fif_status(51_000, 60_000, 0.33, ON)
    assert over["over_threshold"]
    assert over["fdr_income"] == 3_000
    assert over["estimated_tax"] == 990


def test_take_home_pay_components():
    t = nz.take_home_pay(100_000, 0.035, student_loan=True, on=ON)
    assert t["income_tax"] == pytest.approx(22_877.50)
    assert t["acc_levy"] == pytest.approx(1_750.00)
    assert t["student_loan"] == pytest.approx((100_000 - 24_128) * 0.12)
    assert t["net"] == pytest.approx(100_000 - 22_877.50 - 1_750 - 9_104.64 - 3_500)


def test_esct():
    assert nz.esct_rate(60_000, 2_100, ON) == 0.175
    assert nz.esct_rate(250_000, 8_750, ON) == 0.39


def test_esct_gross_up_roundtrip():
    # Six months of statement (after-tax) employer KiwiSaver, annualised.
    net = 2_588.44 * 2
    gross = nz.esct_gross_up(net, 137_400, other_employer_gross=9_617.92, on=ON)
    rate = nz.esct_rate(137_400, 9_617.92 + gross, ON)
    assert rate == 0.33
    assert gross * (1 - rate) == pytest.approx(net, abs=0.02)
