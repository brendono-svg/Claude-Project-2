"""Score the shares you hold, check concentration and FIF, and size new ideas to fit
the satellite that the kiwiwealth plan leaves for share picking."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from kiwiwealth import nz_rules as nz
from kiwiwealth.profile import Profile

from . import market_rules as mr
from .factors import FX
from .screen import ScreenResult


@dataclass
class Position:
    ticker: str
    owner: str
    units: float
    cost_nzd: float
    name: str = ""
    sector: str = ""
    exchange: str = ""
    value_nzd: float | None = None
    rank: int | None = None
    composite: float | None = None
    fif_type: str = ""
    fif_reason: str = ""
    note: str = ""
    dividends_nzd: float | None = None        # last 12 months, cash
    dividends_after_tax_nzd: float | None = None


@dataclass
class WatchResult:
    positions: list[Position]
    total_value: float
    by_sector: dict[str, float]
    by_country: dict[str, float]
    alerts: list[str] = field(default_factory=list)
    fif: dict[str, dict[str, Any]] = field(default_factory=dict)
    satellite: dict[str, float] | None = None
    ideas: list[dict[str, Any]] = field(default_factory=list)


def watch(holdings: list[dict], screen: ScreenResult, source, as_of: date, profile: Profile | None = None,
          *, max_sector_pct: float = 0.35, broker: str = "sharesies", n_ideas: int = 5,
          include_profile_offshore: bool = True) -> WatchResult:
    fx = FX(source)
    universe = {lst.ticker: lst for lst in source.universe()}
    scored = {r.ticker: r for r in screen.ranked}
    alias = {o: main for main, others in screen.other_listings.items() for o in others}
    people = [p.name for p in profile.people] if profile else sorted({h.get("owner", "joint") for h in holdings} - {"joint"})

    positions = []
    for h in holdings:
        t = h["ticker"]
        lst = universe.get(t)
        pos = Position(t, h.get("owner", "joint"), float(h.get("units", 0)), float(h.get("cost", 0)))
        if lst is None:
            pos.note = "not in the data universe"
            positions.append(pos)
            continue
        pos.name, pos.sector, pos.exchange = lst.name, lst.sector, lst.exchange
        px = source.prices(t).on(as_of, 10)
        rate = fx.rate(lst.currency, "NZD", as_of)
        pos.value_nzd = px * pos.units * rate if px and rate else None
        r = scored.get(t) or scored.get(alias.get(t, ""))
        if r:
            pos.rank, pos.composite = r.rank, r.composite
            if t in alias:
                pos.note = f"scored on {alias[t]} (more liquid listing)"
        else:
            pos.note = screen.excluded.get(t, "not scored")
        pos.fif_type, pos.fif_reason = mr.fif_class(lst.domicile, lst.security_type, lst.in_all_ords(as_of),
                                                    lst.franking_account)
        positions.append(pos)

    total = sum(p.value_nzd or 0 for p in positions)
    by_sector: dict[str, float] = {}
    by_country: dict[str, float] = {}
    for p in positions:
        if p.value_nzd:
            by_sector[p.sector or "Unknown"] = by_sector.get(p.sector or "Unknown", 0) + p.value_nzd
            dom = universe[p.ticker].domicile if p.ticker in universe else "?"
            by_country[dom] = by_country.get(dom, 0) + p.value_nzd
    res = WatchResult(positions, total, by_sector, by_country)

    # --- satellite room (kiwiwealth plan) -----------------------------------
    max_single = profile.preferences.max_single_share_pct if profile else 0.10
    if profile:
        core = profile.preferences.core_pct
        # The watchlist replaces the profile's NZX/ASX share holdings; the profile's other
        # direct shares (e.g. US shares) still count towards the satellite.
        replaced = sum(h.value for h in profile.holdings if h.type in (mr.NZ_LISTED, mr.ASX_EXEMPT))
        other_direct = sum(h.value for h in profile.holdings if h.type == mr.FIF)
        investable = profile.total_investments - replaced + total
        target = (1 - core) * investable
        current = total + other_direct
        res.satellite = {"investable": investable, "core_pct": core, "target": target, "current": current,
                         "other_direct": other_direct, "room": target - current}
        if core >= 1:
            res.alerts.append("Your kiwiwealth plan has no satellite for individual shares "
                              "(shares are not a preferred vehicle).")
        elif current > target * 1.1:
            res.alerts.append(f"Direct shares are ${current:,.0f}, above the ${target:,.0f} satellite in your plan. "
                              "Point new money at the core funds.")
        base = investable
    else:
        base = total
    for p in positions:
        if p.value_nzd and base and p.value_nzd / base > max_single:
            res.alerts.append(f"{p.ticker} is {p.value_nzd / base:.0%} of {'investments' if profile else 'your shares'} "
                              f"(limit {max_single:.0%}).")
    for s, v in by_sector.items():
        if total and v / total > max_sector_pct and len(positions) > 2:
            res.alerts.append(f"{s} is {v / total:.0%} of your shares (limit {max_sector_pct:.0%}).")
    if total and len(by_country) == 1:
        only = next(iter(by_country))
        res.alerts.append(f"All your direct shares are in {only}-domiciled companies.")
    for p in positions:
        if p.rank and screen.ranked and p.rank > len(screen.ranked) * 0.8:
            res.alerts.append(f"{p.ticker} ranks {p.rank} of {len(screen.ranked)} in this screen: worth a review "
                              "(a low score is a prompt to check the story, not an instruction to sell).")

    # --- FIF per person ---------------------------------------------------------
    names = people or ["joint"]
    for person in names:
        share_of = lambda owner: 1.0 if owner == person else (1 / len(names) if owner == "joint" else 0.0)  # noqa: E731
        cost = sum(p.cost_nzd * share_of(p.owner) for p in positions if p.fif_type == mr.FIF)
        value = sum((p.value_nzd or 0) * share_of(p.owner) for p in positions if p.fif_type == mr.FIF)
        if profile and include_profile_offshore:
            for h in profile.holdings:
                if h.type == "direct_offshore":
                    cost += h.cost * share_of(h.owner)
                    value += h.value * share_of(h.owner)
        rate = 0.33
        if profile and profile.person(person):
            rate = nz.marginal_tax_rate(profile.person(person).taxable_income, as_of)
        st = nz.fif_status(cost, value, rate, as_of)
        res.fif[person] = {"cost": cost, "value": value, "marginal_rate": rate, **st}
        if st["over_threshold"]:
            res.alerts.append(f"{person}: FIF shares cost ${cost:,.0f}, over $50,000. FDR tax applies "
                              f"(about ${st['estimated_tax']:,.0f} a year).")
        elif cost and st["headroom"] < 10_000:
            res.alerts.append(f"{person}: only ${st['headroom']:,.0f} of FIF headroom left. New overseas or "
                              "non-exempt ASX shares are better bought by another person, or held via PIE funds.")

    # --- dividends after tax, per owner (rate and FIF status differ by person) --------------
    for p in positions:
        lst = universe.get(p.ticker)
        if lst is None:
            continue
        owners = names if p.owner == "joint" else [p.owner]
        cash = net = 0.0
        for dv in source.dividends(p.ticker):
            if not (as_of - timedelta(days=365) < dv.ex_date <= as_of):
                continue
            rate_nzd = fx.rate(dv.currency, "NZD", dv.ex_date) or 0.0
            for o in owners:
                f = res.fif.get(o, {"marginal_rate": 0.33, "over_threshold": False})
                t = mr.dividend_for_nz_resident(dv.amount * rate_nzd * p.units / len(owners), lst.domicile,
                                                imputed_pct=dv.imputed_pct, franked_pct=dv.franked_pct,
                                                nz_credit_pct=dv.nz_credit_pct, marginal_rate=f["marginal_rate"],
                                                fif_over_threshold=f["over_threshold"],
                                                is_fif=p.fif_type == mr.FIF, on=dv.ex_date)
                cash += t["cash"]
                net += t["net"]
        p.dividends_nzd, p.dividends_after_tax_nzd = round(cash, 2), round(net, 2)

    # --- ideas: high-ranked shares you don't hold that fit the plan -------------------------
    held = {p.ticker for p in positions} | {alias.get(p.ticker, p.ticker) for p in positions}
    room = res.satellite["room"] if res.satellite else None
    parcel_cap = max_single * base if base else 5_000
    # No room left: show ideas as swaps for your lowest-ranked holdings instead of new money.
    swap_mode = room is not None and room < 500
    swappable = sorted((p for p in positions if p.value_nzd),
                       key=lambda p: (p.rank is not None, -(p.rank or 0)))
    tight_fif = {k for k, v in res.fif.items() if v["headroom"] < 10_000}
    plan_sector, plan_total = dict(by_sector), total
    for r in screen.ranked:
        if len(res.ideas) >= n_ideas:
            break
        if r.ticker in held or r.composite is None or r.confidence == "low":
            continue
        lst = r.snap.listing
        sector_now = plan_sector.get(lst.sector, 0)
        swap = None
        if swap_mode:
            # Only worth the trading costs if the gap is clear (10+ points, or the holding isn't scored).
            cands = [p for p in swappable if p.composite is None or r.composite - p.composite >= 10]
            if not cands:
                break
            swap = cands[0]
            swappable.remove(swap)
            parcel = min(parcel_cap, swap.value_nzd)
        else:
            parcel = min(parcel_cap, room) if room is not None else parcel_cap
        if plan_total > 0 and not swap:  # stay under the sector limit once this parcel is added
            parcel = min(parcel, max((max_sector_pct * (plan_total + parcel) - sector_now), 0))
        if parcel < 500:
            continue
        ftype, _ = screen.fif.get(r.ticker, ("", ""))
        if ftype == mr.FIF and tight_fif and len(tight_fif) == len(names):
            continue
        ex = lst.exchange if lst.exchange in ("NZX", "ASX") else "ASX"
        res.ideas.append({"ticker": r.ticker, "name": lst.name, "rank": r.rank, "composite": r.composite,
                          "sector": lst.sector, "parcel_nzd": round(parcel, -1), "fif_type": ftype,
                          "round_trip_cost_pct": mr.round_trip_pct(parcel, ex, broker),
                          "swap_for": swap.ticker if swap else None,
                          "swap_note": (f"rank {swap.rank}" if swap and swap.rank else swap.note if swap else "")})
        plan_sector[lst.sector] = sector_now + parcel
        plan_total += parcel
        if room is not None and not swap_mode:
            room -= parcel
    return res
