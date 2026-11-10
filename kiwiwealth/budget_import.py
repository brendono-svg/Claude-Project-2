"""Read transaction CSVs from a budgeting app or NZ bank and work out savings habits.

Supported out of the box (auto-detected from the header row):
  * Budgeting apps that export Date / Description / Amount / Category
    (PocketSmith, Moneyhub-style and most spreadsheet exports)
  * ANZ, ASB, BNZ, Kiwibank and Westpac NZ CSV exports
  * Any CSV with separate Debit / Credit (or Withdrawal / Deposit) columns
Use `column_map` to point at other column names.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

DATE_COLS = ("date", "transaction date", "processed date")
AMOUNT_COLS = ("amount", "amount (nzd)", "value")
DEBIT_COLS = ("debit", "withdrawal", "withdrawals", "amount (debit)", "money out")
CREDIT_COLS = ("credit", "deposit", "deposits", "amount (credit)", "money in")
DESC_COLS = ("description", "payee", "details", "other party", "merchant", "memo/description",
             "memo", "particulars", "op name")
CATEGORY_COLS = ("category", "categories", "budget category")

# Credit-card repayments are counted as spending, because the card itself often isn't imported.
# If you import the card's CSV too, categorise repayments as "Transfer" so spending isn't counted twice.
# Do the same for automatic contributions to your investments: they're saving, not spending.
TRANSFER_CATEGORIES = {"transfer", "transfers", "internal transfer", "savings transfer"}
INCOME_CATEGORIES = {"income", "salary", "wages", "pay"}

# Rough categories for bank exports that have none. Lower-case substrings.
KEYWORDS = {
    "Groceries": ("countdown", "woolworths", "pak n save", "paknsave", "pak'nsave", "new world",
                  "four square", "fresh choice", "supervalue"),
    "Transport": ("z energy", " bp ", "bp connect", "mobil", "caltex", "gull", "waitomo",
                  "at hop", "snapper", "uber", "nzta", "waka kotahi"),
    "Utilities": ("mercury", "genesis", "contact energy", "meridian", "electric kiwi",
                  "powershop", "watercare", "one nz", "spark", "2degrees", "vodafone"),
    "Insurance": ("aa insurance", "ami ", "state insurance", "tower", "southern cross",
                  "nib ", "partners life", "fidelity life", "aia "),
    "Dining & takeaways": ("cafe", "restaurant", "mcdonald", "kfc", "burger", "uber eats",
                           "delivereasy", "domino", "subway"),
    "Shopping": ("kmart", "the warehouse", "farmers", "briscoes", "mitre 10", "bunnings",
                 "noel leeming", "jb hi-fi", "amazon", "temu"),
    "Subscriptions": ("netflix", "spotify", "neon", "disney", "sky ", "apple.com", "google"),
    "Rates": ("council", "rates"),
    "Mortgage": ("mortgage", "home loan", "loan repayment"),
}


@dataclass
class Transaction:
    date: date
    amount: float  # positive = money in
    description: str = ""
    category: str = ""


@dataclass
class BudgetSummary:
    months: float
    monthly_income: float
    monthly_spending: float
    categories: dict[str, float] = field(default_factory=dict)  # average monthly spend
    transactions: int = 0
    excluded_transfers: int = 0

    @property
    def monthly_surplus(self) -> float:
        return self.monthly_income - self.monthly_spending

    @property
    def savings_rate(self) -> float:
        return self.monthly_surplus / self.monthly_income if self.monthly_income else 0.0


def _norm(s: str) -> str:
    return s.strip().lower().lstrip("﻿")


def _find(header: list[str], options) -> int | None:
    norm = [_norm(h) for h in header]
    for opt in options:
        if opt in norm:
            return norm.index(opt)
    return None


def _parse_date(s: str) -> date:
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y",
                "%d %B %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {s!r}")


def _parse_amount(s: str) -> float:
    s = s.strip().replace("$", "").replace(",", "").replace("NZD", "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    v = float(s)
    return -v if neg else v


def guess_category(description: str) -> str:
    d = f" {description.lower()} "
    for cat, words in KEYWORDS.items():
        if any(w in d for w in words):
            return cat
    return "Uncategorised"


def parse_transactions(text: str, column_map: dict[str, str] | None = None) -> list[Transaction]:
    rows = list(csv.reader(io.StringIO(text)))
    # Bank exports often have a few preamble lines; find the real header row.
    header_idx = None
    for i, row in enumerate(rows[:30]):
        norm = [_norm(c) for c in row]
        has_date = any(c in DATE_COLS for c in norm) or (column_map and _norm(column_map.get("date", "")) in norm)
        has_amt = any(c in AMOUNT_COLS + DEBIT_COLS + CREDIT_COLS for c in norm) or (
            column_map and _norm(column_map.get("amount", "")) in norm)
        if has_date and has_amt:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Couldn't find a header row with a date and an amount column. "
                         "Pass column_map={'date': ..., 'amount': ...}.")
    header = rows[header_idx]
    cm = {k: _norm(v) for k, v in (column_map or {}).items()}

    def col(key, options):
        return _find(header, (cm[key],)) if key in cm else _find(header, options)

    i_date = col("date", DATE_COLS)
    i_amt = col("amount", AMOUNT_COLS)
    i_debit, i_credit = col("debit", DEBIT_COLS), col("credit", CREDIT_COLS)
    i_desc, i_cat = col("description", DESC_COLS), col("category", CATEGORY_COLS)
    if i_amt is None and i_debit is None and i_credit is None:
        raise ValueError("No amount / debit / credit column found")

    out = []
    for row in rows[header_idx + 1:]:
        if not row or not any(c.strip() for c in row) or len(row) <= (i_date or 0):
            continue
        try:
            d = _parse_date(row[i_date])
        except ValueError:
            continue  # footer / total lines
        if i_amt is not None and i_amt < len(row) and row[i_amt].strip():
            amount = _parse_amount(row[i_amt])
        else:
            debit = _parse_amount(row[i_debit]) if i_debit is not None and i_debit < len(row) else 0.0
            credit = _parse_amount(row[i_credit]) if i_credit is not None and i_credit < len(row) else 0.0
            amount = credit - abs(debit)
        desc = row[i_desc].strip() if i_desc is not None and i_desc < len(row) else ""
        cat = row[i_cat].strip() if i_cat is not None and i_cat < len(row) else ""
        out.append(Transaction(d, amount, desc, cat or guess_category(desc)))
    return out


def summarise(transactions: list[Transaction]) -> BudgetSummary:
    if not transactions:
        raise ValueError("No transactions to summarise")
    first = min(t.date for t in transactions)
    last = max(t.date for t in transactions)
    months = max(((last - first).days + 1) / 30.44, 1.0)

    income = spend = 0.0
    cats: dict[str, float] = defaultdict(float)
    transfers = 0
    for t in transactions:
        cat_l = t.category.strip().lower()
        if cat_l in TRANSFER_CATEGORIES or re.search(r"\btransfer\b", t.description.lower()):
            transfers += 1
            continue
        if t.amount > 0:
            income += t.amount
        else:
            spend += -t.amount
            cats[t.category or "Uncategorised"] += -t.amount
    # Refunds sit in the same category as the purchase in most apps; net them off there.
    for t in transactions:
        if (t.amount > 0 and t.category in cats and t.category != "Uncategorised"
                and t.category.lower() not in INCOME_CATEGORIES
                and t.category.lower() not in TRANSFER_CATEGORIES
                and not re.search(r"\btransfer\b", t.description.lower())):
            cats[t.category] -= t.amount
            income -= t.amount
            spend -= t.amount

    return BudgetSummary(
        months=round(months, 1),
        monthly_income=round(income / months, 2),
        monthly_spending=round(spend / months, 2),
        categories={k: round(v / months, 2) for k, v in
                    sorted(cats.items(), key=lambda kv: -kv[1]) if v > 0},
        transactions=len(transactions),
        excluded_transfers=transfers,
    )


def load_budget_files(paths: list[str | Path], column_map: dict[str, str] | None = None) -> BudgetSummary:
    txns: list[Transaction] = []
    for p in paths:
        txns += parse_transactions(Path(p).read_text(encoding="utf-8-sig"), column_map)
    return summarise(txns)
