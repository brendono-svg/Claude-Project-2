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
    # Plenty in KiwiSaver, little outside it, retiring at 55: the reachable money runs out first.
    p = base(people=[{"name": "A", "age": 50, "salary": 120_000,
                      "kiwisaver": {"balance": 900_000, "employee_rate": 0.035, "fund_type": "growth"}}],
             preferences={"retirement_age": 55, "retirement_income_target": 60_000})
    a = analyse(p, on=ON)
    assert a.retirement["locked_problem"] is True
    assert any("runs out before KiwiSaver opens" in t for t in titles(a))


def two_schemes(**prefs):
    return base(people=[{
        "name": "F", "age": 50, "salary": 137_400,
        "schemes": [
            {"name": "KiwiSaver", "kind": "kiwisaver", "balance": 110_000, "fund_type": "growth",
             "member": {"amount": 4_241.40, "unit": "six_months"},
             "employer": {"amount": 2_588.44, "unit": "six_months", "after_tax": True}},
            {"name": "Work scheme", "kind": "workplace", "balance": 120_000, "fund_type": "growth",
             "member": {"amount": 121.17, "unit": "fortnight"},
             "employer": {"amount": 369.92, "unit": "fortnight"}},
        ]}], preferences={"retirement_age": 60, **prefs})


def test_contribution_units_and_esct():
    from kiwiwealth.advice import scheme_flows
    ks, wp = scheme_flows(two_schemes().people[0], ON)
    assert ks.member == pytest.approx(8_482.80)
    assert wp.member == pytest.approx(3_150.42)
    assert wp.employer_gross == pytest.approx(9_617.92)
    assert ks.employer_net == pytest.approx(5_176.88, abs=0.05)  # statement figure was already net
    assert ks.govt == 260.72 and wp.govt == 0  # govt contribution is KiwiSaver-only


def test_workplace_scheme_counts_for_bridge_and_has_own_advice():
    a = analyse(two_schemes(), on=ON)
    assert a.retirement["workplace_projected"] > 120_000
    assert any("plan for the Work scheme payout" in t for t in titles(a))


def test_kiwisaver_lock_warning_when_retiring_early():
    t = titles(analyse(two_schemes(), on=ON))
    assert any("locked until 65" in x for x in t)
    t65 = titles(analyse(two_schemes(retirement_age=65), on=ON))
    assert not any("locked until 65" in x for x in t65)


def test_legacy_kiwisaver_field_still_loads():
    p = base()
    assert p.people[0].schemes[0].kind == "kiwisaver"
    assert p.total_kiwisaver == 50_000


def test_parity_cases_match_python_engine():
    """tests/parity_cases.json is also run through the phone app's engine by
    scripts/check_app_engine.js, so this keeps the two engines in step."""
    import json
    import sys
    sys.path.insert(0, str(EXAMPLES.parent / "scripts"))
    from update_parity import expected
    cases = json.loads((EXAMPLES.parent / "tests" / "parity_cases.json").read_text())
    assert len(cases) >= 5
    for c in cases:
        assert expected(c["state"], date.fromisoformat(c["on"])) == c["expected"], c["name"]


def test_per_person_retirement_age_and_super():
    # 9-year age gap: the younger partner keeps working, and gets no NZ Super until their own 65.
    couple = lambda younger_retires: base(  # noqa: E731
        people=[{"name": "Old", "age": 60, "salary": 100_000, "retirement_age": 62,
                 "kiwisaver": {"balance": 300_000, "employee_rate": 0.035}},
                {"name": "Young", "age": 51, "salary": 80_000, "retirement_age": younger_retires,
                 "kiwisaver": {"balance": 100_000, "employee_rate": 0.035}}],
        preferences={"retirement_income_target": 80_000})
    both_stop = analyse(couple(53), on=ON).retirement      # Young stops when Old does
    young_works = analyse(couple(62), on=ON).retirement
    assert young_works["sustainable_income"] > both_stop["sustainable_income"]
    assert both_stop["nz_super_annual"] == 44_412


def test_app_json_loads(tmp_path):
    import json
    cases = json.loads((EXAMPLES.parent / "tests" / "parity_cases.json").read_text())
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"profile": cases[1]["state"], "csv": None}))
    p = load_profile(f)
    assert [x.name for x in p.people] == ["Kahu", "Mere"] and p.people[0].retirement_age == 55
    assert p.people[0].schemes[1].kind == "workplace" and p.people[0].schemes[1].pie is False


def test_low_interest_cash_flagged():
    p = base(cash=[{"name": "Savings", "balance": 30_000, "interest_rate": 0.0}])
    assert any("cash is earning very little" in t for t in titles(analyse(p, on=ON)))


def offset_household(**over):
    d = dict(
        loans=[{"name": "Fixed loan", "balance": 92_000, "rate": 0.0495, "monthly_repayment": 1_092, "fixed": True},
               {"name": "Offset loan", "balance": 200_000, "rate": 0.059, "monthly_repayment": 1_324,
                "kind": "offset", "offset_cash": 200_000}],
        mortgage=None, cash=[{"name": "Savings", "balance": 20_000, "interest_rate": 0.03}],
        budget={"monthly_net_income": 19_700, "monthly_spending": 11_300})
    d.update(over)
    return base(**d)


def test_offset_loan_advice():
    p = offset_household()
    assert p.total_cash == 220_000 and p.total_debt == 292_000   # offset cash is an asset
    a = analyse(p, on=ON)
    t = titles(a)
    assert "Pay down Fixed loan first" in t
    assert "Offset loan: your offset cash covers the whole loan" in t
    assert "Consider an offset or revolving portion" not in t
    assert "You may be holding too much cash" not in t        # offset cash is doing its job
    assert "Build the emergency fund" not in t                # and it counts as accessible
    spare = next(r for r in a.recommendations if "surplus has no job" in r.title)
    assert spare.numbers["payoff_months_now"] == 104 and spare.numbers["payoff_months"] < 14
    assert "break fees" in spare.action                       # fixed-rate caveat


def test_fully_offset_only_loan_and_legacy_mortgage():
    p = offset_household(loans=[{"name": "Offset loan", "balance": 100_000, "rate": 0.059,
                                 "monthly_repayment": 800, "kind": "offset", "offset_cash": 150_000}])
    assert "Your home lending is fully offset" in titles(analyse(p, on=ON))
    legacy = base()   # uses the old single `mortgage` field
    assert [l.name for l in legacy.loans] == ["Mortgage"] and legacy.loans[0].balance == 50_000
    assert "Finish the mortgage, then redirect the repayment" in titles(analyse(legacy, on=ON))


def test_loans_take_precedence_over_legacy_mortgage():
    from kiwiwealth.profile import profile_from_app_state
    p = offset_household(mortgage={"balance": 50_000, "rate": 0.05, "monthly_repayment": 2_000})
    assert p.total_debt == 292_000                      # the legacy mortgage isn't counted on top
    state = {"people": [{"name": "A", "age": 40}], "loans": [],
             "mortgage": {"balance": 50_000, "rate": 0.05, "repayment": 2_000}}
    assert profile_from_app_state(state).loans == []    # an empty loans list means no loans
    del state["loans"]
    assert profile_from_app_state(state).loans[0].balance == 50_000
