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
    "emergency_fund_months": 4,
}


@dataclass
class KiwiSaver:
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
    kiwisaver: KiwiSaver | None = None

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


@dataclass
class Mortgage:
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
    mortgage: Mortgage = field(default_factory=Mortgage)
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
        return sum(c.balance for c in self.cash)

    @property
    def total_investments(self) -> float:
        return sum(h.value for h in self.holdings)

    @property
    def total_kiwisaver(self) -> float:
        return sum(p.kiwisaver.balance for p in self.people if p.kiwisaver)

    @property
    def total_debt(self) -> float:
        return self.mortgage.balance + sum(d.balance for d in self.debts)

    @property
    def total_assets(self) -> float:
        return (self.home_value + self.other_property + self.vehicles_and_other
                + self.total_cash + self.total_investments + self.total_kiwisaver)

    @property
    def net_worth(self) -> float:
        return self.total_assets - self.total_debt

    @property
    def liquid_wealth(self) -> float:
        """Assets you could use before retirement (excludes home and KiwiSaver)."""
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
        person = _build(Person, p)
        if ks is not None:
            person.kiwisaver = _build(KiwiSaver, ks)
            _check(person.kiwisaver.fund_type, FUND_TYPES, "kiwisaver.fund_type")
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
        mortgage=_build(Mortgage, d.get("mortgage")),
        debts=[_build(Debt, x) for x in d.get("debts", [])],
        goals=[_build(Goal, g) for g in d.get("goals", [])],
        budget=_build(Budget, d.get("budget")),
        preferences=prefs,
        dependants=d.get("dependants", 0),
        has_wills=d.get("has_wills"),
        has_income_protection=d.get("has_income_protection"),
        assumptions=d.get("assumptions", {}),
    )


def load_profile(path: str | Path) -> Profile:
    with open(path, encoding="utf-8") as f:
        return profile_from_dict(yaml.safe_load(f) or {})
