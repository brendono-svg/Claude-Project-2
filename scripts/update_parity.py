"""Recompute the expected values in tests/parity_cases.json from the Python engine.

Run after an intended change to the advice rules, then make the same change in
app/kiwiwealth.html until `node scripts/check_app_engine.js` passes.
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kiwiwealth.advice import analyse  # noqa: E402
from kiwiwealth.profile import profile_from_app_state  # noqa: E402

CASES = ROOT / "tests" / "parity_cases.json"


def expected(state: dict, on: date) -> dict:
    a = analyse(profile_from_app_state(state), on=on)
    r = a.retirement
    return {
        "net_worth": round(a.profile.net_worth),
        "ks": r["kiwisaver_projected"], "wp": r["workplace_projected"], "acc": r["investments_projected"],
        "sustainable": r["sustainable_income"], "extra": r["extra_monthly_needed"],
        "short_year": r["shortfall_year"], "locked_problem": r["locked_problem"],
        "titles": sorted(f"{x.priority} {x.title}" for x in a.recommendations),
    }


if __name__ == "__main__":
    cases = json.loads(CASES.read_text())
    for c in cases:
        c["expected"] = expected(c["state"], date.fromisoformat(c["on"]))
    CASES.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n")
    print(f"updated {len(cases)} cases")
