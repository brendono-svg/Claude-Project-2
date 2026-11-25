from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from kiwiwealth.profile import Preferences
from sharepicker import market_rules as mr
from sharepicker.backtest import run_backtest
from sharepicker.cli import main
from sharepicker.data import CsvSource, YahooSource, add_months
from sharepicker.factors import FX, snapshot
from sharepicker.models import Dividend, Fundamentals, Listing, Series
from sharepicker.sample import generate
from sharepicker.scoring import percentiles, score
from sharepicker.screen import run_screen

ON = date(2026, 11, 20)


@pytest.fixture(scope="session")
def sample(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("sample"))


class FakeSource:
    """In-memory source for exact-number tests."""
    name = "fake"

    def __init__(self, listings, prices=None, funds=None, divs=None, fx=None):
        self._l, self._p, self._f, self._d, self._fx = listings, prices or {}, funds or {}, divs or {}, fx or {}

    def universe(self):
        return self._l

    def prices(self, t):
        return self._p.get(t, Series(t))

    def fundamentals(self, cid):
        return self._f.get(cid, [])

    def dividends(self, t):
        return self._d.get(t, [])

    def fx(self, pair):
        return self._fx.get(pair, Series(pair))


def daily(ticker, start, end, price=10.0, volume=100_000, step=0.0):
    s, d, p = Series(ticker), start, price
    while d <= end:
        if d.weekday() < 5:
            s.dates.append(d)
            s.values.append(p)
            s.volumes.append(volume)
            p *= 1 + step
        d += timedelta(days=1)
    return s


# --- scoring ---------------------------------------------------------------

