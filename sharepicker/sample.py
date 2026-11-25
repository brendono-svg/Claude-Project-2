"""A made-up NZX/ASX universe for trying the tool and for tests.

Every company here is FICTIONAL and every number is generated. The generator builds
in the awkward cases the real markets have: a dual-listed NZ company, a delisted
share, an illiquid NZX small cap, an Australian stapled trust, an Australian company
outside the All Ordinaries, one paying trans-Tasman NZ imputation credits, and
dividends with missing imputation data.
"""
from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

# ticker, exchange, company_id, name, sector, currency, domicile, type, franking, all_ords, listed_from, listed_to
# plus generator knobs: quality (0-1), growth, payout, base mcap (m, listing ccy), liquidity (turnover/day)
COMPANIES = [
    ("KAU.NZ", "NZX", "KAU", "Kauri Health", "Health Care", "NZD", "NZ", "company", 0, None, None, None, .85, .09, .55, 9000, .002),
    ("KAU.AX", "ASX", "KAU", "Kauri Health", "Health Care", "AUD", "NZ", "company", 0, None, None, None, .85, .09, .55, 9000, .0007),
    ("TUI.NZ", "NZX", "TUI", "Tui Airports", "Industrials", "NZD", "NZ", "company", 0, None, None, None, .55, .05, .70, 11000, .0015),
    ("PUR.NZ", "NZX", "PUR", "Pureora Power", "Utilities", "NZD", "NZ", "company", 0, None, None, None, .60, .02, .95, 7000, .0012),
    ("RAN.NZ", "NZX", "RAN", "Rangitoto Retail", "Consumer", "NZD", "NZ", "company", 0, None, None, None, .40, .01, .80, 1800, .0018),
    ("WAK.NZ", "NZX", "WAK", "Waka Logistics", "Industrials", "NZD", "NZ", "company", 0, None, None, None, .70, .07, .50, 2600, .0016),
    ("HOI.NZ", "NZX", "HOI", "Hoiho Telecom", "Communication", "NZD", "NZ", "company", 0, None, None, None, .50, .00, 1.10, 6500, .0014),
    ("MAT.NZ", "NZX", "MAT", "Matai Software", "Technology", "NZD", "NZ", "company", 0, None, None, None, .90, .18, .10, 3200, .0025),
    ("KEA.NZ", "NZX", "KEA", "Kea Building", "Materials", "NZD", "NZ", "company", 0, None, None, None, .30, .00, .60, 2200, .0020),
    ("PIP.NZ", "NZX", "PIP", "Pipi Dairy", "Consumer", "NZD", "NZ", "company", 0, None, None, None, .45, .03, .65, 900, .0010),
    ("TOE.NZ", "NZX", "TOE", "Toetoe Gentailer", "Utilities", "NZD", "NZ", "company", 0, None, None, None, .65, .03, .90, 5200, .0012),
    ("MOA.NZ", "NZX", "MOA", "Moa Minerals", "Materials", "NZD", "NZ", "company", 0, None, None, None, .20, .04, .00, 120, .0003),
    ("KAK.NZ", "NZX", "KAK", "Kakapo Finance", "Financials", "NZD", "NZ", "company", 0, None, "2018-01-01", "2023-08-31", .25, -.04, .70, 600, .0015),
    ("WAI.NZ", "NZX", "WAI", "Waitemata Bank", "Financials", "NZD", "NZ", "company", 0, None, None, None, .60, .04, .75, 4000, .0012),
    ("BOO.AX", "ASX", "BOO", "Boomerang Bank", "Financials", "AUD", "AU", "company", 1, "2010-01-01", None, None, .65, .04, .75, 160000, .0020),
    ("WTL.AX", "ASX", "WTL", "Wattle Mining", "Materials", "AUD", "AU", "company", 1, "2010-01-01", None, None, .60, .03, .65, 210000, .0022),
    ("COO.AX", "ASX", "COO", "Coolabah Healthcare", "Health Care", "AUD", "AU", "company", 1, "2010-01-01", None, None, .90, .10, .45, 120000, .0020),
    ("BIL.AX", "ASX", "BIL", "Billabong Software", "Technology", "AUD", "AU", "company", 1, "2016-01-01", None, None, .85, .20, .05, 40000, .0040),
    ("KOA.AX", "ASX", "KOA", "Koala Retail", "Consumer", "AUD", "AU", "company", 1, "2010-01-01", None, None, .50, .02, .80, 30000, .0025),
    ("EMU.AX", "ASX", "EMU", "Emu Energy", "Energy", "AUD", "AU", "company", 1, "2010-01-01", None, None, .45, .02, .70, 50000, .0025),
    ("DIN.AX", "ASX", "DIN", "Dingo Telco", "Communication", "AUD", "AU", "company", 1, "2010-01-01", None, None, .55, .01, 1.00, 45000, .0020),
    ("BAN.AX", "ASX", "BAN", "Banksia Insurance", "Financials", "AUD", "AU", "company", 1, "2010-01-01", None, None, .70, .05, .70, 25000, .0022),
    ("GUM.AX", "ASX", "GUM", "Gumtree Property Group", "Real Estate", "AUD", "AU", "stapled", 0, "2010-01-01", None, None, .55, .02, .95, 15000, .0018),
    ("QOL.AX", "ASX", "QOL", "Quoll Engineering", "Industrials", "AUD", "AU", "company", 1, None, None, None, .60, .06, .40, 400, .0030),
    ("WOM.AX", "ASX", "WOM", "Wombat Construction", "Industrials", "AUD", "AU", "company", 1, "2012-01-01", None, None, .25, .00, .50, 3000, .0035),
    ("KIW.AX", "ASX", "KIW", "Kiwi-Oz Bank", "Financials", "AUD", "AU", "company", 1, "2010-01-01", None, None, .65, .04, .75, 90000, .0020),
    ("PLA.AX", "ASX", "PLA", "Platypus Biotech", "Health Care", "AUD", "AU", "company", 1, "2019-01-01", None, None, .35, .25, .00, 6000, .0045),
    ("ECH.AX", "ASX", "ECH", "Echidna Logistics", "Industrials", "AUD", "AU", "company", 1, "2010-01-01", None, None, .75, .07, .55, 14000, .0025),
]
NZ_CREDIT_PAYERS = {"KIW.AX": 0.25}        # trans-Tasman: share of dividend with NZ imputation credits
MISSING_IMPUTATION = {"PIP.NZ"}
YEAR_ENDS = {"KAU": (3, 31), "TUI": (6, 30), "PUR": (6, 30), "RAN": (7, 31), "WAK": (6, 30), "HOI": (6, 30),
             "MAT": (3, 31), "KEA": (6, 30), "PIP": (5, 31), "TOE": (6, 30), "MOA": (12, 31), "KAK": (3, 31),
             "WAI": (9, 30)}


