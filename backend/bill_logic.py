"""Pure rules for bills. No database, no network. Ported from RIPS bills/logic.py (Brief 15)
and scoped to a node: no business-unit picker, the node-level tracking option applies."""
from __future__ import annotations

import difflib
import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple


VAT_RATE_FALLBACK = 0.15

# A bill is only ever captured from the supplier's own invoice. No GRN link in Vezubunye:
# powder arrives through the powder ledger; Fenix's own invoice is an ordinary bill.
BILL_SOURCES = ("upload", "test")


# ---------- normalisation ---------- #

def normalise_invoice_number(s: Optional[str]) -> str:
    """Uppercase, strip spaces and punctuation, for duplicate checks."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


_LEGAL = re.compile(r"\b(pty|ltd|cc|inc|limited|proprietary|the|t/a|trading as)\b|[().,&'\"-]")


def normalise_name(s: Optional[str]) -> str:
    return " ".join(_LEGAL.sub(" ", (s or "").lower()).split())


def normalise_vat(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")


# ---------- supplier matching ---------- #

def match_supplier(name: Optional[str], vat_number: Optional[str], contacts: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalised exact first, then VAT number, then fuzzy. Returns
    {contact_id, name, method, score} or None. Contacts carry name,
    name_normalised, vat_number."""
    if not contacts:
        return None
    target = normalise_name(name)
    if target:
        for c in contacts:
            if normalise_name(c.get("name")) == target:
                return {"contact_id": c["_id"], "name": c["name"], "method": "exact", "score": 1.0}
    vat = normalise_vat(vat_number)
    if len(vat) >= 10:
        for c in contacts:
            if normalise_vat(c.get("vat_number")) == vat:
                return {"contact_id": c["_id"], "name": c["name"], "method": "vat_number", "score": 0.95}
    if not target:
        return None
    best, best_score = None, 0.0
    for c in contacts:
        cand = normalise_name(c.get("name"))
        if not cand:
            continue
        score = difflib.SequenceMatcher(None, target, cand).ratio()
        # A contained name ("myplas" in "myplas pty ltd") is a strong signal.
        if target in cand or cand in target:
            score = max(score, 0.86)
        if score > best_score:
            best, best_score = c, score
    if best and best_score >= 0.80:
        return {"contact_id": best["_id"], "name": best["name"], "method": "fuzzy", "score": round(best_score, 2)}
    return None


# ---------- dates ---------- #

def _parse(d: Any) -> Optional[date]:
    if d is None or d == "":
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def terms_from_xero_contact(contact: Optional[Dict[str, Any]]) -> Optional[Tuple[str, int]]:
    """Xero bill terms to the Brief 13 vocabulary. DAYSAFTERBILLDATE and
    OFCURRENTMONTH are days from invoice; DAYSAFTERBILLMONTH and
    OFFOLLOWINGMONTH are statement terms."""
    if not contact or contact.get("bill_terms_day") is None:
        return None
    day = int(contact["bill_terms_day"])
    t = (contact.get("bill_terms_type") or "").upper()
    if t in ("DAYSAFTERBILLDATE", "OFCURRENTMONTH"):
        return ("days_from_invoice", day)
    if t in ("DAYSAFTERBILLMONTH", "OFFOLLOWINGMONTH"):
        return ("days_from_statement", 30 if day <= 31 else day)
    return None