def test_percentiles_average_ties_and_skip_missing():
    p = percentiles({"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0, "e": None})
    assert p["a"] == 0 and p["d"] == 100
    assert p["b"] == p["c"] == 50
    assert p["e"] is None
    assert percentiles({"x": 5.0, "y": None}) == {"x": 50.0, "y": None}


# --- FIF classification ----------------------------------------------------

@pytest.mark.parametrize("dom,st,idx,frank,expected", [
    ("NZ", "company", False, False, mr.NZ_LISTED),      # includes NZ companies listed only on the ASX
    ("AU", "company", True, True, mr.ASX_EXEMPT),
    ("AU", "company", False, True, mr.FIF),             # not in the All Ordinaries
    ("AU", "company", True, False, mr.FIF),             # no franking account
    ("AU", "stapled", True, True, mr.FIF),              # exemption not verified for stapled securities
    ("US", "company", False, False, mr.FIF),
])
def test_fif_class(dom, st, idx, frank, expected):
    assert mr.fif_class(dom, st, idx, frank)[0] == expected


def test_all_ords_membership_is_point_in_time():
    lst = Listing("X.AX", "ASX", "X", "X", domicile="AU", franking_account=True,
                  all_ords_from=date(2020, 1, 1), all_ords_to=date(2023, 6, 30))
    assert lst.in_all_ords(date(2022, 1, 1))
    assert not lst.in_all_ords(date(2019, 1, 1)) and not lst.in_all_ords(date(2024, 1, 1))


def test_nz_domiciled_asx_listing_is_nz_listed_in_screen(sample):
    res = run_screen({"filters": {"exchanges": ["ASX"]}}, CsvSource(sample), ON)
    assert res.fif["KAU.AX"][0] == mr.NZ_LISTED  # Kauri Health is NZ-resident, ASX-only screen


# --- dividend tax ------------------------------------------------------------

def test_fully_imputed_nz_dividend():
    t = mr.dividend_for_nz_resident(72.0, "NZ", imputed_pct=1.0, marginal_rate=0.33)
    assert t["nz_credits"] == pytest.approx(28.0)
    assert t["gross_nz"] == pytest.approx(100.0)
    assert t["nz_tax"] == pytest.approx(5.0)  # 33 - 28
    assert t["net"] == pytest.approx(67.0)


def test_excess_imputation_credits_are_not_refunded():
    t = mr.dividend_for_nz_resident(72.0, "NZ", imputed_pct=1.0, marginal_rate=0.175)
    assert t["nz_tax"] == 0 and t["net"] == pytest.approx(72.0)


def test_franking_does_not_help_nz_resident_but_removes_withholding():
    franked = mr.dividend_for_nz_resident(100.0, "AU", franked_pct=1.0, nz_credit_pct=0.0, marginal_rate=0.33)
    unfranked = mr.dividend_for_nz_resident(100.0, "AU", franked_pct=0.0, nz_credit_pct=0.0, marginal_rate=0.33)
    assert franked["withholding"] == 0 and franked["gross_nz"] == 100.0
    assert unfranked["withholding"] == pytest.approx(15.0)
    # 15% Australian tax is credited against NZ tax, so both end at 67 when the rate is over 15%
    assert franked["net"] == pytest.approx(67.0) and unfranked["net"] == pytest.approx(67.0)
    assert mr.australian_headline_gross(70.0, 1.0) == pytest.approx(100.0)


def test_withholding_above_nz_rate_is_a_real_cost():
    t = mr.dividend_for_nz_resident(100.0, "AU", franked_pct=0.0, nz_credit_pct=0.0, marginal_rate=0.105)
    assert t["nz_tax"] == 0 and t["net"] == pytest.approx(85.0)


def test_fif_threshold_changes_after_tax_dividend():
    kw = dict(franked_pct=0.0, nz_credit_pct=0.0, marginal_rate=0.39, is_fif=True)
    under = mr.dividend_for_nz_resident(100.0, "AU", fif_over_threshold=False, **kw)
    over = mr.dividend_for_nz_resident(100.0, "AU", fif_over_threshold=True, **kw)
    assert under["net"] == pytest.approx(61.0)
    assert over["net"] == pytest.approx(85.0) and over["note"]
    assert under["net"] != over["net"]


def test_trans_tasman_credits_and_missing_flags():
    t = mr.dividend_for_nz_resident(72.0, "AU", franked_pct=1.0, nz_credit_pct=1.0, marginal_rate=0.33)
    assert t["nz_credits"] == pytest.approx(28.0) and t["net"] == pytest.approx(67.0)
    m = mr.dividend_for_nz_resident(1.0, "AU", franked_pct=None, nz_credit_pct=None)
    assert set(m["missing"]) == {"franking", "nz_credits"}


def test_trade_costs():
    assert mr.trade_cost(1_000, "NZX", "sharesies") == 5.0
    assert mr.trade_cost(100_000, "NZX", "sharesies") == 25.0       # capped
    assert mr.trade_cost(1_000, "NZX", "asb") == 15.0               # minimum
    assert mr.trade_cost(1_000, "ASX", "sharesies") == 10.0         # 0.5% + 0.5% FX
    assert mr.round_trip_pct(1_000, "NZX", "hatch") == pytest.approx(0.006)


# --- factors -----------------------------------------------------------------

def _one_company(extra_future: bool = False):
    lst = Listing("ABC.NZ", "NZX", "ABC", "ABC Ltd", sector="Industrials")
    funds = [Fundamentals("ABC", date(y, 6, 30), date(y, 9, 30), revenue=1000 + 100 * i, gross_profit=400 + 40 * i,
                          ebit=200 + 20 * i, net_income=100 + 10 * i, equity=1000, debt=500, cash=100,
                          interest_expense=25, operating_cash_flow=150, capex=50, shares_out=100)
             for i, y in enumerate(range(2020, 2026))]
    prices = daily("ABC.NZ", date(2021, 1, 1), ON, price=20.0)
    divs = [Dividend("ABC.NZ", date(2026, 3, 1), 0.36, imputed_pct=1.0),
            Dividend("ABC.NZ", date(2026, 9, 1), 0.36, imputed_pct=1.0)]
    if extra_future:
        funds.append(Fundamentals("ABC", date(2026, 6, 30), date(2026, 12, 31), revenue=9e9, net_income=9e9,
                                  equity=1, shares_out=100))
        prices.dates.append(ON + timedelta(days=3))
        prices.values.append(999.0)
        prices.volumes.append(1.0)
        divs.append(Dividend("ABC.NZ", ON + timedelta(days=5), 50.0, imputed_pct=1.0))
    return FakeSource([lst], {"ABC.NZ": prices}, {"ABC": funds}, {"ABC.NZ": divs}), lst


def test_snapshot_numbers():
    src, lst = _one_company()
    s = snapshot(lst, src, ON, FX(src), marginal_rate=0.33)
    m = s.metrics
    assert s.price == 20.0 and s.market_cap_nzd == 2000
    assert m["earnings_yield"] == pytest.approx(150 / 2000)          # latest available: FY2025
    assert m["ebit_ev"] == pytest.approx(300 / (2000 + 500 - 100))
    assert m["roe"] == pytest.approx(0.15)
    assert m["interest_cover"] == pytest.approx(12.0)
    assert m["gross_yield_nz"] == pytest.approx(1.0 / 20)            # $0.72 cash grosses up to $1.00
    assert m["dividend_cover"] == pytest.approx(1.5 / 0.72)
    assert m["earnings_consistency"] == 1.0
    assert m["momentum_12_1"] == pytest.approx(0.0)


def test_no_look_ahead_in_snapshot():
    a_src, lst = _one_company()
    b_src, _ = _one_company(extra_future=True)
    a = snapshot(lst, a_src, ON, FX(a_src)).metrics
    b = snapshot(lst, b_src, ON, FX(b_src)).metrics
    assert a == b


def test_reporting_lag_holds_back_new_results():
    src, lst = _one_company()
    before = snapshot(lst, src, date(2025, 9, 29), FX(src)).facts["fundamentals_period"]
    after = snapshot(lst, src, date(2025, 9, 30), FX(src)).facts["fundamentals_period"]
    assert before == "2024-06-30" and after == "2025-06-30"


def test_missing_data_is_not_zero_and_lowers_confidence():
    src, lst = _one_company()
    bare = Listing("NEW.NZ", "NZX", "NEW", "New Ltd", sector="Industrials")
    src._l.append(bare)
    src._p["NEW.NZ"] = daily("NEW.NZ", date(2021, 1, 1), ON, price=5.0)
    snaps = [snapshot(x, src, ON, FX(src)) for x in src.universe()]
    ranked = {r.ticker: r for r in score(snaps)}
    assert ranked["NEW.NZ"].group_scores["value"] is None
    assert ranked["NEW.NZ"].confidence == "low" and ranked["ABC.NZ"].confidence in ("high", "medium")


# --- screen ------------------------------------------------------------------

def test_dual_listing_scored_once_on_most_liquid(sample):
    res = run_screen({}, CsvSource(sample), ON)
    tickers = [r.ticker for r in res.ranked]
    assert "KAU.NZ" in tickers and "KAU.AX" not in tickers
    assert res.other_listings["KAU.NZ"] == ["KAU.AX"]
    assert "dual listing" in res.excluded["KAU.AX"]


def test_filters_and_exclusions(sample):
    res = run_screen({"filters": {"exclude_sectors": ["Energy"], "min_market_cap_nzd": 100e6}}, CsvSource(sample), ON)
    assert "EMU.AX" in res.excluded and "MOA.NZ" in res.excluded      # sector, then too small
    assert "GUM.AX" in res.excluded                                   # stapled not screened by default
    assert "KAK.NZ" not in {r.ticker for r in res.ranked}              # delisted in 2023
    assert all(r.composite is not None for r in res.ranked)


def test_australian_franking_trap_is_flagged(sample):
    res = run_screen({}, CsvSource(sample), ON)
    au = [r for r in res.ranked if r.snap.listing.domicile == "AU" and r.snap.facts.get("dividends_counted")]
    assert au
    for r in au:
        assert r.snap.facts["headline_yield"] >= r.snap.metrics["gross_yield_nz"]
    assert any("franking credits you can't use" in t for r in au for _, t in res.flags[r.ticker])


def test_missing_imputation_is_flagged(sample):
    res = run_screen({}, CsvSource(sample), ON)
    assert any("Imputation/franking data missing" in t for _, t in res.flags["PIP.NZ"])


# --- backtest ----------------------------------------------------------------

class Truncated:
    """A source that physically holds no data after `cut`."""

    def __init__(self, src, cut):
        self.src, self.cut, self.name = src, cut, "truncated"

    def universe(self):
        return self.src.universe()

    def prices(self, t):
        return self.src.prices(t).until(self.cut)

    def fundamentals(self, cid):
        return [f for f in self.src.fundamentals(cid) if f.available_date <= self.cut]

    def dividends(self, t):
        return [d for d in self.src.dividends(t) if d.ex_date <= self.cut]

    def fx(self, pair):
        return self.src.fx(pair).until(self.cut)


def test_screen_on_past_date_matches_truncated_data(sample):
    full = CsvSource(sample)
    cut = date(2023, 5, 31)
    a = run_screen({}, full, cut)
    b = run_screen({}, Truncated(full, cut), cut)
    assert [(r.ticker, round(r.composite, 9)) for r in a.ranked] == [(r.ticker, round(r.composite, 9)) for r in b.ranked]


def test_backtest_runs_and_reports(sample):
    bt = run_backtest({"backtest": {"top_n": 5}}, CsvSource(sample), date(2023, 1, 1), ON)
    assert len(bt.returns) >= 40
    assert all(len(h) == 5 for h in bt.holdings)
    s = bt.stats()
    assert s["max_drawdown"] <= 0 and s["volatility"] > 0
    assert set(bt.bench) >= {"^NZ50", "^AXJO"}
    assert 0 <= bt.hit_rate("^NZ50") <= 1
    assert sum(bt.costs) > 0


def test_backtest_holds_delisted_share_only_while_listed(sample):
    bt = run_backtest({"backtest": {"top_n": 20}, "filters": {"min_market_cap_nzd": 0, "min_traded_value_nzd": 0}},
                      CsvSource(sample), date(2022, 1, 1), ON)
    for month, held in zip(bt.months, bt.holdings):
        if "KAK.NZ" in held:
            assert month <= add_months(date(2023, 8, 31), 1)


# --- data ----------------------------------------------------------------------

def test_yahoo_parses_chart_and_degrades_gracefully(tmp_path):
    payload = {"chart": {"result": [{"meta": {"currency": "NZD"}, "timestamp": [1763596800, 1763683200],
                                     "indicators": {"quote": [{"close": [10.0, None], "volume": [500, 0]}]},
                                     "events": {"dividends": {"1": {"date": 1763596800, "amount": 0.2}}}}]}}

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def ok(req, timeout):
        return Resp(json.dumps(payload).encode())

    y = YahooSource(tmp_path, opener=ok)
    s = y.prices("FPH.NZ")
    assert s.values == [10.0] and s.volumes == [500.0]
    d = y.dividends("FPH.NZ")
    assert d[0].amount == 0.2 and d[0].imputed_pct is None   # Yahoo has no imputation data

    def fail(req, timeout):
        raise OSError("blocked")

    y2 = YahooSource(tmp_path / "empty", opener=fail)
    assert len(y2.prices("CBA.AX")) == 0 and "CBA.AX" in y2.errors
    y3 = YahooSource(tmp_path, opener=fail, cache_days=0)   # stale cache beats nothing
    assert len(y3.prices("FPH.NZ")) == 1


def test_kiwiwealth_core_pct_shared():
    assert Preferences().core_pct == 0.85
    assert Preferences(preferred_vehicles=["managed_funds"]).core_pct == 1.0


# --- CLI -----------------------------------------------------------------------

def test_cli_end_to_end(sample, tmp_path, capsys):
    cfg = tmp_path / "screen.yaml"
    cfg.write_text(f"data: {{source: csv, folder: '{sample}'}}\nas_of: 2026-11-20\ntop_n: 5\n")
    md, js = tmp_path / "s.md", tmp_path / "s.json"
    assert main(["screen", str(cfg), "-o", str(md), "--json", str(js)]) == 0
    text = md.read_text()
    assert "## Top 5" in text and "not regulated financial advice" in text
    data = json.loads(js.read_text())
    assert data["shares"][0]["rank"] == 1 and "why" in data["shares"][0]
    assert main(["screen", str(cfg), "--preset", "income", "-o", str(tmp_path / "i.md")]) == 0
    assert main(["explain", str(cfg), "KAU.AX"]) == 0
    assert "more liquid listing" in capsys.readouterr().out
    assert main(["backtest", str(cfg), "--start", "2024-01-01", "-o", str(tmp_path / "b.md")]) == 0
    assert "CAGR" in (tmp_path / "b.md").read_text()

    hold = tmp_path / "holdings.yaml"
    hold.write_text("screen: screen.yaml\nholdings:\n  - {ticker: KAU.NZ, owner: A, units: 100, cost: 1000}\n"
                    "  - {ticker: GUM.AX, owner: A, units: 100, cost: 600}\n")
    assert main(["watchlist", str(hold), "-o", str(tmp_path / "w.md")]) == 0
    w = (tmp_path / "w.md").read_text()
    assert "FIF" in w and "KAU.NZ" in w


def test_watchlist_after_tax_dividends_depend_on_owner(sample):
    from kiwiwealth.profile import Person, Profile
    from sharepicker.portfolio import watch
    src = CsvSource(sample)
    res = run_screen({}, src, ON)
    prof = Profile("Test", [Person("Lo", 40, salary=40_000), Person("Hi", 40, salary=250_000)])
    hold = [{"ticker": "BOO.AX", "owner": "Lo", "units": 1000, "cost": 10_000},
            {"ticker": "BOO.AX", "owner": "Hi", "units": 1000, "cost": 10_000}]
    w = watch(hold, res, src, ON, prof)
    lo, hi = w.positions
    assert lo.dividends_nzd == pytest.approx(hi.dividends_nzd) and lo.dividends_nzd > 0
    assert lo.dividends_after_tax_nzd > hi.dividends_after_tax_nzd


def test_watchlist_owner_names_and_unknown_owners(sample):
    from kiwiwealth.profile import Person, Profile
    from sharepicker.portfolio import watch
    src = CsvSource(sample)
    res = run_screen({}, src, ON)
    prof = Profile("Test", [Person("Alex", 40, salary=90_000), Person("Sam", 40, salary=60_000)])
    w = watch([{"ticker": "GUM.AX", "owner": "alex", "units": 100, "cost": 6_000}], res, src, ON, prof)
    assert w.positions[0].owner == "Alex" and w.fif["Alex"]["cost"] == 6_000 and w.fif["Sam"]["cost"] == 0
    w2 = watch([{"ticker": "GUM.AX", "owner": "Alix", "units": 100, "cost": 6_000}], res, src, ON, prof)
    assert any("isn't a person" in a for a in w2.alerts)
    assert w2.fif["Alex"]["cost"] == 6_000 and w2.fif["Sam"]["cost"] == 6_000


def test_idea_parcels_respect_sector_cap(sample):
    from sharepicker.portfolio import watch
    src = CsvSource(sample)
    res = run_screen({}, src, ON)
    hold = [{"ticker": t, "owner": "A", "units": 300, "cost": 3_000} for t in ("PUR.NZ", "BOO.AX", "WTL.AX", "EMU.AX")]
    w = watch(hold, res, src, ON, max_sector_pct=0.35, n_ideas=8)
    sectors, total = dict(w.by_sector), w.total_value
    for i in w.ideas:
        sectors[i["sector"]] = sectors.get(i["sector"], 0) + i["parcel_nzd"]
        total += i["parcel_nzd"]
        assert sectors[i["sector"]] / total <= 0.35 + 1e-3


def test_benchmark_returns_line_up_by_month(sample):
    bt = run_backtest({"backtest": {"top_n": 5}}, CsvSource(sample), date(2024, 1, 1), ON)
    for v in bt.bench.values():
        assert len(v) == len(bt.returns)


# --- research layer ----------------------------------------------------------------

from sharepicker import research as rsch  # noqa: E402


@pytest.mark.parametrize("headline,cat", [
    ("Half-year result for the period ended 31 December 2025", "results"),
    ("Appendix 4E and annual report", "results"),
    ("Trading update: FY27 earnings guidance lowered on weaker demand", "guidance_down"),
    ("Company raises full-year earnings guidance", "guidance_up"),
    ("Fully underwritten placement and share purchase plan", "capital_raise"),
    ("Trading halt", "trading_halt"),
    ("Receipt of non-binding indicative proposal", "takeover"),
    ("Ongoing disclosure notice: director's interest", "director_trade"),
    ("Quarterly activities report and Appendix 5B", "quarterly"),
    ("CEO resignation", "leadership"),
    ("Notice of annual general meeting", "agm"),
    ("Change of registry address", "other"),
    ("Results of Annual Meeting", "agm"),
    ("Notice of FY26 results date", "results_notice"),
    ("Results briefing webcast details", "results_notice"),
])
def test_classify_announcements(headline, cat):
    assert rsch.classify(headline)[0] == cat


def test_results_deadlines():
    assert mr.results_deadline(date(2026, 6, 30), "NZX") == date(2026, 8, 29)   # 60 days
    assert mr.results_deadline(date(2026, 6, 30), "ASX") == date(2026, 8, 31)   # 2 months, month end
    assert mr.results_deadline(date(2026, 12, 31), "ASX") == date(2027, 2, 28)
    assert mr.results_deadline(date(2026, 9, 30), "ASX", quarterly=True) == date(2026, 10, 31)


def test_next_results_falls_back_to_deadline(tmp_path):
    store = rsch.ResearchStore(tmp_path)        # empty: no events, no announcements
    funds = [Fundamentals("X", date(2025, 6, 30), date(2025, 9, 30)), Fundamentals("X", date(2026, 6, 30), date(2026, 9, 30))]
    for ex, expected in (("NZX", date(2026, 8, 29)), ("ASX", date(2026, 8, 31))):
        nr = rsch.next_results(Listing("X", ex, "X", "X"), funds, store, date(2026, 7, 15))
        assert nr["date"] == expected and nr["basis"].startswith("listing-rule deadline") and nr["kind"] == "full-year"
    nr = rsch.next_results(Listing("X", "NZX", "X", "X"), funds, store, date(2026, 9, 10))
    assert nr["date"] == date(2027, 3, 1) and nr["kind"] == "half-year"   # 31 Dec + 60 days


def test_next_results_uses_confirmed_then_history(sample):
    store = rsch.ResearchStore(sample)
    src = CsvSource(sample)
    kau = next(x for x in src.universe() if x.ticker == "KAU.NZ")
    nr = rsch.next_results(kau, src.fundamentals("KAU"), store, ON)
    assert nr["date"] == date(2026, 11, 27) and nr["basis"] == "confirmed"
    koa = next(x for x in src.universe() if x.ticker == "KOA.AX")
    nr = rsch.next_results(koa, src.fundamentals("KOA"), store, ON)
    assert nr["basis"].startswith("estimated") and date(2027, 2, 1) < nr["date"] <= nr["deadline"]


def test_rules_parse_check_and_reject_typos():
    assert rsch.parse_rule("roe < 10%") == ("roe", "<", 0.10)
    ctx = {"metrics": {"roe": 0.08, "dividend_cover": None}, "top": {"rank": 3.0}}
    assert rsch.check_rule("roe < 10%", ctx) == ("broken", 0.08)
    assert rsch.check_rule("rank > 5", ctx)[0] == "ok"
    assert rsch.check_rule("dividend_cover < 1.2", ctx)[0] == "unknown"
    with pytest.raises(rsch.RuleError):
        rsch.parse_rule("dividnd_cover < 1")
    with pytest.raises(rsch.RuleError):
        rsch.parse_rule("__import__('os') < 1")


def test_journal_typo_fails_loudly(tmp_path):
    (tmp_path / "journal.yaml").write_text("shares:\n  ABC.NZ:\n    break_if: ['roe << 5']\n")
    with pytest.raises(rsch.RuleError):
        rsch.ResearchStore(tmp_path)


def test_price_reaction_is_relative_to_index():
    stock = daily("S", date(2026, 1, 5), date(2026, 1, 16), price=10.0)
    index = daily("I", date(2026, 1, 5), date(2026, 1, 16), price=100.0)
    i = stock.dates.index(date(2026, 1, 12))
    for k in range(i, len(stock.values)):
        stock.values[k] = 11.0          # +10% on the announcement day
    for k in range(i + 1, len(index.values)):
        index.values[k] = 102.0         # index +2% the day after
    assert rsch.price_reaction(stock, index, date(2026, 1, 12)) == pytest.approx(0.08)
    assert rsch.price_reaction(stock, index, date(2026, 1, 16)) is None   # no day after yet


def test_sample_story_reactions_and_checklist(sample):
    from sharepicker.brief import Context, announcements_with_reactions, research_for
    src = CsvSource(sample)
    res = run_screen({}, src, ON)
    store = rsch.ResearchStore(sample)
    ctx = Context(src, store, {"NZX": src.prices("^NZ50"), "ASX": src.prices("^AXJO")})
    anns = announcements_with_reactions(ctx, "KOA.AX", "ASX", ON)
    cut = next(a for a in anns if a.category == "guidance_down")
    assert cut.reaction < -0.05 and cut.relevance == 1.0
    koa = next(r for r in res.ranked if r.ticker == "KOA.AX")
    lenses = research_for(koa, res, ctx)["lenses"]
    assert lenses["thesis"].startswith("BROKEN") and "short_pct > 5%" in lenses["thesis"]
    assert "high" in lenses["short_interest"]
    pur = next(r for r in res.ranked if r.ticker == "PUR.NZ")
    assert research_for(pur, res, ctx)["lenses"]["thesis"].startswith("review due")


def test_history_memory_and_persistent_flags(tmp_path):
    hist = [{"as_of": f"2026-0{m}-28", "shares": {"A": {"rank": m, "composite": 50, "red": ["Low interest cover (2.1x): x"]}}}
            for m in (6, 7, 8)]
    mem = rsch.memory_for("A", hist)
    assert [p[1] for p in mem["path"]] == [6, 7, 8]
    assert mem["persistent_flags"] == {"Low interest cover": 3}
    hist[1]["shares"]["A"]["red"] = []
    assert rsch.memory_for("A", hist)["persistent_flags"] == {}


def test_cli_research_commands(sample, tmp_path):
    hist = tmp_path / "hist"
    cfg = tmp_path / "screen.yaml"
    cfg.write_text(f"data: {{source: csv, folder: '{sample}'}}\nresearch: {{history_dir: '{hist}'}}\n")
    assert main(["screen", str(cfg), "--as-of", "2026-10-30", "--save", "-o", str(tmp_path / "s.md")]) == 0
    assert (hist / "screen-2026-10-30.json").exists()
    out = tmp_path / "d.md"
    assert main(["digest", str(cfg), "--as-of", "2026-11-20", "-o", str(out)]) == 0
    d = out.read_text()
    assert "Compared with the screen saved for **2026-10-30**" in d
    assert "ECH.AX** Takeover" in d and "KAU.NZ** Kauri Health (confirmed)" in d
    assert "KOA.AX**: BROKEN" in d
    b = tmp_path / "b.md"
    assert main(["brief", str(cfg), "KAU.AX", "--for-claude", "--as-of", "2026-11-20", "-o", str(b)]) == 0
    t = b.read_text()
    assert "Kauri Health (KAU.NZ)" in t and "Prompt to paste into Claude" in t and "nzx.com" in t
    js = tmp_path / "s.json"
    assert main(["screen", str(cfg), "--as-of", "2026-11-20", "--json", str(js), "-o", str(tmp_path / "x.md")]) == 0
    sh = {s["ticker"]: s for s in json.loads(js.read_text())["shares"]}
    assert sh["KOA.AX"]["research"]["lenses"]["thesis"].startswith("BROKEN")
    assert sh["KAU.NZ"]["research"]["next_results"]["date"] == "2026-11-27"


def test_next_results_from_balance_date_only(tmp_path):
    store = rsch.ResearchStore(tmp_path)
    lst = Listing("X.NZ", "NZX", "X", "X", balance_date="03-31")
    nr = rsch.next_results(lst, [], store, date(2026, 11, 20))
    assert nr["date"] == date(2026, 11, 29) and nr["kind"] == "half-year"   # 30 Sep + 60 days


def test_cli_note_and_announce(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"data: {{folder: '{folder}'}}\n")
    assert main(["note", str(cfg), "fph.nz", "Margins", "recovering"]) == 0
    notes = list((folder / "research" / "FPH.NZ").glob("*.md"))
    assert notes and "Margins recovering" in notes[0].read_text()
    assert main(["announce", str(cfg), "FPH.NZ", "2026-11-25", "Half-year result", "--sensitive"]) == 0
    assert main(["announce", str(cfg), "FPH.NZ", "2026-11-26", "Investor day presentation"]) == 0
    a = rsch.ResearchStore(folder).announcements("FPH.NZ")
    assert [x.category for x in a] == ["results", "guidance"] and a[0].price_sensitive is True


def test_relevance_is_stable_across_calls(sample):
    from sharepicker.brief import Context, announcements_with_reactions
    src = CsvSource(sample)
    ctx = Context(src, rsch.ResearchStore(sample), {"ASX": src.prices("^AXJO")})
    a = [x.relevance for x in announcements_with_reactions(ctx, "KOA.AX", "ASX", ON)]
    b = [x.relevance for x in announcements_with_reactions(ctx, "KOA.AX", "ASX", ON)]
    assert a == b
    assert all(x.reaction is None for x in ctx.store.announcements("KOA.AX"))   # store untouched


def test_results_date_notice_does_not_hide_upcoming_release(tmp_path):
    (tmp_path / "announcements.csv").write_text(
        "date,ticker,headline\n2026-07-15,X.NZ,Notice of FY26 results date\n2026-07-16,X.NZ,Full-year results date confirmed\n")
    store = rsch.ResearchStore(tmp_path)
    nr = rsch.next_results(Listing("X.NZ", "NZX", "X", "X", balance_date="06-30"), [], store, date(2026, 7, 20))
    assert nr["date"] == date(2026, 8, 29)


def test_mdhtml_escapes_and_renders():
    from sharepicker.mdhtml import to_html
    h = to_html("# T <x>\n\n- **a** `b` [l](https://e.nz)\n- [bad](javascript:alert(1))\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```text\n<p>\n```\n> q")
    assert "<h1>T &lt;x&gt;</h1>" in h and "<strong>a</strong>" in h and "<code>b</code>" in h
    assert 'href="https://e.nz"' in h and "javascript" not in h
    assert "<td>1</td>" in h and "&lt;p&gt;" in h and "<blockquote>q</blockquote>" in h


def test_screen_json_with_briefs(sample, tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"data: {{folder: '{sample}'}}\nresearch: {{history_dir: '{tmp_path / 'h'}'}}\n")
    js = tmp_path / "s.json"
    assert main(["screen", str(cfg), "--as-of", "2026-11-20", "--json", str(js), "--with-briefs", "-o", str(tmp_path / "x.md")]) == 0
    d = json.loads(js.read_text())
    assert "KOA.AX" in d["briefs"] and "I'm a New Zealand-resident" in d["briefs"]["KOA.AX"]["prompt"]
    assert "<h1>" in d["digest_html"] and "not regulated financial advice" not in d["briefs"]["KOA.AX"]["html"]


def test_yahoo_fundamentals_parse_and_cache(tmp_path):
    payload = {"timeseries": {"result": [
        {"meta": {"type": ["annualNetIncome"]}, "annualNetIncome": [
            {"asOfDate": "2025-06-30", "currencyCode": "AUD", "reportedValue": {"raw": 100.0}}, None]},
        {"meta": {"type": ["annualCapitalExpenditure"]}, "annualCapitalExpenditure": [
            {"asOfDate": "2025-06-30", "currencyCode": "AUD", "reportedValue": {"raw": -20.0}}]},
        {"meta": {"type": ["annualOrdinarySharesNumber"]}, "annualOrdinarySharesNumber": [
            {"asOfDate": "2025-06-30", "currencyCode": "AUD", "reportedValue": {"raw": 50.0}}]}]}}

    class Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    y = YahooSource(tmp_path, opener=lambda req, timeout: Resp())
    y.symbols["CBA"] = "CBA.AX"
    f = y.fundamentals("CBA")
    assert len(f) == 1 and f[0].net_income == 100 and f[0].capex == 20 and f[0].currency == "AUD"
    assert f[0].available_date == date(2025, 9, 30) and "restated" in f[0].source
    assert y.fundamentals("UNKNOWN") == []


def test_financials_skip_debt_metrics_without_losing_confidence():
    src, lst = _one_company()
    lst.sector = "Financials"
    s = snapshot(lst, src, ON, FX(src))
    assert s.metrics["debt_to_equity"] is None and s.metrics["interest_cover"] is None
    assert s.metrics["roe"] is not None and "debt_to_equity" in s.not_applicable
    assert score([s])[0].confidence == "high"


def test_backtest_drops_restated_statements(sample):
    from sharepicker.backtest import _AsReported
    src = _AsReported(CsvSource(sample))
    assert src.fundamentals("KAU") and not src.dropped     # CSV statements are kept


# --- feeds and detail ----------------------------------------------------------------

from sharepicker import feeds  # noqa: E402


class _FakeOpen:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        for key, body in self.routes.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return _Body(body)
        raise OSError("no route " + url)


class _Body:
    def __init__(self, b):
        self.b = b if isinstance(b, bytes) else b.encode()

    def read(self):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


NZX_PAGE = ('<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {
    "dehydratedState": {"queries": [{"queryKey": ["announcements", "recent", "NZSX"], "state": {"data": {"data": [
        {"summary": {"id": 11, "title": "Half Year Results", "type": "MKTUPDTE", "companyCode": "FPH",
                     "publicationDate": "2026-11-20T08:30:00+13:00"},
         "details": {"releaseDate": "2026-11-20T08:30:00+13:00", "isPriceSensitive": True}},
        {"summary": {"id": 12, "title": "Other company", "companyCode": "ZZZ", "publicationDate": "2026-11-20"},
         "details": {}}]}}}]}}}}) + '</script></html>')
