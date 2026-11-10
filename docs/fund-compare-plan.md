# NZ fund comparison app: plan and brainstorm

Status: plan agreed in outline, nothing built. Facts marked **(verify)** were not
confirmed against a primary source and belong in the Phase 0 spike.

Decided: personal use only; KiwiSaver first; lives in this repo as `fundcompare/`
beside `kiwiwealth/`.

## 1. Goal

Compare NZ managed investment scheme (MIS) and KiwiSaver **funds** (not just schemes):

- Top five funds per category (defensive, conservative, balanced, growth, aggressive,
  plus single-sector) over 1-year and 5-year horizons.
- Weigh that against fees, passive vs active approach, investment philosophy and the
  underlying wholesale managers.
- Add credibility signals: time in operation, funds under management (FUM), investor numbers.
- Layer on third-party views (Canstar, Morningstar and similar).

## 2. Which register

The fund data lives on the **Disclose Register** (Companies Office), not the Financial
Service Providers Register (FSPR). The FSPR only tells us whether a provider is registered
and since when, which is a minor credibility input.

The Disclose Register was replaced on 23 March 2026. The old SOAP public APIs were retired
and a new REST API is published on api.business.govt.nz. Older scraping guides and CSV
links found online are probably stale.

## 3. Data sources

| Source | What it gives | Access | Notes |
| --- | --- | --- | --- |
| Disclose REST API | Offers, schemes, funds, fund filings, historic versions | API key from api.business.govt.nz **(verify sign-up terms and rate limits)** | Primary source |
| Quarterly fund updates (on Disclose) | Returns, fees, mix, top 10 holdings, FUM, investors, start date, risk indicator | Structured data via API, PDF as fallback | Filed for 31 Mar, 30 Jun, 30 Sep, 31 Dec |
| Full portfolio holdings (on Disclose) | Every holding per fund | File per fund **(verify format and frequency)** | Best evidence for underlying managers and passive/active |
| PDS, SIPO, other material information (on Disclose) | Philosophy, benchmarks, target ranges, named underlying managers | PDFs | Needs LLM extraction |
| FMA archived quarterly sheets | Consolidated fund update data up to 31 Dec 2022 | Excel download | FMA has stopped publishing these; useful for history backfill |
| FMA KiwiSaver annual report | Scheme-level fees, members, FUM | PDF and tables | Cross-check only |
| Sorted Smart Investor | Same Disclose data, already categorised | Website | Use for spot-check testing, not as a source |
| Canstar, Morningstar KiwiSaver survey, Consumer NZ, Mindful Money | Ratings, awards, ethical screens | Websites and PDFs | Copyright and terms of use need a human check before any automated capture |
| FSPR, FMA licence list | Provider registration date, licence status | Website | Small credibility input |

Fields expected in each fund update (from the regulated template, **verify against the API**):
fund total value, number of investors, fund start date, risk indicator (1 to 7),
past-year return after fees (before tax and after tax at 28% PIR), five-year average
annual return, year-by-year annual returns (up to 10 years), market index return,
total annual fund charges split into manager's basic fee, performance fee and other
charges, fixed dollar fees, actual and target investment mix by asset class, top 10
investments, currency hedging, key personnel.

## 4. Method decisions to settle

**Category.** Do not trust the fund name. Derive category from growth-asset share using
the Sorted bands: defensive 0-9.9%, conservative 10-34.9%, balanced 35-62.9%, growth
63-89.9%, aggressive 90-100%. Decide whether to use target mix (stable) or actual mix
(drifts); recommendation is target, with a flag when actual sits in a different band.
Single-sector funds (NZ shares, global shares, property, bonds, cash) get their own
categories. Life-stage and default KiwiSaver funds get flags.

**Returns.** Fund updates only give 1-year and 5-year average returns plus annual
returns by year. Anything else (3-year, 10-year, rolling periods) has to be derived
from the annual series or from stored quarterly snapshots. Pick one tax basis and
stick to it; recommendation is after fees, before tax, with after-tax at 28% as a
secondary column.

**History.** Each quarter is a snapshot, so the app must store every quarter from day
one. Backfill options: API historic versions, and the FMA sheets to Dec 2022. There
may be a gap between Dec 2022 and whatever the API exposes **(verify)**.

**Survivorship bias.** Closed and merged funds must stay in the database. A top-five
list that silently drops dead funds flatters the survivors.

**Eligibility for the top five.** Suggested gates: a full track record for the horizon
(no 5-year ranking for a 3-year-old fund), a minimum FUM (say $10m), open to new
investors. Show excluded funds in a separate list with the reason.

**Fees.** Percentage charges alone mislead on small balances. Compute total cost in
dollars at reference balances ($10k, $50k, $100k, $250k) including fixed member fees.
Show performance fees separately. Buy/sell spreads come from the PDS, not the fund update.

**Risk-adjusted view.** Returns ranked within category, return minus the fund's own
market index, risk indicator, and consistency (share of years in the top quartile of
its category). A fund that tops a 1-year table by taking more risk than its category
peers should be visible as such.