def compute_due_date(invoice_date: Any, payment_state: str, paid_date: Any,
                     profile: Optional[Dict[str, Any]], contact: Optional[Dict[str, Any]]) -> Tuple[Optional[str], str]:
    """(due_date ISO, source). Paid: the paid date. Unpaid: supplier profile
    terms, else the Xero contact's bill terms, else 30 days from invoice."""
    inv = _parse(invoice_date)
    if payment_state in ("paid_cash", "paid_card"):
        pd = _parse(paid_date)
        return (pd.isoformat() if pd else None), "paid_date"
    if not inv:
        return None, "none"
    terms = None
    source = "default_30"
    if profile and profile.get("payment_terms_type"):
        t = profile["payment_terms_type"]
        d = int(profile.get("payment_terms_days") or 0)
        if t == "on_receipt":
            return inv.isoformat(), "profile"
        terms, source = (t, d), "profile"
    if terms is None:
        ct = terms_from_xero_contact(contact)
        if ct:
            terms, source = ct, "xero_contact"
    if terms is None:
        terms = ("days_from_invoice", 30)
    return _due(inv, terms[0], terms[1]).isoformat(), source


def _due(inv: date, kind: str, days: int) -> date:
    """days_from_invoice: invoice date + days. days_from_statement: the statement is the last
    day of the invoice month; due that many days after it."""
    if kind == "days_from_statement":
        nxt = (inv.replace(day=28) + timedelta(days=4)).replace(day=1)
        return (nxt - timedelta(days=1)) + timedelta(days=days)
    return inv + timedelta(days=days)


# ---------- tax type default (Brief 19) ---------- #

def default_tax_type(*, notional: bool, profile: Optional[Dict[str, Any]], account: Optional[Dict[str, Any]],
                     standard: Optional[str], capital: Optional[str], notional_type: Optional[str]) -> Optional[str]:
    """In order: notional setting for notional suppliers, the supplier
    profile, the capital setting for a fixed asset account, the standard
    setting, and only then the account's Xero default. Nothing hardcoded."""
    if notional and notional_type:
        return notional_type
    if profile and profile.get("default_tax_type"):
        return profile["default_tax_type"]
    if account and (account.get("type") or "").upper() == "FIXED" and capital:
        return capital
    if standard:
        return standard
    if account and account.get("tax_type"):
        return account["tax_type"]
    return None


# ---------- money ---------- #

def round2(x: float) -> float:
    return float(f"{x:.2f}")


def compute_totals(lines: List[Dict[str, Any]], amounts_are: str, tax_rates: Dict[str, float]) -> Dict[str, Any]:
    """Line and bill totals from quantity x unit_amount, with the tax rate per
    line's tax_type (percent). Lines without a known tax type carry no tax.
    Returns updated lines plus total_excl, total_tax, total_incl."""
    out_lines = []
    total_excl = total_tax = 0.0
    for ln in lines:
        qty = float(ln.get("quantity") or 0)
        unit = float(ln.get("unit_amount") or 0)
        amount = round2(qty * unit)
        rate_pct = tax_rates.get(ln.get("tax_type") or "", 0.0)
        rate = rate_pct / 100.0
        if amounts_are == "inclusive":
            incl = amount
            excl = round2(incl / (1 + rate)) if rate else incl
            tax = round2(incl - excl)
        else:
            excl = amount
            tax = round2(excl * rate)
            incl = round2(excl + tax)
        out_lines.append({**ln, "quantity": qty, "unit_amount": unit, "line_total": amount, "line_excl": excl, "line_tax": tax, "line_incl": incl, "tax_rate_used": rate_pct})
        total_excl += excl
        total_tax += tax
    return {"lines": out_lines, "total_excl": round2(total_excl), "total_tax": round2(total_tax), "total_incl": round2(total_excl + total_tax)}


# ---------- currency ---------- #

HOME_CURRENCY = "ZAR"
# Pegged 1:1 to the rand: entered as rand, no conversion, no flag.
PAR_CURRENCIES = {"NAD": "ZAR"}


def normalise_currency(code: Optional[str]) -> Tuple[str, Optional[str]]:
    """(currency to book, original code if it was converted at par)."""
    c = (code or HOME_CURRENCY).upper().strip()
    if c in PAR_CURRENCIES:
        return PAR_CURRENCIES[c], c
    return c, None


def is_foreign(code: Optional[str]) -> bool:
    c, _ = normalise_currency(code)
    return c != HOME_CURRENCY