ASX_JSON = json.dumps({"data": {"items": [{"date": "2026-11-19T22:50:52.000Z", "documentKey": "k1",
                                           "headline": "Appendix 3Y - J Smith", "announcementType": "SECURITY HOLDER DETAILS",
                                           "isPriceSensitive": False}]}})
RSS = """<rss><channel><item><title>FPH tipped to have strong result - NZ Herald</title><link>https://n.z/1</link>
<guid>g1</guid><pubDate>Mon, 23 Nov 2026 01:00:00 GMT</pubDate><source url="https://nzherald.co.nz">NZ Herald</source></item>
<item><title>Old story - X</title><link>https://n.z/2</link><guid>g2</guid><pubDate>Mon, 01 Jan 2024 01:00:00 GMT</pubDate></item>
</channel></rss>"""
ASIC_INDEX = json.dumps([{"date": 20261119, "version": "001"}, {"date": 20261020, "version": "001"}])
ASIC_CSV = "Product,Product Code,Reported Short Positions,Total Product in Issue,%\nCOMMONWEALTH BANK,CBA,1,2,2.765\n"


def _listings():
    return [Listing("FPH.NZ", "NZX", "FPH", "Fisher & Paykel Healthcare"),
            Listing("CBA.AX", "ASX", "CBA", "Commonwealth Bank", domicile="AU")]


