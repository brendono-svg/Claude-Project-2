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