**Passive vs active.** No register field exists for this. Classify from evidence:
SIPO wording, tracking difference against the stated index, fee level, number of
holdings, and index-fund names in the holdings. Output is passive, active or blended
(factor, enhanced index, mixed), with a confidence level and a quoted source line.

**Underlying managers.** Extract from full holdings, top 10 investments, SIPO and other
material information. Needs a manager table with alias matching (one wholesale manager
appears under many spellings).

**Scoring.** Recommendation: no single hidden composite score. Show the raw top five by
return, then a scorecard beside it (fees, risk, consistency, credibility, approach,
external ratings) with weights the user can adjust.

**Legal.** Keep the existing kiwiwealth stance: general information for our own use.
Publishing rankings to other people raises financial advice and copyright questions
that need a human decision.

## 5. Architecture sketch

New Python package `fundcompare/` beside `kiwiwealth/`, same conventions (pytest, dated
sources, source link on every figure).

- **Raw layer:** API responses and PDFs stored unchanged, with fetch date and hash,
  in a git-ignored data folder. The API key is read from an environment variable only.
- **Fixtures:** recorded raw responses for 2-3 KiwiSaver schemes, key stripped, are
  committed under `tests/fundcompare/`. Build and test models work offline against
  these and do not call the live API.
- **Normalised layer:** SQLite. Tables: `provider`, `scheme`, `fund`, `fund_update`
  (one row per fund per quarter), `asset_mix`, `holding`, `fee`, `document`,
  `manager`, `fund_manager_link`, `extraction` (LLM output with quote and page
  reference), `external_rating`.
- **Derived layer:** category, peer ranks, dollar fees, flags. Rebuilt from the
  normalised layer, never hand-edited.
- **Output:** start with a Markdown or single-page HTML report (matches the existing
  app pattern); interactive filters later.
- **Refresh:** quarterly job roughly a month after each quarter end, once fund updates
  are filed **(verify the filing deadline)**.
- **Later:** kiwiwealth reads a dated data file exported by fundcompare (fees, category).
  Neither package imports the other, so the kiwiwealth parity checks are unaffected.

## 6. Who does what

| Work | Model | Why |
| --- | --- | --- |
| Schema, ingest pipeline, metrics, ranking, UI | Opus (build) | Design-heavy, needs consistency across modules |
| SIPO and PDS extraction to a fixed JSON schema, one provider per agent | Sonnet agents | Parallel, bounded, each returns quotes and page numbers |
| External ratings capture (after terms check) | Sonnet agents | Small per-site tasks |
| Manager alias matching | Sonnet proposes, human approves ambiguous cases | Errors here corrupt every downstream view |
| Schema validation, mix sums to 100%, outlier checks, spot-checks against Smart Investor | Haiku subagents | Cheap, repeatable, read-only |
| Checkpoint review | Opus advisor via `/consult` | Per CLAUDE.md |

Rules for capture agents: write only to the raw layer and the `extraction` table, never
to derived tables; every extracted claim carries a quote and a document reference;
"not stated" is a valid answer and guessing is not.

## 7. Phases

0. **Spike.** Get an API key and pull one KiwiSaver scheme end to end. Exit criteria,
   written back into this document:
   - endpoints used, and a field map against the list in section 3;
   - history depth, rate limits and sign-up terms;
   - every **(verify)** resolved;
   - one fund's values checked by hand against its published fund update PDF;
   - fixtures for 2-3 schemes committed;
   - a decision: **structured** (API returns fund update fields), **PDF fallback**
     (API returns documents only, so Phases 1-3 parse the regulated fund update
     template instead), or **stop**.
1. **MVP.** Ingest all KiwiSaver funds for the latest quarter, categorise, top five by
   1-year and 5-year return per category, with the track-record, minimum FUM and
   open-to-new-investors gates applied. Done when `python -m pytest -q tests/fundcompare`
   passes with the network off.
2. **Fees and credibility.** Dollar fees, fund age, investor numbers, scorecard.
3. **All MIS funds and history.** Extend beyond KiwiSaver, backfill past quarters,
   keep closed funds.
4. **Approach and managers.** SIPO extraction, passive/active classification, manager table.
5. **External ratings.** Entered by hand with a source link and date (personal use,
   no automated capture).
6. **UI and scheduled refresh.**
7. **kiwiwealth integration.**

## 8. Risks

- New register API may not expose full fund update history, or may limit bulk pulls.
- Ratings sites may prohibit automated capture or republication.
- LLM extraction from SIPOs will be uneven; providers describe the same thing very differently.
- Fund updates lag by one to two months, and 1-year rankings are noisy. The app should
  not present a 1-year top five as a recommendation.
- Multi-fund structures (a KiwiSaver fund investing in the provider's own wholesale
  fund, which then uses external managers) hide the real manager two layers down.

## 9. Open questions

1. Output: report, phone page like kiwiwealth, or web app?
2. Which tax basis should be the headline return?