def test_feeds_parse_and_merge(tmp_path):
    opener = _FakeOpen({"nzx.com": NZX_PAGE, "markitdigital": ASX_JSON, "news.google": RSS,
                        "short-selling-data.json": ASIC_INDEX, "SSDailyAggShortPos": ASIC_CSV,
                        "finance/search": json.dumps({"quotes": [{"symbol": "CBA.AX", "longname": "Commonwealth Bank of Australia",
                                                                  "industry": "Banks"}]})})
    f = feeds.Fetcher(tmp_path, delay=0, opener=opener)
    a = feeds.collect(_listings(), f, None, log=lambda m: None)
    ids = {r["id"] for r in a["announcements"]}
    assert ids == {"NZX:11", "ASX:k1"}
    nzx = next(r for r in a["announcements"] if r["id"] == "NZX:11")
    assert nzx["ticker"] == "FPH.NZ" and nzx["price_sensitive"] and nzx["date"] == "2026-11-20"
    assert {r["headline"] for r in a["news"]} == {"FPH tipped to have strong result"}   # old item dropped, publisher stripped
    assert {(r["date"], r["short_pct"]) for r in a["shorts"]} == {("2026-11-19", 0.02765), ("2026-10-20", 0.02765)}
    assert a["profiles"]["CBA.AX"]["industry"] == "Banks"
    # Second run with every feed failing keeps everything and records the failures.
    g = feeds.Fetcher(tmp_path / "c2", delay=0, opener=_FakeOpen({}))
    b = feeds.collect(_listings(), g, a, log=lambda m: None)
    for k in ("announcements", "news", "shorts"):
        assert len(b[k]) == len(a[k]) and len({r["id"] for r in b[k]}) == len(b[k])
    assert any(c["failed"] for c in b["coverage"])


