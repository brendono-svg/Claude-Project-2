"""NZX / ASX market and tax rules for an NZ-resident investor.

Same pattern as `kiwiwealth/nz_rules.py`: each rule is a dated history plus a source,
so a rule change means adding an entry, not editing an old one. Figures are what was
known in late 2026. Broker fees in particular change often: check the broker's own page.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from kiwiwealth.nz_rules import Rule

AS_OF = date(2026, 11, 25)

RULES: dict[str, Rule] = {
    # NZ imputation: credits attach at the company tax rate. A fully imputed dividend
    # of $72 carries $28 of credits (gross $100).
    "nz_company_tax_rate": Rule(
        history=((date(2008, 4, 1), 0.28),),
        source="https://www.ird.govt.nz/income-tax/income-tax-for-businesses-and-organisations/types-of-business-income/imputation",
    ),
    # Australian franking credits gross up at the Australian company rate. They are
    # only refundable/usable by Australian taxpayers: shown so the report can explain
    # why the headline yield in Australian media overstates what a New Zealander gets.
    "au_company_tax_rate": Rule(
        history=((date(2001, 7, 1), 0.30),),
        source="https://www.ato.gov.au/tax-rates-and-codes/company-tax-rates",
        note="Base-rate entities use 25%; most ASX 200 companies use 30%.",
    ),
    # Withholding on dividends paid to NZ residents. Franked portions carry no
    # withholding; the unfranked portion is withheld at the NZ/Australia DTA rate.
    "au_dividend_wht_nz_resident": Rule(
        history=((date(2010, 3, 19), {"franked": 0.0, "unfranked": 0.15}),),
        source="https://www.ato.gov.au/businesses-and-organisations/international-tax-for-business/foreign-residents-investing-in-australia/withholding-from-dividends-paid-to-foreign-residents",
        note="NZ/Australia DTA article 10 limits it to 15%. Conduit foreign income treatment is not modelled.",
    ),
    # FIF: exemption for shares in Australian-resident companies listed on an approved
    # ASX index that maintain franking accounts. NZ-resident companies are never FIFs,
    # wherever they list.
    "fif_australian_exemption": Rule(
        history=((date(2007, 4, 1), {"approved_index": "S&P/ASX All Ordinaries",
                                     "needs_franking_account": True}),),
        source="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/types-of-individual-income/overseas-income/foreign-investment-funds-fifs",
        note="Unit trusts, A-REITs and stapled securities: exemption status not verified here, "
             "so they are treated as FIF. Check with IRD or your tax adviser.",
    ),
    "settlement_days": Rule(
        history=((date(2014, 3, 7), 2),),
        source="https://www.nzx.com/services/nzx-clearing",
        note="NZX and ASX both settle T+2.",
    ),
    # Illustrative NZ retail broker fees. pct = percentage of trade, min/max in the
    # trade currency, fx = spread charged converting NZD to AUD (and back).
    "brokers": Rule(
        history=((date(2026, 1, 1), {
            "sharesies": {"NZX": {"pct": 0.005, "min": 0.0, "max": 25.0},
                          "ASX": {"pct": 0.005, "min": 0.0, "max": 15.0}, "fx": 0.005},
            "hatch":     {"NZX": {"pct": 0.0, "min": 3.0, "max": 3.0},
                          "ASX": {"pct": 0.0, "min": 3.0, "max": 3.0}, "fx": 0.005},
            "asb":       {"NZX": {"pct": 0.003, "min": 15.0, "max": None},
                          "ASX": {"pct": 0.003, "min": 30.0, "max": None}, "fx": 0.006},
            "jarden":    {"NZX": {"pct": 0.003, "min": 29.9, "max": None},
                          "ASX": {"pct": 0.0035, "min": 39.9, "max": None}, "fx": 0.005},
        }),),
        source="Each broker's published fee page",
        note="Illustrative only. Fees change often; override them in your screen config.",
    ),
}


def rule(name: str, on: date | None = None) -> Any:
    return RULES[name].at(on or AS_OF)


# ---------------------------------------------------------------------------
# FIF classification
# ---------------------------------------------------------------------------

# Values match kiwiwealth's HOLDING_TYPES so kiwiwealth.advice.rule_fif works unchanged.
NZ_LISTED, ASX_EXEMPT, FIF = "nz_listed", "asx_exempt", "direct_offshore"


def fif_class(domicile: str, security_type: str = "company", on_approved_index: bool = False,
              franking_account: bool = False) -> tuple[str, str]:
    """Return (holding type, reason) for an NZ resident holding this security directly."""
    dom = domicile.upper()
    st = security_type.lower()
    if dom == "NZ":
        if st in ("company", "ordinary"):
            return NZ_LISTED, "NZ-resident company: not a FIF wherever it lists"
        return NZ_LISTED, f"NZ-domiciled {st} (often a listed PIE): not a FIF"
    if dom == "AU":
        if st not in ("company", "ordinary"):
            return FIF, f"Australian {st}: exemption not verified, treated as FIF"
        if not on_approved_index:
            return FIF, "Australian company not in the ASX All Ordinaries: FIF"
        if not franking_account:
            return FIF, "Australian company without a franking account: FIF"
        return ASX_EXEMPT, "Australian company in the All Ordinaries with a franking account: FIF-exempt"
    return FIF, f"Domiciled in {dom}: FIF"


# ---------------------------------------------------------------------------
# Dividends for an NZ resident
# ---------------------------------------------------------------------------

def dividend_for_nz_resident(cash: float, domicile: str, *, imputed_pct: float | None = 0.0,
                             franked_pct: float | None = 0.0, nz_credit_pct: float | None = 0.0,
                             marginal_rate: float = 0.33, fif_over_threshold: bool = False,
                             is_fif: bool = False, on: date | None = None) -> dict[str, Any]:
    """Tax on one cash dividend (in its own currency) for an NZ-resident individual.

    imputed_pct   NZ company: share of the dividend fully imputed (0-1)
    franked_pct   Australian company: share franked (0-1); only affects withholding
    nz_credit_pct Australian company paying NZ imputation credits (trans-Tasman), 0-1
    Unknown percentages (None) are reported back as missing so the caller can flag them.
    """
    missing = []
    nz_rate = rule("nz_company_tax_rate", on)
    if domicile.upper() == "NZ":
        if imputed_pct is None:
            missing.append("imputation")
        credits = cash * (imputed_pct or 0.0) * nz_rate / (1 - nz_rate)
        wht = 0.0
    else:
        wht_rates = rule("au_dividend_wht_nz_resident", on) if domicile.upper() == "AU" else {"franked": 0.15, "unfranked": 0.15}
        if domicile.upper() == "AU" and franked_pct is None:
            missing.append("franking")
        f = franked_pct or 0.0
        wht = cash * (f * wht_rates["franked"] + (1 - f) * wht_rates["unfranked"])
        if nz_credit_pct is None:
            missing.append("nz_credits")
        credits = cash * (nz_credit_pct or 0.0) * nz_rate / (1 - nz_rate)

    gross = cash + credits
    if is_fif and fif_over_threshold:
        # Under the FDR method the dividend isn't taxed separately; tax comes from 5% of
        # opening value instead. Foreign withholding can still be claimed against that.
        nz_tax = 0.0
        note = "Over the FIF threshold: dividend replaced by FDR income (5% of opening value)"
    else:
        tax_before_credits = gross * marginal_rate
        nz_tax = max(tax_before_credits - credits - min(wht, cash * marginal_rate), 0.0)
        note = ""
    net = cash - wht - nz_tax
    return {"cash": cash, "nz_credits": round(credits, 6), "gross_nz": round(gross, 6),
            "withholding": round(wht, 6), "nz_tax": round(nz_tax, 6), "net": round(net, 6),
            "missing": missing, "note": note}


def australian_headline_gross(cash: float, franked_pct: float | None, on: date | None = None) -> float:
    """Grossed-up dividend as Australian sites quote it (franking credits added back)."""
    au = rule("au_company_tax_rate", on)
    return cash + cash * (franked_pct or 0.0) * au / (1 - au)


# ---------------------------------------------------------------------------
# Trading costs
# ---------------------------------------------------------------------------

def trade_cost(amount: float, exchange: str, broker: str = "sharesies", home_currency_is_nzd: bool = True,
               brokers: dict | None = None, on: date | None = None) -> float:
    """Cost of one trade of `amount` (trade currency), including FX spread for ASX."""
    table = brokers or rule("brokers", on)
    b = table[broker]
    ex = b[exchange]
    fee = amount * ex["pct"]
    fee = max(fee, ex["min"] or 0.0)
    if ex.get("max") is not None:
        fee = min(fee, ex["max"])
    if exchange != "NZX" and home_currency_is_nzd:
        fee += amount * b.get("fx", 0.0)
    return round(fee, 2)


def round_trip_pct(amount: float, exchange: str, broker: str = "sharesies", brokers: dict | None = None) -> float:
    return 2 * trade_cost(amount, exchange, broker, brokers=brokers) / amount if amount else 0.0