def _bdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        # Weekdays, minus a few fixed public holidays shared by NZ and Australia.
        if d.weekday() < 5 and (d.month, d.day) not in ((1, 1), (12, 25), (12, 26), (4, 25)):
            out.append(d)
        d += timedelta(days=1)
    return out


def generate(folder: str | Path, end: date = date(2026, 11, 20), years: int = 8, seed: int = 7) -> Path:
    rng = random.Random(seed)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    start = date(end.year - years, end.month, 1)
    days = _bdays(start, end)

    # Market factors and FX
    mkts = {"NZX": [], "ASX": []}
    for ex, mu, vol in (("NZX", 0.06, 0.13), ("ASX", 0.07, 0.15)):
        x = []
        for _ in days:
            x.append(rng.gauss(mu / 252, vol / math.sqrt(252)))
        mkts[ex] = x
    fx, rate = [], 1.08
    for _ in days:
        rate *= math.exp(rng.gauss(0, 0.07 / math.sqrt(252)) - 0.0002 * math.log(rate / 1.09))
        fx.append(rate)

    listings, prices, funds, divs = [], [], [], []
    made: dict[str, dict] = {}
    for row in COMPANIES:
        (tk, ex, cid, name, sector, cur, dom, st, frank, ao_from, lf, lt, q, g, payout, mcap0, turn) = row
        listings.append({"ticker": tk, "exchange": ex, "company_id": cid, "name": name, "sector": sector,
                         "currency": cur, "domicile": dom, "security_type": st, "franking_account": frank,
                         "all_ords_from": ao_from or "", "all_ords_to": "", "listed_from": lf or start.isoformat(),
                         "listed_to": lt or ""})
        lf_d = date.fromisoformat(lf) if lf else start
        lt_d = date.fromisoformat(lt) if lt else end
        if cid not in made:
            made[cid] = _company(rng, cid, cur if dom != "NZ" else "NZD", q, g, payout, mcap0 * 1e6, lf_d, lt_d, end,
                                 YEAR_ENDS.get(cid, (6, 30)), dom)
            funds.extend(made[cid]["funds"])
        comp = made[cid]
        beta = 0.6 + 0.8 * rng.random()
        dev = rng.gauss(0, 0.15)
        idio = 0.30 - 0.18 * q
        shares_by_day = comp["shares_by_day"]
        for i, d in enumerate(days):
            if d < lf_d or d > lt_d:
                continue
            # Price wanders around fair value: market and company shocks fade over about a year.
            dev = dev * (1 - 1.2 / 252) + rng.gauss(0, idio / math.sqrt(252)) + beta * mkts[ex][i]
            p_nzd_or_own = comp["fair_value"](d) * math.exp(dev)
            p = p_nzd_or_own / fx[i] if (cur == "AUD" and comp["currency"] == "NZD") else p_nzd_or_own
            vol = max(0, shares_by_day(d) * turn * math.exp(rng.gauss(0, 0.6)))
            prices.append((tk, d.isoformat(), round(p, 4), int(vol)))
        for exd, amt in comp["dividends"]:
            if not (lf_d <= exd <= lt_d):
                continue
            divs.append({"ticker": tk, "ex_date": exd.isoformat(), "amount": round(amt, 4),
                         "currency": comp["currency"],
                         "imputed_pct": "" if tk in MISSING_IMPUTATION else (round(min(1.0, 0.6 + q * 0.5), 2) if dom == "NZ" else ""),
                         "franked_pct": round(min(1.0, 0.5 + q * 0.6), 2) if dom == "AU" and st == "company" else (0 if dom == "AU" else ""),
                         "nz_credit_pct": NZ_CREDIT_PAYERS.get(tk, 0 if dom == "AU" else "")})

    # Indexes: a cap-weighted-ish index level from the market factor
    for ex, tk in (("NZX", "^NZ50"), ("ASX", "^AXJO")):
        lvl = 1000.0
        for i, d in enumerate(days):
            lvl *= math.exp(mkts[ex][i])
            prices.append((tk, d.isoformat(), round(lvl, 2), 0))

    _write(folder / "listings.csv", listings)
    with (folder / "prices.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "date", "close", "volume"])
        w.writerows(prices)
    _write(folder / "fundamentals.csv", funds)
    _write(folder / "dividends.csv", divs)
    with (folder / "fx.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["pair", "date", "rate"])
        w.writerows(("AUDNZD", d.isoformat(), round(r, 5)) for d, r in zip(days, fx))
    (folder / "README.txt").write_text("FICTIONAL sample data generated by `python -m sharepicker sample-data`.\n"
                                       "None of these companies exist. Do not use for real decisions.\n")
    return folder


def _company(rng, cid, currency, q, g, payout, mcap, lf, lt, end, ye, dom):
    """Annual financials plus a fair-value path the price wanders around."""
    years = list(range(lf.year - 5, end.year + 1))
    margin = 0.06 + 0.22 * q
    fair_pe = 9 + 14 * q + 40 * max(g, 0)
    # Start revenue so that market cap ends up near `mcap` (today's size) after growing.
    rev = mcap / (fair_pe * margin * 0.72) / (1 + g) ** (len(years) - 1)
    shares = mcap / (10 + 10 * rng.random())        # ~$10-20 a share at the start
    equity = mcap * (0.3 + 0.4 * (1 - q))
    debt_ratio = 0.2 + 1.3 * (1 - q) * rng.random()
    dilution = 0.08 if q < 0.3 else 0.0
    funds, divs, points = [], [], []
    for y in years:
        pe = date(y, *ye)
        growth = g + rng.gauss(0, 0.06 * (1.3 - q))
        rev *= 1 + growth
        m = max(0.01, margin + rng.gauss(0, 0.05 * (1.2 - q)))
        gross = rev * min(0.9, m + 0.25 + rng.gauss(0, 0.04 * (1.2 - q)))
        ebit = rev * m
        debt = equity * debt_ratio
        interest = debt * 0.06
        ni = (ebit - interest) * 0.72
        if q < 0.3 and rng.random() < 0.35:
            ni = -abs(ni) * 0.5
        shares *= 1 + dilution
        dps = max(0.0, ni * payout / shares)
        equity += ni - dps * shares
        ocf = ni * (1.1 + 0.3 * q) + rev * 0.03
        capex = rev * (0.04 + 0.05 * (1 - q))
        cash = rev * 0.05
        if pe > end:
            break
        funds.append({"company_id": cid, "period_end": pe.isoformat(), "available_date": "",
                      "currency": currency, "revenue": round(rev), "gross_profit": round(gross), "ebit": round(ebit),
                      "net_income": round(ni), "equity": round(equity), "debt": round(debt), "cash": round(cash),
                      "interest_expense": round(interest), "operating_cash_flow": round(ocf), "capex": round(capex),
                      "shares_out": round(shares), "dps": round(dps, 4)})
        eps = (ebit - interest) * 0.72 / shares
        points.append((pe, max(eps, 0.02) * fair_pe, shares))
        if dps > 0:
            divs.append((pe + timedelta(days=75), dps * 0.6))
            divs.append((pe + timedelta(days=75 + 182), dps * 0.4))

    def fair_value(d: date) -> float:
        if d <= points[0][0]:
            return points[0][1]
        for (d0, v0, _), (d1, v1, _) in zip(points, points[1:]):
            if d0 <= d <= d1:
                t = (d - d0).days / (d1 - d0).days
                return v0 + (v1 - v0) * t
        return points[-1][1]

    def shares_by_day(d: date) -> float:
        s = points[0][2]
        for pd, _, sh in points:
            if pd <= d:
                s = sh
        return s

    return {"funds": funds, "dividends": [x for x in divs if x[0] <= end], "fair_value": fair_value,
            "shares_by_day": shares_by_day, "currency": currency}


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