def test_nzx_layout_change_is_reported(tmp_path):
    f = feeds.Fetcher(tmp_path, delay=0, opener=_FakeOpen({"nzx.com": "<html>new design</html>"}))
    assert feeds.nzx_announcements(f, _listings()) is None and "nzx" in f.errors


def test_archive_drops_only_old_items():
    old = {"announcements": [{"id": "A", "date": "2020-01-01"}, {"id": "B", "date": "2026-01-01"}]}
    m = feeds.merge(old, {"announcements": [{"id": "B", "date": "2026-01-01", "headline": "new"}]}, date(2026, 11, 25))
    assert [r["id"] for r in m["announcements"]] == ["B"] and m["announcements"][0]["headline"] == "new"


def test_store_reads_archive_and_merges_dual_listings(tmp_path):
    store = rsch.ResearchStore(tmp_path)
    store.add_archive({"announcements": [
        {"id": "NZX:1", "date": "2026-11-20", "ticker": "FPH.NZ", "headline": "Half Year Results", "source": "NZX"},
        {"id": "ASX:1", "date": "2026-11-20", "ticker": "FPH.AX", "headline": "Half year results", "source": "ASX"}],
        "news": [{"id": "N1", "date": "2026-11-21", "ticker": "FPH.NZ", "headline": "Broker lifts price target on FPH",
                  "publisher": "X"}],
        "shorts": [{"id": "s", "date": "2026-11-19", "ticker": "CBA.AX", "short_pct": 0.03},
                   {"id": "t", "date": "2026-10-20", "ticker": "CBA.AX", "short_pct": 0.02}]})
    store.aliases = {"FPH.NZ": ["FPH.NZ", "FPH.AX"], "FPH.AX": ["FPH.NZ", "FPH.AX"]}
    assert len(store.announcements("FPH.NZ")) == 1                         # re-post merged
    assert store.news("FPH.AX")[0].category == "broker_rating"
    assert store.short_trend("CBA.AX", date(2026, 11, 20)) == (0.03, 0.02)