# ---------- validation ---------- #

BLOCK = "block"
WARN = "warn"


def submit_blocks(bill: Dict[str, Any], business_unit_required: bool) -> List[Dict[str, Any]]:
    """Section 6.3 block rules that need no database. Duplicate checks are
    done by the route with the collection."""
    flags: List[Dict[str, Any]] = []

    def block(code: str, message: str):
        flags.append({"code": code, "level": BLOCK, "message": message})

    if not bill.get("documents"):
        block("no_document", "Upload the invoice or slip first.")
    if not bill.get("xero_contact_id") and not any(f.get("code") == "new_supplier" for f in bill.get("flags") or []):
        block("no_supplier", "Pick the supplier, or choose Supplier not listed.")
    if not (bill.get("invoice_number") or "").strip():
        block("no_invoice_number", "Invoice number is required. Use the slip or quote number if there is no invoice.")
    if not _parse(bill.get("invoice_date")):
        block("no_invoice_date", "Invoice date is required.")
    if not bill.get("lines") or not any(float(l.get("unit_amount") or 0) for l in bill["lines"]):
        block("no_total", "Total is required.")
    if bill.get("payment_state") in ("paid_cash", "paid_card") and not _parse(bill.get("paid_date")):
        block("no_paid_date", "Paid date is required when the bill is already paid.")
    if bill.get("payment_state") not in ("paid_cash", "paid_card", "unpaid"):
        block("no_payment_state", "Say whether it is paid already.")
    if business_unit_required and not bill.get("business_unit_option"):
        block("no_business_unit", "Choose the business unit this purchase was for.")
    if not (bill.get("description") or "").strip():
        block("no_description", "Say what it is for.")
    stated = bill.get("stated_total")
    if stated is not None and bill.get("total_incl") is not None:
        if abs(float(stated) - float(bill["total_incl"])) > 0.05:
            block("total_mismatch", f"Lines add up to {bill['total_incl']:.2f} but the total entered is {float(stated):.2f}.")
    return flags


# ---------- warn flags (section 6.3) ---------- #

# Warns an approver must clear explicitly before approving.
WARNS_REQUIRING_CLEAR = ("terms_deviation", "possible_duplicate", "duplicate_file")
LATE_PAID_DAYS = 2
LATE_UNPAID_DAYS = 7


