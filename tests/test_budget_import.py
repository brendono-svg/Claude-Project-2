from kiwiwealth.budget_import import guess_category, parse_transactions, summarise

APP_CSV = """Date,Description,Amount,Category
01/09/2026,Salary,6000.00,Income
03/09/2026,Countdown,-250.00,Groceries
10/09/2026,Transfer to savings,-1000.00,Transfer
15/09/2026,Refund Countdown,50.00,Groceries
30/09/2026,Mercury,-200.00,Utilities
"""

ANZ_STYLE = """Account,12-3456-7890123-00
Statement period,01/09/2026 - 30/09/2026

Type,Details,Particulars,Code,Reference,Amount,Date,ForeignCurrencyAmount,ConversionCharge
Salary,ACME LTD,,,,"5,000.00",01/09/2026,,
Eftpos,PAK N SAVE,,,,-300.00,05/09/2026,,
Eftpos,Z ENERGY,,,,-80.00,06/09/2026,,
"""

DEBIT_CREDIT = """Transaction Date,Payee,Debit,Credit
2026-09-01,Employer,,4000
2026-09-02,New World,120.50,
"""


def test_budget_app_export():
    s = summarise(parse_transactions(APP_CSV))
    assert s.excluded_transfers == 1
    assert s.categories["Groceries"] == 200.0  # refund netted off
    assert s.monthly_income == 6000.0
    assert s.monthly_spending == 400.0
    assert s.savings_rate > 0.9


def test_bank_export_with_preamble_and_guessed_categories():
    txns = parse_transactions(ANZ_STYLE)
    assert len(txns) == 3
    assert txns[0].amount == 5000.0
    assert txns[1].category == "Groceries"
    assert txns[2].category == "Transport"


def test_debit_credit_columns():
    txns = parse_transactions(DEBIT_CREDIT)
    assert [t.amount for t in txns] == [4000.0, -120.5]


def test_column_map():
    csv = "When,What,NZD\n01/09/2026,Countdown,-10\n"
    txns = parse_transactions(csv, {"date": "When", "amount": "NZD", "description": "What"})
    assert txns[0].amount == -10 and txns[0].category == "Groceries"


def test_guess_category_unknown():
    assert guess_category("Something random") == "Uncategorised"
