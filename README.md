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


## sharepicker: NZX and ASX share screener

A local, private research tool for picking the **satellite** of individual shares that the
kiwiwealth plan leaves room for. It ranks NZX and ASX companies on value, quality, growth,
momentum, income and risk, explains each rank, and gets the trans-Tasman tax details right
for a **New Zealand resident**:

- **Dividends:** NZ imputation credits are added back. Australian franking credits are *not*
  (an NZ resident can't use them), so ASX yields use the cash dividend, less 15% withholding
  on the unfranked part, which is then credited against NZ tax. Trans-Tasman NZ credits paid by
  some Australian companies are counted. The report flags the gap between the Australian
  "headline" grossed-up yield and what you actually get.
- **FIF:** NZ-resident companies are never FIFs, wherever they list. Australian-resident
  companies in the All Ordinaries with a franking account are exempt (index membership is
  checked on the screen date). Everything else counts toward each person's $50k threshold.
  The labels match kiwiwealth's holding types (`nz_listed`, `asx_exempt`, `direct_offshore`).
- **Dual listings** (one company on NZX and ASX) are scored once, on the listing that trades the most.
- **Costs:** an illustrative broker fee table (Sharesies, Hatch, ASB, Jarden) with FX spread,
  overridable in your config.

> General information for our own use, not regulated financial advice. Offering it to others
> as personalised advice would need a Financial Advice Provider licence (NZ) or an AFSL (Australia).

### Quick start

```bash
python -m sharepicker sample-data my-data/sample        # FICTIONAL companies to try it on
cp examples/screen.example.yaml my-data/screen.yaml     # then edit data.folder and filters
python -m sharepicker screen my-data/screen.yaml -o my-data/screen.md --json my-data/screen.json
python -m sharepicker screen my-data/screen.yaml --preset income      # balanced | value | quality | income | momentum
python -m sharepicker explain my-data/screen.yaml KAU.NZ              # every metric and percentile for one share
python -m sharepicker backtest my-data/screen.yaml --start 2021-01-01 -o my-data/backtest.md
python -m sharepicker watchlist my-data/holdings.yaml -o my-data/watch.md
python -m sharepicker fetch my-data/screen.yaml FPH.NZ AIA.NZ CBA.AX BHP.AX   # live Yahoo check
```

The example configs point at `my-data/sample` and `examples/profile.example.yaml`, so they work
straight after `sample-data`.

- **screen** ranks the universe, explains the top picks (strengths, weaknesses, the points each
  factor adds, dividend tax, FIF status, red and green flags) and lists what was left out and why.
- **backtest** re-runs the screen every month using only data available at the time, holds the
  top N, and reports CAGR, volatility, drawdown, turnover and months ahead of the NZX 50 and ASX 200,
  after brokerage, FX and tax on dividends. FIF tax on non-exempt holdings is not modelled.
- **watchlist** scores what you hold and shows after-tax dividends using each owner's tax rate and
  FIF position, concentration by share, sector and country, FIF headroom per person, and how much
  room the kiwiwealth satellite has left. It then suggests high-ranked shares that fit your limits,
  or swaps for your lowest-ranked holdings when the satellite is full.
- **app/sharepicker.html** opens the `--json` output on a phone or desktop. You can move the
  factor weights and watch the ranking change, filter (NZX/ASX, sector, no red flags, pays
  dividends, not FIF) and open any share for its full breakdown. "Try the fictional demo" works offline.

### How scoring works

Each metric becomes a percentile (0-100) within the screened universe, with ties sharing a rank.
Value and quality are averaged with the percentile inside the sector when the sector has three
or more members. Metrics roll up into six factor scores, and the composite is their weighted
average. A missing metric is skipped, never scored as zero, and lowers the confidence label.

| Factor | Metrics |
|---|---|
| Value | earnings yield, EBIT/EV, book/price, free-cash-flow yield |
| Quality | ROE, ROIC, gross-margin stability, debt/equity, interest cover, earnings consistency |
| Growth | revenue and EPS CAGR over up to 5 years |
| Momentum | 12-1 month return, closeness to 52-week high |
| Income | NZ-resident grossed-up yield, dividend cover from earnings and from free cash flow (capped at 5x) |
| Risk | 3-year volatility, max drawdown, beta to the home index |

Financials count from their release date, or 3 months after the period ends
(`reporting_lag_months`). Backtests therefore never see results before the market did.

### sharepicker data files

`data.source: csv` reads these from `data.folder`. `data.source: yahoo` takes prices, volumes,
dividends and AUD/NZD from Yahoo's public chart API (cached in `cache_dir`) and everything else
from the CSVs. Yahoo has no financial statements, imputation or franking: without CSVs for those,
the related factors are skipped and the report says so.

| File | Columns |
|---|---|
| `listings.csv` | ticker, exchange (NZX/ASX), company_id (shared by dual listings), name, sector, currency, domicile (NZ/AU/...), security_type (company/etf/stapled/unit_trust), franking_account (0/1), all_ords_from, all_ords_to, listed_from, listed_to (fill in for delisted shares) |
| `prices.csv` | ticker, date, close (split-adjusted, not dividend-adjusted), volume. Include index tickers `^NZ50` and `^AXJO` |
| `fundamentals.csv` | company_id, period_end, available_date (optional), currency, revenue, gross_profit, ebit, net_income, equity, debt, cash, interest_expense, operating_cash_flow, capex, shares_out, dps |
| `dividends.csv` | ticker, ex_date, amount, currency, imputed_pct (NZ, 0-1), franked_pct (AU, 0-1), nz_credit_pct (AU trans-Tasman, 0-1). Leave blank if unknown |
| `fx.csv` | pair (e.g. AUDNZD = NZD per AUD), date, rate |

### Limits

- Free data is patchy for NZX small caps, and has no financial statements. For real use, supply
  financials from annual reports or a paid feed (NZX data, Morningstar, EODHD).
- Without delisted shares in the data, backtests suffer survivorship bias. The report warns about this.
- If the benchmark series are price indexes (no dividends), the backtest comparison favours the strategy.
- Tax rules are simplified for an individual NZ tax resident. Excess imputation credits, conduit
  foreign income, A-REIT and stapled-security FIF status, and the comparative value FIF method are
  not modelled. Rules live in `sharepicker/market_rules.py` with sources and dates.
- Broker fees are illustrative and change often.

### sharepicker layout

| File | What it does |
| --- | --- |
| `sharepicker/market_rules.py` | Dated NZ/AU rules: imputation, franking, withholding, FIF classification, broker fees |
| `sharepicker/data.py` | CSV, Yahoo (cached) and layered data sources |
| `sharepicker/factors.py` | Point-in-time metrics for one listing on one date |
| `sharepicker/scoring.py` | Percentiles, sector blend, presets, composite and contributions |
| `sharepicker/screen.py` | Universe filters, dual-listing merge, red and green flags |
| `sharepicker/backtest.py`, `portfolio.py` | Monthly backtest; watchlist, FIF and satellite sizing |
| `sharepicker/report.py`, `cli.py`, `sample.py` | Markdown/JSON output, command line, fictional sample data |
| `app/sharepicker.html` | Offline viewer with live re-weighting; `scripts/build_sharepicker_demo.py` refreshes its demo and `scripts/check_sharepicker_app.js` checks it matches Python |

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
