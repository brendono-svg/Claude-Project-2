from datetime import date
from pathlib import Path

import pytest

from kiwiwealth.advice import analyse
from kiwiwealth.budget_import import load_budget_files
from kiwiwealth.profile import load_profile, profile_from_dict
from kiwiwealth.report import render

ON = date(2026, 11, 8)
EXAMPLES = Path(__file__).parent.parent / "examples"


def base(**over):
    d = {
        "household": "Test",
        "people": [{"name": "A", "age": 40, "salary": 90_000,
                    "kiwisaver": {"balance": 50_000, "employee_rate": 0.035, "fund_type": "growth"}}],
        "home_value": 1_000_000,
        "cash": [{"name": "Savings", "balance": 30_000}],
        "mortgage": {"balance": 50_000, "rate": 0.05, "monthly_repayment": 2_000},
        "budget": {"monthly_net_income": 6_000, "monthly_spending": 4_500},
        "has_wills": True,
    }
    d.update(over)
    return profile_from_dict(d)


def titles(a):
    return [r.title for r in a.recommendations]


def test_expensive_debt_is_top_priority():
    a = analyse(base(debts=[{"name": "credit card", "balance": 3_000, "rate": 0.21}]), on=ON)
    assert a.recommendations[0].priority == 1
    assert any("credit card" in t for t in titles(a))


def test_kiwisaver_topup_when_below_govt_threshold():
    p = base(people=[{"name": "B", "age": 35, "salary": 20_000,
                      "kiwisaver": {"balance": 5_000, "employee_rate": 0.035}}])
    rec = next(r for r in analyse(p, on=ON).recommendations if "top up" in r.title)
    assert rec.numbers["contributing"] == 700
    assert rec.numbers["govt_contribution"] == 175


def test_no_topup_over_income_cap():
    p = base(people=[{"name": "C", "age": 45, "salary": 200_000,
                      "kiwisaver": {"balance": 5_000, "employee_rate": 0.035}}])
    t = titles(analyse(p, on=ON))
    assert not any("top up" in x for x in t)
    assert any("no government contribution" in x for x in t)


def test_pir_mismatch_flagged():
    p = base(people=[{"name": "D", "age": 40, "salary": 40_000, "current_pir": 0.28,
                      "kiwisaver": {"balance": 1, "employee_rate": 0.035}}])
    rec = next(r for r in analyse(p, on=ON).recommendations if "PIR is set" in r.title)
    assert rec.priority == 1


def test_low_emergency_fund():
    p = base(cash=[{"name": "Savings", "balance": 2_000}])
    assert any("emergency fund" in t.lower() for t in titles(analyse(p, on=ON)))


def test_fif_over_threshold():
    p = base(holdings=[{"name": "US", "owner": "A", "type": "direct_offshore",
                        "value": 70_000, "cost": 60_000, "single_company": False}])
    rec = next(r for r in analyse(p, on=ON).recommendations if "FIF rules apply" in r.title)
    assert rec.numbers["fdr_income"] == 3_500


def test_pie_funds_not_counted_for_fif():
    p = base(holdings=[{"name": "Global PIE", "owner": "A", "type": "pie", "value": 200_000, "cost": 150_000}])
    assert not any("FIF" in t for t in titles(analyse(p, on=ON)))


def test_bad_profile_rejected():
    with pytest.raises(ValueError):
        base(holdings=[{"name": "X", "owner": "nobody", "value": 1}])
    with pytest.raises(ValueError):
        base(preferences={"risk_tolerance": "yolo"})


def test_example_report_renders():
    profile = load_profile(EXAMPLES / "profile.example.yaml")
    budget = load_budget_files([EXAMPLES / "budget-export.example.csv"])
    md = render(analyse(profile, budget, on=ON))
    assert "## Action plan" in md and "Retirement projection" in md
    assert "not financial advice" in md


def test_pir_uses_prior_year_pie_income():
    # Taxable 50k + PIE 30k = 80k, over the 78,100 limit both years, so 28%.
    p = base(people=[{"name": "E", "age": 40, "salary": 50_000, "pie_income": 30_000,
                      "prior_year_taxable_income": 50_000, "current_pir": 0.28,
                      "kiwisaver": {"balance": 1, "employee_rate": 0.035}}])
    a = analyse(p, on=ON)
    assert a.pirs["E"] == 0.28
    assert not any("should be" in t for t in titles(a))


def test_bridge_before_kiwisaver_access():
    p = base(preferences={"retirement_age": 55, "retirement_income_target": 80_000})
    assert any("needs savings outside KiwiSaver" in t for t in titles(analyse(p, on=ON)))
