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