def warn_flags(bill: Dict[str, Any], today: date, same_supplier_bills: Sequence[Dict[str, Any]],
               same_file_bills: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure part of the warn rules. The route supplies the candidate bills."""
    out: List[Dict[str, Any]] = []

    def warn(code: str, message: str, **extra):
        out.append({"code": code, "level": WARN, "message": message, **extra})

    inv = _parse(bill.get("invoice_date"))
    total = bill.get("total_incl")
    # possible_duplicate: same supplier, same total, invoice dates within 7 days.
    if inv and total is not None:
        for other in same_supplier_bills:
            if other.get("_id") == bill.get("_id") or other.get("status") == "void":
                continue
            od = _parse(other.get("invoice_date"))
            if od and abs((od - inv).days) <= 7 and other.get("total_incl") is not None and abs(float(other["total_incl"]) - float(total)) < 0.005:
                warn("possible_duplicate", f"Same supplier and total as {other.get('bill_number')} dated {other.get('invoice_date')}.", other_bill=other.get("bill_number"))
                break
    # duplicate_file: a document with the same sha256 on another bill.
    for other in same_file_bills:
        if other.get("_id") != bill.get("_id") and other.get("status") != "void":
            warn("duplicate_file", f"The same file is attached to {other.get('bill_number')}.", other_bill=other.get("bill_number"))
            break
    # vat_mismatch: stated VAT vs computed from the tax types on the lines.
    vm = vat_mismatch_flag(bill)
    if vm:
        out.append(vm)
    # terms_deviation: capturer or document gave a due date that differs from terms.
    if bill.get("due_date_locked") and bill.get("due_date") and bill.get("due_date_computed") and bill["due_date"] != bill["due_date_computed"]:
        warn("terms_deviation", f"Due {bill['due_date']} entered; supplier terms give {bill['due_date_computed']}.",
             entered=bill["due_date"], computed=bill["due_date_computed"])
    # late_capture: information for Pierre, never a block.
    if bill.get("payment_state") in ("paid_cash", "paid_card"):
        pd = _parse(bill.get("paid_date"))
        if pd and (today - pd).days > LATE_PAID_DAYS:
            warn("late_capture", f"Paid {pd.isoformat()}, captured {(today - pd).days} days later.")
    elif inv and (today - inv).days > LATE_UNPAID_DAYS:
        warn("late_capture", f"Invoice dated {inv.isoformat()}, captured {(today - inv).days} days later.")
    # future_date
    if inv and inv > today:
        warn("future_date", f"Invoice date {inv.isoformat()} is after today.")
    return out


def vat_mismatch_flag(bill: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Only meaningful once a line carries a tax type. An uncoded bill has
    nothing to compare, and a warning that fires on every bill teaches the
    approver to ignore warnings."""
    stated_vat = bill.get("stated_vat")
    if stated_vat is None or bill.get("total_tax") is None or bill.get("notional_vat"):
        return None
    if not any(ln.get("tax_type") for ln in bill.get("lines") or []):
        return None
    if abs(float(stated_vat) - float(bill["total_tax"])) > 0.05:
        rates = ", ".join(f"{ln.get('tax_type')} at {ln.get('tax_rate_used', 0)}%" for ln in bill.get("lines") or [])
        print(f"[bills] vat_mismatch on {bill.get('bill_number')}: document {float(stated_vat):.2f}, lines {float(bill['total_tax']):.2f} using {rates}", flush=True)
        return {"code": "vat_mismatch", "level": WARN,
                "message": f"Document shows VAT {float(stated_vat):.2f}, tax types on the lines give {float(bill['total_tax']):.2f} ({rates})."}
    return None


def approve_blocks(bill: Dict[str, Any], business_unit_required: bool) -> List[str]:
    """Why a bill cannot be approved yet. Empty means approve is allowed."""
    reasons: List[str] = []
    for f in bill.get("flags") or []:
        if f.get("level") == BLOCK:
            reasons.append(f.get("message") or f.get("code"))
        elif f.get("code") in WARNS_REQUIRING_CLEAR and not f.get("cleared_at"):
            reasons.append(f"Clear the {f.get('code').replace('_', ' ')} flag first.")
    if not bill.get("xero_contact_id"):
        reasons.append("Link the supplier to a Xero contact.")
    if not bill.get("lines"):
        reasons.append("The bill has no lines.")
    for i, ln in enumerate(bill.get("lines") or [], start=1):
        if not ln.get("account_code"):
            reasons.append(f"Line {i} has no account.")
        if not ln.get("tax_type"):
            reasons.append(f"Line {i} has no tax type.")
        if business_unit_required and not (ln.get("business_unit_option") or bill.get("business_unit_option")):
            reasons.append(f"Line {i} has no business unit.")
    return reasons


def profile_suggestion(bill: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What 'Save as default for this supplier' would write, and whether it
    differs from the profile. Offered after approval when coding changed."""
    first = (bill.get("lines") or [{}])[0]
    proposed = {
        "default_account_code": first.get("account_code"),
        # A notional supplier's tax type comes from the Xero setting, not the profile.
        "default_tax_type": None if (profile or {}).get("notional_vat") else first.get("tax_type"),
        "default_business_unit_option": first.get("business_unit_option") or bill.get("business_unit_option"),
    }
    current = {k: (profile or {}).get(k) for k in proposed}
    changed = {k: v for k, v in proposed.items() if v and v != current.get(k)}
    return {"proposed": proposed, "changed": changed, "offer": bool(changed)}
