"""Household profile: what we own, owe, earn, and want."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

FUND_TYPES = ("cash", "conservative", "balanced", "growth", "aggressive")
HOLDING_TYPES = ("pie", "direct_offshore", "nz_listed", "asx_exempt", "term_deposit", "other")

DEFAULT_ASSUMPTIONS = {
    "inflation": 0.02,
    # Nominal long-run returns before fees and tax. These are planning guesses, not forecasts.
    "returns": {"cash": 0.035, "conservative": 0.045, "balanced": 0.06,
                "growth": 0.07, "aggressive": 0.075},
    # Part of the return that is taxed each year inside a PIE fund (dividends, interest, FDR 5%).
    "taxable_yield": {"cash": None, "conservative": 0.04, "balanced": 0.045,
                      "growth": 0.048, "aggressive": 0.05},
    "safe_withdrawal_rate": 0.04,
    "plan_to_age": 95,          # the simulation runs until the youngest person reaches this
    "nz_super_age": None,       # None = current law (65); set higher to stress-test
    "emergency_fund_months": 4,
}


CONTRIBUTION_UNITS = {"pct": None, "week": 52, "fortnight": 26, "month": 12, "year": 1,
                      "six_months": 2}
SCHEME_KINDS = ("kiwisaver", "workplace")


@dataclass
class Contribution:
    """A contribution as a % of salary (unit "pct", amount 0.035) or a $ amount per period."""
    amount: float = 0.0
    unit: str = "pct"
    after_tax: bool = False  # employer $ from a fund statement are usually after ESCT

    def annual(self, salary: float) -> float:
        mult = CONTRIBUTION_UNITS[self.unit]
        return salary * self.amount if mult is None else self.amount * mult


@dataclass
class Scheme:
    """A retirement savings scheme. KiwiSaver is locked until 65; a workplace
    savings scheme usually pays out when you leave the employer."""
    name: str = "KiwiSaver"
    kind: str = "kiwisaver"
    balance: float = 0.0
    fund_type: str = "balanced"
    fee_pct: float = 0.005
    pie: bool = True  # non-PIE super schemes are taxed at 28%
    vested_pct: float = 1.0  # share of the balance you keep if you leave now
    voluntary_annual: float = 0.0  # one-off top-ups per year
    member: Contribution = field(default_factory=Contribution)
    employer: Contribution = field(default_factory=Contribution)


@dataclass
class KiwiSaver:  # legacy profile format; converted to a Scheme on load
    balance: float = 0.0
    employee_rate: float = 0.035
    employer_rate: float = 0.035
    fund_type: str = "balanced"
    fee_pct: float = 0.005
    self_employed_annual: float = 0.0  # voluntary contributions if not on PAYE


@dataclass
class Person:
    name: str
    age: int
    salary: float = 0.0
    other_taxable_income: float = 0.0  # interest, dividends, rent etc. (non-PIE)
    pie_income: float = 0.0
    prior_year_taxable_income: float | None = None
    prior_year_pie_income: float | None = None  # defaults to this year's pie_income
    current_pir: float | None = None  # what's currently set with providers
    student_loan: bool = False
    schemes: list[Scheme] = field(default_factory=list)
    retirement_age: int | None = None  # None = preferences.retirement_age

    @property
    def taxable_income(self) -> float:
        return self.salary + self.other_taxable_income


@dataclass
class Holding:
    name: str
    owner: str  # a person's name or "joint"
    type: str = "pie"
    value: float = 0.0
    cost: float = 0.0
    fund_type: str = "growth"
    fee_pct: float = 0.004
    monthly_contribution: float = 0.0
    single_company: bool = True  # False for a basket of shares held directly

    @property
    def is_direct_shares(self) -> bool:
        return self.type in ("nz_listed", "direct_offshore", "asx_exempt")


@dataclass
class CashAccount:
    name: str
    balance: float
    interest_rate: float = 0.0
    is_emergency_fund: bool = True


LOAN_KINDS = ("table", "offset", "revolving")


@dataclass
class Loan:
    """A home loan. An offset loan charges interest only on balance minus offset_cash."""
    name: str = "Home loan"
    balance: float = 0.0
    rate: float = 0.0
    monthly_repayment: float = 0.0
    kind: str = "table"          # table | offset | revolving
    fixed: bool = False          # fixed rate: extra repayments may trigger break fees
    fixed_until: str | None = None
    offset_cash: float = 0.0     # cash in accounts offsetting this loan (don't also list it under cash)
    revolving_limit: float = 0.0

    @property
    def marginal_rate(self) -> float:
        """What an extra dollar repaid saves: nothing while the loan is fully offset."""
        if self.balance <= 0 or (self.kind == "offset" and self.offset_cash >= self.balance):
            return 0.0
        return self.rate


@dataclass
class Mortgage:  # legacy single-loan format; converted to a Loan on load
    balance: float = 0.0
    rate: float = 0.0
    monthly_repayment: float = 0.0
    kind: str = "table"  # table | revolving | offset
    fixed_until: str | None = None
    revolving_limit: float = 0.0


@dataclass
class Debt:
    name: str
    balance: float
    rate: float
    monthly_payment: float = 0.0


@dataclass
class Goal:
    name: str
    target_amount: float
    target_year: int
    saved_so_far: float = 0.0
    priority: int = 2  # 1 = must have, 3 = nice to have


@dataclass
class Budget:
    monthly_net_income: float | None = None
    monthly_spending: float | None = None
    categories: dict[str, float] = field(default_factory=dict)
    source: str = "manual"


@dataclass
class Preferences:
    risk_tolerance: str = "growth"
    preferred_vehicles: list[str] = field(default_factory=lambda: ["managed_funds", "shares"])
    retirement_age: int = 65
    retirement_income_target: float = 70_000  # per year, today's dollars, whole household
    max_single_share_pct: float = 0.10


@dataclass
class Profile:
    household: str
    people: list[Person]
    home_value: float = 0.0
    other_property: float = 0.0
    vehicles_and_other: float = 0.0
    cash: list[CashAccount] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    loans: list[Loan] = field(default_factory=list)
    debts: list[Debt] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    preferences: Preferences = field(default_factory=Preferences)
    dependants: int = 0
    has_wills: bool | None = None
    has_income_protection: bool | None = None
    assumptions: dict[str, Any] = field(default_factory=dict)

    def assumption(self, key: str):
        base = DEFAULT_ASSUMPTIONS[key]
        override = self.assumptions.get(key)
        if isinstance(base, dict):
            return {**base, **(override or {})}
        return base if override is None else override

    # -- Balance sheet -----------------------------------------------------
    @property
    def total_cash(self) -> float:
        return sum(c.balance for c in self.cash) + self.offset_cash

    @property
    def offset_cash(self) -> float:
        return sum(l.offset_cash for l in self.loans if l.kind == "offset")

    @property
    def total_investments(self) -> float:
        return sum(h.value for h in self.holdings)

    @property
    def total_kiwisaver(self) -> float:
        return sum(s.balance for p in self.people for s in p.schemes if s.kind == "kiwisaver")

    @property
    def total_workplace(self) -> float:
        return sum(s.balance for p in self.people for s in p.schemes if s.kind == "workplace")

    @property
    def total_debt(self) -> float:
        return sum(l.balance for l in self.loans) + sum(d.balance for d in self.debts)

    @property
    def total_assets(self) -> float:
        return (self.home_value + self.other_property + self.vehicles_and_other
                + self.total_cash + self.total_investments + self.total_kiwisaver
                + self.total_workplace)

    @property
    def net_worth(self) -> float:
        return self.total_assets - self.total_debt

    @property
    def liquid_wealth(self) -> float:
        """Assets you can use any time (excludes home, KiwiSaver and workplace schemes)."""
        return self.total_cash + self.total_investments

    def person(self, name: str) -> Person | None:
        return next((p for p in self.people if p.name == name), None)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _build(cls, data: dict[str, Any] | None):
    if data is None:
        return cls()
    names = {f.name for f in fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"Unknown field(s) for {cls.__name__}: {', '.join(sorted(unknown))}")
    return cls(**data)


def _check(value: str, allowed: tuple[str, ...], what: str) -> None:
    if value not in allowed:
        raise ValueError(f"{what} must be one of {allowed}, got {value!r}")


def profile_from_dict(d: dict[str, Any]) -> Profile:
    people = []
    for p in d.get("people", []):
        p = dict(p)
        ks = p.pop("kiwisaver", None)
        schemes = p.pop("schemes", [])
        person = _build(Person, p)
        for sc in schemes:
            sc = dict(sc)
            member, employer = sc.pop("member", None), sc.pop("employer", None)
            scheme = _build(Scheme, sc)
            scheme.member = _build(Contribution, member)
            scheme.employer = _build(Contribution, employer)
            _check(scheme.kind, SCHEME_KINDS, f"scheme {scheme.name!r} kind")
            _check(scheme.fund_type, FUND_TYPES, f"scheme {scheme.name!r} fund_type")
            for c in (scheme.member, scheme.employer):
                _check(c.unit, tuple(CONTRIBUTION_UNITS), f"scheme {scheme.name!r} contribution unit")
            person.schemes.append(scheme)
        if ks is not None:  # legacy format
            old = _build(KiwiSaver, ks)
            _check(old.fund_type, FUND_TYPES, "kiwisaver.fund_type")
            person.schemes.insert(0, Scheme(
                name="KiwiSaver", kind="kiwisaver", balance=old.balance, fund_type=old.fund_type,
                fee_pct=old.fee_pct, voluntary_annual=old.self_employed_annual,
                member=Contribution(old.employee_rate, "pct"),
                employer=Contribution(old.employer_rate, "pct")))
        people.append(person)
    if not people:
        raise ValueError("Profile needs at least one person")

    holdings = [_build(Holding, h) for h in d.get("holdings", [])]
    valid_owners = {p.name for p in people} | {"joint"}
    for h in holdings:
        _check(h.type, HOLDING_TYPES, f"holding {h.name!r} type")
        _check(h.fund_type, FUND_TYPES, f"holding {h.name!r} fund_type")
        if h.owner not in valid_owners:
            raise ValueError(f"holding {h.name!r} owner must be one of {sorted(valid_owners)}")

    loans = [_build(Loan, x) for x in d.get("loans", [])]
    if d.get("mortgage") and "loans" not in d:   # legacy single mortgage; ignored once loans are listed
        old = _build(Mortgage, d["mortgage"])
        loans.insert(0, Loan(name="Mortgage", balance=old.balance, rate=old.rate,
                             monthly_repayment=old.monthly_repayment, kind=old.kind,
                             fixed_until=old.fixed_until, revolving_limit=old.revolving_limit))
    for l in loans:
        _check(l.kind, LOAN_KINDS, f"loan {l.name!r} kind")

    prefs = _build(Preferences, d.get("preferences"))
    _check(prefs.risk_tolerance, FUND_TYPES[1:], "preferences.risk_tolerance")

    return Profile(
        household=d.get("household", "Our household"),
        people=people,
        home_value=d.get("home_value", 0.0),
        other_property=d.get("other_property", 0.0),
        vehicles_and_other=d.get("vehicles_and_other", 0.0),
        cash=[_build(CashAccount, c) for c in d.get("cash", [])],
        holdings=holdings,
        loans=loans,
        debts=[_build(Debt, x) for x in d.get("debts", [])],
        goals=[_build(Goal, g) for g in d.get("goals", [])],
        budget=_build(Budget, d.get("budget")),
        preferences=prefs,
        dependants=d.get("dependants", 0),
        has_wills=d.get("has_wills"),
        has_income_protection=d.get("has_income_protection"),
        assumptions=d.get("assumptions", {}),
    )



# ---------------------------------------------------------------------------
# Phone app data ("Copy my data" in app/kiwiwealth.html, or the synced record)
# ---------------------------------------------------------------------------


def _blank_none(v):
    return None if v in ("", None) else v


def profile_from_app_state(state: dict[str, Any]) -> Profile:
    """Convert the phone app's JSON into a Profile, so both engines can run on the same data."""
    prefs = state.get("prefs", {})
    names = {p["name"] for p in state.get("people", [])}
    people = []
    for p in state.get("people", []):
        people.append({
            "name": p["name"], "age": int(p.get("age") or 0), "salary": p.get("salary") or 0,
            "other_taxable_income": p.get("other") or 0, "pie_income": p.get("pie") or 0,
            "prior_year_taxable_income": _blank_none(p.get("priorTaxable")),
            "prior_year_pie_income": _blank_none(p.get("priorPie")),
            "current_pir": _blank_none(p.get("currentPir")), "student_loan": bool(p.get("sl")),
            "retirement_age": _blank_none(p.get("retireAge")),
            "schemes": [{
                "name": s.get("name", "Scheme"), "kind": s.get("kind", "kiwisaver"),
                "balance": s.get("balance") or 0, "fund_type": s.get("fund", "balanced"),
                "fee_pct": s.get("fee") or 0, "pie": s.get("pie", True) is not False,
                "vested_pct": 1 if s.get("vested") in ("", None) else s["vested"],
                "voluntary_annual": s.get("voluntary") or 0,
                "member": {"amount": (s.get("member") or {}).get("amount") or 0,
                           "unit": (s.get("member") or {}).get("unit", "pct")},
                "employer": {"amount": (s.get("employer") or {}).get("amount") or 0,
                             "unit": (s.get("employer") or {}).get("unit", "pct"),
                             "after_tax": bool((s.get("employer") or {}).get("afterTax"))},
            } for s in p.get("schemes", [])],
        })
    loans = [{"name": l.get("name", "Home loan"), "balance": l.get("balance") or 0, "rate": l.get("rate") or 0,
              "monthly_repayment": l.get("repayment") or 0, "kind": l.get("kind", "table"),
              "fixed": bool(l.get("fixed")), "offset_cash": l.get("offsetCash") or 0,
              "revolving_limit": l.get("limit") or 0} for l in state.get("loans", [])]
    mort = state.get("mortgage")
    if mort and "loans" not in state and ((mort.get("balance") or 0) > 0 or (mort.get("repayment") or 0) > 0):   # data saved before loans were a list (same rule as the app's migrate)
        loans = [{"name": "Mortgage", "balance": mort.get("balance") or 0, "rate": mort.get("rate") or 0,
                  "monthly_repayment": mort.get("repayment") or 0, "kind": mort.get("kind", "table"),
                  "revolving_limit": mort.get("limit") or 0}]
    budget = state.get("budget", {})
    return profile_from_dict({
        "household": state.get("household", "Our household"),
        "people": people,
        "home_value": state.get("home") or 0, "vehicles_and_other": state.get("other") or 0,
        "dependants": state.get("dependants") or 0,
        "has_wills": bool(state.get("wills")), "has_income_protection": bool(state.get("incomeCover")),
        "cash": [{"name": c.get("name", "Cash"), "balance": c.get("balance") or 0,
                  "interest_rate": c.get("rate") or 0, "is_emergency_fund": bool(c.get("emergency"))}
                 for c in state.get("cash", [])],
        "holdings": [{"name": h.get("name", "Holding"),
                      "owner": h.get("owner") if h.get("owner") in names else "joint",
                      "type": h.get("type", "pie"), "value": h.get("value") or 0, "cost": h.get("cost") or 0,
                      "fee_pct": h.get("fee") or 0, "monthly_contribution": h.get("monthly") or 0,
                      "single_company": bool(h.get("single"))} for h in state.get("holdings", [])],
        "loans": loans,
        "debts": [{"name": d.get("name", "Debt"), "balance": d.get("balance") or 0, "rate": d.get("rate") or 0}
                  for d in state.get("debts", [])],
        "goals": [{"name": g.get("name", "Goal"), "target_amount": g.get("target") or 0,
                   "target_year": int(g.get("year") or 0), "saved_so_far": g.get("saved") or 0,
                   "priority": int(g.get("priority") or 2)} for g in state.get("goals", [])],
        "budget": {"monthly_net_income": _blank_none(budget.get("income")),
                   "monthly_spending": _blank_none(budget.get("spending"))},
        "preferences": {"risk_tolerance": prefs.get("risk", "growth"),
                        "preferred_vehicles": ["managed_funds"] + (["shares"] if prefs.get("shares") else []),
                        "retirement_age": int(prefs.get("retireAge") or 65),
                        "retirement_income_target": prefs.get("target") or 0,
                        "max_single_share_pct": prefs.get("maxShare") or 0.10},
    })


def load_profile(path: str | Path) -> Profile:
    """Load a YAML profile, or the phone app's JSON (a file ending in .json)."""
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        if path.suffix == ".json":
            import json
            data = json.load(f)
            data = data.get("data", data)            # a raw synced record
            return profile_from_app_state(data.get("profile", data))
        return profile_from_dict(yaml.safe_load(f) or {})
