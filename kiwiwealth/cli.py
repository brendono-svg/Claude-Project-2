"""Command line: `python -m kiwiwealth report my-data/profile.yaml --budget export.csv`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .advice import analyse
from .budget_import import load_budget_files
from .profile import load_profile
from .report import render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kiwiwealth", description="NZ household financial planner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("report", help="Build the full financial plan")
    rp.add_argument("profile")
    rp.add_argument("--budget", nargs="*", default=[], help="Transaction CSV export(s)")
    rp.add_argument("-o", "--output", help="Write Markdown here instead of stdout")

    bp = sub.add_parser("budget", help="Summarise transaction CSVs only")
    bp.add_argument("files", nargs="+")
    bp.add_argument("--json", action="store_true")

    for p in (rp, bp):
        p.add_argument("--map", help='Column names as JSON, e.g. \'{"date":"When","amount":"NZD"}\'')

    args = ap.parse_args(argv)
    cmap = json.loads(args.map) if args.map else None

    if args.cmd == "budget":
        s = load_budget_files(args.files, cmap)
        if args.json:
            print(json.dumps(s.__dict__ | {"savings_rate": s.savings_rate}, indent=2))
        else:
            print(f"{s.transactions} transactions over {s.months} months "
                  f"({s.excluded_transfers} transfers excluded)")
            print(f"Income   ${s.monthly_income:,.0f}/month")
            print(f"Spending ${s.monthly_spending:,.0f}/month")
            print(f"Surplus  ${s.monthly_surplus:,.0f}/month  (savings rate {s.savings_rate:.0%})")
            for k, v in s.categories.items():
                print(f"  {k:<24} ${v:,.0f}")
        return 0

    profile = load_profile(args.profile)
    budget = load_budget_files(args.budget, cmap) if args.budget else None
    md = render(analyse(profile, budget))
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(md)
    return 0
