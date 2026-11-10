# Claude-Project-2

## kiwiwealth: NZ family financial planner

A local, private tool that turns your household's numbers into a ranked NZ-specific
action plan, written as a Markdown report. It covers:

- **Balance sheet:** net worth, and how much of it is tied up in the house
- **Savings habits** from your budgeting app or bank CSV exports: savings rate, top
  spending categories, and surplus with no job
- **Home loans:** several loans, including offset loans (interest only on the part not covered by
  offset cash). It picks the loan where extra repayments save the most, shows how fast spare cash
  clears it, and redirects each repayment into investments once that loan is gone
- **KiwiSaver:** the government contribution (25c per $1 up to $260.72, nil over $180k),
  the 3.5% rates from 1 Apr 2026, fund type vs timeframe, fees
- **Tax:** PIR check (lowest of the last two years), FIF $50k threshold per person
  (PIE funds excluded), putting taxable investments in the lower earner's name,
  and the 28% PIE cap for 33%/39% earners
- **Investment plan:** core low-fee PIE funds plus a small satellite of shares,
  concentration and fee checks
- **Goals:** monthly amount needed per goal, with cash, balanced or growth matched to the timeframe
- **Retirement:** a month-by-month simulation to age 95 in today's dollars. Each person has their own
  retirement age, KiwiSaver stays locked until 65, workplace schemes pay out on leaving, and NZ Super
  starts at each person's 65. It reports the income your savings can support and when reachable money runs out
- **Downsizing scenario** for a large house, plus protection (wills, income cover)

> General information for our own use, not regulated financial advice. Offering it to
> other people as personalised advice would require a Financial Advice Provider licence.

### Quick start

```bash
pip install -r requirements.txt
mkdir -p my-data && cp examples/profile.example.yaml my-data/profile.yaml   # my-data/ is git-ignored
python -m kiwiwealth budget path/to/transactions.csv          # check the budget import
python -m kiwiwealth report my-data/profile.yaml --budget path/to/transactions.csv -o my-data/plan.md
python -m pytest -q
node scripts/check_app_engine.js      # phone app engine vs Python on tests/parity_cases.json
```

To run the Python report on the phone app's data, use **Details → Copy my data** in the app, save it as
`my-data/app.json`, and run `python -m kiwiwealth report my-data/app.json`.

Try it with the examples: `python -m kiwiwealth report examples/profile.example.yaml --budget examples/budget-export.example.csv`

**Budget CSVs:** the format is auto-detected for exports with Date/Description/Amount/Category
(most budgeting apps) and for ANZ, ASB, BNZ, Kiwibank and Westpac. If your app uses other
column names, map them with `--map '{"date":"When","amount":"Value","category":"Group"}'`.
Rows categorised as `Transfer` are left out. Give that category to moves between your own accounts, automatic investment contributions, and (if you also import the card CSV) credit-card repayments.

### Phone app

`app/kiwiwealth.html` is the same engine ported to a single offline page for phones. It has tabs for the plan, future projections, budget CSV import and details. It syncs one shared household record between everyone the page is shared with as Contributor or Editor (view-only people cannot read it), and keeps a local copy so it still works offline. When you change a rule, change it in both engines, run `python scripts/update_parity.py`, and make `node scripts/check_app_engine.js` pass. CI runs both.

### Layout

| File | What it does |
| --- | --- |
| `kiwiwealth/nz_rules.py` | Dated NZ rules (tax, ACC, student loan, ESCT, KiwiSaver, PIR, FIF, NZ Super), each with an IRD/govt source link |
| `kiwiwealth/profile.py` | YAML household profile, validation, default assumptions |
| `kiwiwealth/budget_import.py` | CSV import and savings-habit summary |
| `kiwiwealth/projections.py` | Compounding, goal, mortgage and after-tax return maths |
| `kiwiwealth/advice.py` | Rule engine: one function per rule, ranked output |
| `kiwiwealth/retirement.py` | Month-by-month retirement simulation (mirrored in the phone app) |
| `kiwiwealth/report.py`, `cli.py` | Markdown report and command line |

To update a rule after a Budget, add a new dated entry in `nz_rules.py` and leave the old ones in place.

---


## Claude Code setup: Sonnet builds, Haiku explores, Opus reviews

Works on a Claude Pro/Max subscription login - no API key or advisor feature needed.

| Piece | File | What it does |
| --- | --- | --- |
| Main model | `.claude/settings.json` | Session runs on Sonnet |
| Explorers | `.claude/agents/{search,grep,docs}.md` | Read-only Haiku subagents |
| Advisor | `.claude/agents/advisor.md` | Read-only Opus subagent, gets a short brief only |
| `/consult` | `.claude/skills/consult/SKILL.md` | Builds the brief and calls the advisor |
| Repeat-error guard | `.claude/hooks/repeat_error.py` | Zero-token hook: on the 2nd identical tool failure, tells Claude to stop and `/consult` |
| Rules | `CLAUDE.md` | When to consult, plus usage-saving habits |

Requires `python3` on your PATH for the hook. If Opus isn't available on your
plan, change `model: opus` in `advisor.md` to `sonnet`.

Alternative with no setup: `/model opusplan` uses Opus in plan mode and Sonnet
when executing.
