"""Data records. Every record knows where it came from (`source`) and how current it is."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class Listing:
    ticker: str               # e.g. FPH.NZ, FPH.AX
    exchange: str             # NZX | ASX | other venue code
    company_id: str           # same for every listing of one company (dual listings)
    name: str
    sector: str = "Other"
    currency: str = "NZD"
    domicile: str = "NZ"      # country of tax residence: NZ | AU | US ...
    security_type: str = "company"   # company | etf | unit_trust | stapled | reit
    franking_account: bool = False
    all_ords_from: date | None = None   # in the ASX All Ordinaries from/to (FIF exemption)
    all_ords_to: date | None = None
    listed_from: date | None = None
    listed_to: date | None = None       # set for delisted shares (avoids survivorship bias)
    balance_date: str = ""              # "MM-DD" financial year end, e.g. "03-31"; for the results calendar
    units_per_share: float = 1.0        # e.g. 10 for ResMed: 10 ASX CDIs = 1 US share
    credit_pct: float | None = None     # typical imputation (NZ) or franking (AU) share, used when a dividend has none
    source: str = "csv"

    def listed_on(self, d: date) -> bool:
        return (self.listed_from is None or self.listed_from <= d) and (self.listed_to is None or d <= self.listed_to)

    def in_all_ords(self, d: date) -> bool:
        return self.all_ords_from is not None and self.all_ords_from <= d and (self.all_ords_to is None or d <= self.all_ords_to)


@dataclass
class Fundamentals:
    """One annual reporting period for a company. Money in the reporting currency."""
    company_id: str
    period_end: date
    available_date: date          # first day the market could know these numbers
    currency: str = "NZD"
    revenue: float | None = None
    gross_profit: float | None = None
    ebit: float | None = None
    net_income: float | None = None
    equity: float | None = None
    debt: float | None = None
    cash: float | None = None
    interest_expense: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    shares_out: float | None = None
    dps: float | None = None      # dividends per share declared for the period
    source: str = "csv"

    @property
    def eps(self) -> float | None:
        if self.net_income is None or not self.shares_out:
            return None
        return self.net_income / self.shares_out


@dataclass
class Dividend:
    ticker: str
    ex_date: date
    amount: float                 # cash per share in `currency`
    currency: str = "NZD"
    imputed_pct: float | None = None   # NZ imputation, 0-1; None = unknown
    franked_pct: float | None = None   # Australian franking, 0-1; None = unknown
    nz_credit_pct: float | None = None  # trans-Tasman NZ credits on an Australian dividend
    source: str = "csv"


@dataclass
class Series:
    """A dated series (prices, an index, an FX rate). Dates ascending."""
    name: str
    dates: list[date] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)
    source: str = "csv"

    def index_on(self, d: date) -> int:
        """Index of the last point on or before d, or -1."""
        return bisect_right(self.dates, d) - 1

    def on(self, d: date, max_gap_days: int = 10) -> float | None:
        i = self.index_on(d)
        if i < 0 or (d - self.dates[i]).days > max_gap_days:
            return None
        return self.values[i]

    def until(self, d: date) -> "Series":
        i = self.index_on(d) + 1
        return Series(self.name, self.dates[:i], self.values[:i], self.volumes[:i], self.source)

    def window(self, start: date, end: date) -> "Series":
        lo = bisect_right(self.dates, start - timedelta(days=1))
        hi = bisect_right(self.dates, end)
        return Series(self.name, self.dates[lo:hi], self.values[lo:hi], self.volumes[lo:hi], self.source)

    @property
    def last_date(self) -> date | None:
        return self.dates[-1] if self.dates else None

    def __len__(self) -> int:
        return len(self.dates)


def month_ends(series: Series) -> list[int]:
    """Indexes of the last observation in each calendar month."""
    out = []
    for i, d in enumerate(series.dates):
        if i + 1 == len(series.dates) or series.dates[i + 1].month != d.month:
            out.append(i)
    return out