def test_f_score_and_detail_tables(sample):
    from sharepicker import detail
    src = CsvSource(sample)
    res = run_screen({}, src, ON)
    kau = next(r for r in res.ranked if r.ticker == "KAU.NZ")
    assert kau.snap.metrics["f_score"] is not None and "/" in kau.snap.facts["f_score_text"]
    boo = next(r for r in res.ranked if r.ticker == "BOO.AX")                  # a bank: fewer tests
    tests = dict(boo.snap.facts["f_score_tests"])
    assert tests["Current ratio improved"] is None and boo.snap.metrics["net_debt_ebitda"] is None
    assert detail.financials_table(src.fundamentals("KAU"), ON)[0].startswith("| NZD |")
    perf = detail.performance_table(src.prices("KAU.NZ"), src.prices("^NZ50"), ON)
    assert any(r.startswith("| 1 year |") for r in perf)
    divs = detail.dividend_history(src.dividends("KAU.NZ"), ON, "NZD")
    assert "Paid in each of the last" in divs[-1]


def test_nzx_company_page_gives_imputation_and_people(tmp_path):
    page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"dehydratedState": {"queries": [
        {"queryKey": ["instrument", "pastDividends", "X"], "state": {"data": [
            {"id": 1, "type": "INT", "recordDate": 1701774000, "amount": "18.0", "currencyCode": "NZD",
             "imputationCreditAmount": "0.07"},
            {"id": 2, "type": "FIN", "recordDate": 1719403200, "amount": "20.0", "imputationCreditAmount": "0"},
            {"id": 3, "type": "SPC", "amount": "5.0"}]}},
        {"queryKey": ["company", "summary", "FPH000000"], "state": {"data": {"financialYearEnd": {"month": 3, "day": 31},
                                                                          "firstListedDate": "1979-11-01T00:00:00+13:00"}}},
        {"queryKey": ["company", "directors", "FPH000000"], "state": {"data": [
            {"firstName": "A", "lastName": "B", "role": "Chair", "isCurrent": True},
            {"firstName": "C", "lastName": "D", "role": "Director", "isCurrent": False}]}}]}}}}) + "</script>")
    f = feeds.Fetcher(tmp_path, delay=0, opener=_FakeOpen({"instruments/FPH": page}))
    c = feeds.nzx_company(f, Listing("FPH.NZ", "NZX", "FPH", "F"))
    assert [d["imputed_pct"] for d in c["dividends"]] == [1.0, 0.0]          # 7c on 18c = fully imputed; undated row skipped
    assert c["dividends"][0]["date"] == "2023-12-06"                           # NZ date, not UTC
    assert c["year_end"] == "03-31" and c["people"] == [{"name": "A B", "role": "Chair"}]
