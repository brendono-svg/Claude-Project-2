# Build prompt: `sharepicker`, a share screener for NZX and ASX

Paste everything below the line into a fresh Claude Code session in this repo.

---

## Goal

Build `sharepicker`, a local, private Python tool in this repo next to `kiwiwealth`. It
screens and ranks listed shares on the **NZX** (Main Board) and **ASX**, with optional
support for other venues later (Cboe Australia, NSX, NZX dual listings on other exchanges).
It writes a Markdown report that explains, for each share, why it ranked where it did.
It is a research aid for a **New Zealand-resident household**. It is not a trading bot and
it does not give personalised advice.

> Add this disclaimer to the README and to every report: general information for our own
> use, not regulated financial advice. Offering it to others as personalised advice would
> need a Financial Advice Provider licence (NZ) or an AFSL (Australia).

## Follow the house style of `kiwiwealth`

- Read `README.md`, `CLAUDE.md`, `kiwiwealth/cli.py`, `kiwiwealth/nz_rules.py` and
  `tests/` first, and match their structure, CLI style, YAML config and Markdown reports.
- Stdlib-first Python plus `pyyaml`. Add a dependency only when it pays for itself
  (for example `pandas` for price history), and say why in the commit.
- Put user data under `my-data/` (git-ignored). Never commit holdings, API keys or downloaded data.
- Put every tax and market rule in one place (`sharepicker/market_rules.py`), with a
  source comment and an "as of" date, the way `nz_rules.py` does.

## Data layer (pluggable, cached, honest about gaps)

- Define a `DataSource` interface: `universe()`, `prices(ticker, start, end)`,
  `fundamentals(ticker)`, `dividends(ticker)`. Implement:
  1. `CsvSource`: reads local CSVs in a documented format. This is the offline default and
     what tests use, with small fixture files in `tests/fixtures/`.
  2. `YahooSource`: optional, uses `.NZ` / `.AX` ticker suffixes. Wrap it so a failure
     degrades gracefully, and cache responses on disk with a timestamp.
- Every data point carries its source and as-of date. The report flags stale (older than
  N days) or missing data rather than silently filling it in.
- Store prices in the listing currency (NZD or AUD). Convert to NZD only for portfolio
  views, using a recorded FX rate and date.
- Handle dual listings (for example FPH, AIA, FBU, IFT on both exchanges): one company
  record, several listings. Score on the more liquid listing and show both.

## Screening and scoring

1. **Universe filters** (configurable in YAML): exchange, minimum market cap, minimum
   median daily traded value (NZX small caps are often illiquid), minimum years listed,
   exclude list (sectors or tickers, for ethical screens).
2. **Factor scores**, each a percentile within the filtered universe and, where it makes
   sense, within its sector:
   - Value: earnings yield, EV/EBIT, price to book, free-cash-flow yield
   - Quality: ROE / ROIC, gross margin stability, debt to equity, interest cover, earnings consistency
   - Growth: 3 to 5 year revenue and EPS growth
   - Momentum: 12-1 month return, distance from 52-week high
   - Income: **grossed-up** yield and payout sustainability
   - Risk: volatility, max drawdown, beta to the NZX 50 / ASX 200
3. **Composite score** from user-set weights, with presets (`value`, `quality`,
   `income`, `balanced`). Show each factor's contribution, so every rank can be explained.
4. Missing factors lower the confidence flag. They are never treated as zero.

## NZ / Australia rules that must be modelled correctly

- **Imputation and franking credits:** NZ imputation credits are usable by an NZ resident.
  Australian franking credits are generally **not** usable by an NZ resident, so for
  ASX shares the after-tax yield must use the cash dividend (minus 15% Australian
  non-resident withholding on any unfranked portion). Note the trans-Tasman imputation exception
  for companies that attach NZ imputation credits.
- **FIF:** NZX-listed NZ companies are outside FIF. ASX-listed **Australian-resident
  companies on the approved index that keep franking accounts** are exempt from FIF.
  NZ-resident companies are never FIFs, even when listed only on the ASX. Everything else
  (for example ETFs, stapled securities, non-Australian ASX listings) counts toward the
  NZ$50k-per-person FIF threshold. Reuse the threshold logic
  in `kiwiwealth/nz_rules.py`, don't duplicate it.
- **Brokerage and FX costs:** a configurable broker table (fee per trade, FX spread) for
  NZ platforms. Show the round-trip cost as a percentage of a typical parcel.
- **Settlement / trading calendars:** T+2 on both exchanges. Handle NZ and Australian public holidays in price history.

## Backtesting (keep it honest)

- Monthly rebalance backtest of the top-N by composite score against the NZX 50 and ASX 200
  total-return benchmarks.
- Avoid **look-ahead bias**: use only fundamentals available at each rebalance date (use
  reporting lag of 3 months by default).
- Point out **survivorship bias** in the report if the universe does not include delisted shares.
- Include brokerage, FX and tax drag. Report CAGR, volatility, max drawdown, turnover and hit rate.

## Outputs

- `python -m sharepicker screen my-data/screen.yaml -o my-data/screen.md`: ranked table
  plus a short "why" paragraph per top-N share, data-quality flags and the disclaimer.
- `python -m sharepicker backtest my-data/screen.yaml`.
- `python -m sharepicker watchlist my-data/holdings.yaml`: scores your current holdings,
  flags concentration (single share, sector, country), and FIF exposure per person.
- **Link to kiwiwealth:** respect the satellite cap set in the kiwiwealth profile, so
  that stock picks stay a small share of investable wealth. Show how much room is left.
- Optional, later: a single-file `app/sharepicker.html` viewer for the generated JSON, like
  `app/kiwiwealth.html`.

## Tests and done criteria

- pytest tests with fixture data for: each factor calculation, percentile ranking with
  ties and gaps, grossed-up yield for NZ vs ASX shares, FIF classification, dual-listing
  merge, no look-ahead in backtests, and CLI smoke tests.
- `python -m pytest -q` passes. CI (`.github/workflows`) runs the new tests.
- Run one end-to-end `YahooSource` fetch for a few tickers (for example `FPH.NZ`, `AIA.NZ`, `CBA.AX`,
  `BHP.AX`) and record the result. If the network blocks it, say so.
- README section with quick start, the config format and the limits.

## Before writing code

Propose a short plan (modules, data format, config schema) and confirm these with me:
1. Data source: free (Yahoo, best effort) or a paid feed (for example NZX data, Morningstar, EODHD)?
2. Universe: NZX Main Board + ASX 200 to start, or the full ASX?
3. Holding horizon and style: long-term buy-and-hold quality/income, or more active?
4. Ethical exclusions, if any.

Then build it in small, tested steps and commit after each one.
