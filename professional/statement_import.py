"""
Bank statement importer. No third-party services required.

Supports:
- CSV (auto-detects common header names across banks worldwide)
- OFX / QFX (open standard used by most US, CA, AU, NZ, UK banks)
- PDF (best-effort text extraction, line heuristics)
- Plain-text / tab-separated export

Returns a list of normalized dicts ready for pdb.create_transaction().
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from typing import List, Optional

log = logging.getLogger(__name__)

# Column-name heuristics. Check lowercased and stripped.
DATE_KEYS = {"date", "transaction date", "posting date", "posted date", "fecha", "fecha operacion",
             "fecha operación", "data", "datum", "date operation", "trans. date", "trans date",
             "transdate", "booking date", "value date"}
DESC_KEYS = {"description", "payee", "merchant", "memo", "details", "narrative", "concept",
             "concepto", "beschreibung", "libellé", "libelle", "descrizione", "descripcion",
             "descripción", "transaction", "name", "merchant name"}
AMOUNT_KEYS = {"amount", "value", "importe", "monto", "monto operacion", "betrag",
               "montant", "importo", "valor", "transaction amount"}
DEBIT_KEYS = {"debit", "debits", "withdrawal", "withdrawals", "cargo", "cargos", "retiro",
              "retiros", "débito", "debito", "money out", "paid out", "salida"}
CREDIT_KEYS = {"credit", "credits", "deposit", "deposits", "abono", "abonos",
               "crédito", "credito", "money in", "paid in", "entrada", "ingreso"}
CURRENCY_KEYS = {"currency", "ccy", "moneda", "divisa"}

# Keyword → category mapping for auto-categorization
CATEGORY_HINTS = {
    "groceries": ["walmart", "whole foods", "kroger", "safeway", "soriana", "chedraui",
                  "aldi", "lidl", "tesco", "sainsbury", "carrefour", "mercadona", "costco",
                  "trader joe", "publix", "heb", "oxxo"],
    "food_dining": ["starbucks", "mcdonald", "burger king", "subway", "chipotle",
                    "restaurant", "cafe", "coffee", "pizza", "doordash", "uber eats",
                    "rappi", "didi food", "grubhub", "deliveroo", "glovo", "just eat",
                    "kfc", "domino", "wendy"],
    "transportation": ["uber", "lyft", "cabify", "didi", "shell", "exxon", "chevron",
                       "bp ", "pemex", "metro", "mta", "parking", "gas ", "fuel",
                       "texaco", "mobil", "ticket"],
    "subscriptions": ["netflix", "spotify", "hulu", "disney", "amazon prime", "youtube",
                      "apple.com/bill", "icloud", "google", "adobe", "microsoft",
                      "dropbox", "github", "notion", "chatgpt", "openai", "claude",
                      "hbo", "paramount"],
    "utilities": ["electric", "water", "gas company", "verizon", "at&t", "comcast",
                  "xfinity", "telmex", "telcel", "movistar", "vodafone", "orange",
                  "internet", "cfe ", "telefon"],
    "rent_mortgage": ["rent", "renta", "mortgage", "hipoteca", "loyer", "miete",
                      "landlord", "apartment"],
    "health": ["pharmacy", "cvs", "walgreens", "doctor", "hospital", "clinic",
               "farmacia", "medical", "dental", "gym", "fitness"],
    "shopping": ["amazon", "ebay", "mercadolibre", "aliexpress", "shein", "zara",
                 "h&m", "uniqlo", "nike", "adidas", "apple store", "best buy",
                 "target"],
    "entertainment": ["cinema", "theater", "theatre", "cinépolis", "cinemex",
                      "amc", "regal", "concert", "steam", "playstation", "xbox",
                      "nintendo"],
    "travel": ["airbnb", "booking.com", "expedia", "hotel", "marriott", "hilton",
               "airlines", "american", "delta", "united", "aeromexico", "volaris",
               "ryanair", "easyjet", "lufthansa", "klm", "iberia"],
    "education": ["coursera", "udemy", "university", "school", "tuition",
                  "duolingo", "khan academy", "udacity"],
    "transfer": ["transfer", "transferencia", "wire", "zelle", "venmo",
                 "cashapp", "paypal transfer", "spei", "pix"],
    "income": ["salary", "payroll", "deposit", "nomina", "nómina",
               "payment received", "direct deposit", "stripe payout",
               "transferencia recibida"],
}


def _guess_category(merchant: str, description: str = "") -> str:
    """Simple keyword-based categorization — no AI cost."""
    text = f"{merchant} {description}".lower()
    if not text.strip():
        return "other"
    for cat, keywords in CATEGORY_HINTS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return "other"


def _parse_amount(raw) -> Optional[float]:
    """Parse amount strings from any common format (US, EU, LatAm)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Remove currency symbols & codes
    s = re.sub(r"[A-Za-z$€£¥₹¢₩₪₽₱₦﷼₺]+", "", s).strip()
    # Detect sign in parentheses (negative)
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("() ")
    # Detect explicit minus/plus
    if s.startswith("-"):
        negative = True
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()

    if not s:
        return None

    # Decide which is decimal separator (last non-digit run of length 1-2)
    s = s.replace(" ", "")
    if "," in s and "." in s:
        # Whichever is rightmost = decimal separator
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # If comma followed by 1-2 digits at the end → decimal
        if re.search(r",\d{1,2}$", s):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")

    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def _parse_date(raw) -> Optional[str]:
    """Return ISO YYYY-MM-DD from any common format."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Strip time portion if present
    s = re.split(r"[T ]", s, 1)[0]
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y",
        "%d/%m/%y", "%d-%m-%y", "%m/%d/%y", "%Y%m%d", "%d.%m.%Y", "%d.%m.%y",
        "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────
# CSV
# ─────────────────────────────────────────────────────────────
def _find_col(headers: list, candidates: set) -> Optional[int]:
    for i, h in enumerate(headers):
        key = (h or "").strip().lower()
        if key in candidates:
            return i
    # partial match
    for i, h in enumerate(headers):
        key = (h or "").strip().lower()
        for cand in candidates:
            if cand in key:
                return i
    return None


def parse_csv(content: str) -> List[dict]:
    """Parse CSV (any delimiter, any locale) → list of transaction dicts."""
    # Try to sniff delimiter
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
    except csv.Error:
        class _D:
            delimiter = ","
            quotechar = '"'
        dialect = _D()

    reader = csv.reader(io.StringIO(content), delimiter=dialect.delimiter,
                        quotechar=getattr(dialect, "quotechar", '"'))
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        return []

    # Find the header row — first row with at least 3 non-empty cells
    # containing at least one known keyword
    header_idx = 0
    for i, r in enumerate(rows[:10]):
        lowered = " ".join((c or "").lower() for c in r)
        if any(k in lowered for k in ("date", "fecha", "amount", "importe", "monto",
                                      "debit", "credit", "description", "descripcion",
                                      "payee", "datum", "betrag")):
            header_idx = i
            break
    headers = [h.strip() for h in rows[header_idx]]
    body = rows[header_idx + 1:]

    date_col = _find_col(headers, DATE_KEYS)
    desc_col = _find_col(headers, DESC_KEYS)
    amount_col = _find_col(headers, AMOUNT_KEYS)
    debit_col = _find_col(headers, DEBIT_KEYS)
    credit_col = _find_col(headers, CREDIT_KEYS)
    ccy_col = _find_col(headers, CURRENCY_KEYS)

    if date_col is None:
        # fallback: use first col
        date_col = 0
    if desc_col is None:
        # Pick the widest text column that's not date/amount
        for i in range(len(headers)):
            if i in {date_col, amount_col, debit_col, credit_col}:
                continue
            desc_col = i
            break

    out = []
    for row in body:
        if not row or len(row) <= date_col:
            continue
        iso_date = _parse_date(row[date_col])
        if not iso_date:
            continue
        description = (row[desc_col] if desc_col is not None and desc_col < len(row) else "").strip()

        # Amount logic
        amt = None
        is_income = False
        if amount_col is not None and amount_col < len(row):
            amt = _parse_amount(row[amount_col])
            if amt is not None and amt > 0:
                is_income = True  # positive in single-column files are usually deposits
                amount_value = amt
            elif amt is not None:
                amount_value = -amt  # flip sign for expenses
            else:
                continue
        else:
            # Two-column (debit/credit) format
            debit_val = _parse_amount(row[debit_col]) if debit_col is not None and debit_col < len(row) else None
            credit_val = _parse_amount(row[credit_col]) if credit_col is not None and credit_col < len(row) else None
            if credit_val and credit_val != 0:
                is_income = True
                amount_value = abs(credit_val)
            elif debit_val and debit_val != 0:
                amount_value = abs(debit_val)
            else:
                continue

        if amount_value is None or abs(amount_value) < 0.005:
            continue

        currency = ""
        if ccy_col is not None and ccy_col < len(row):
            currency = (row[ccy_col] or "").strip().upper()[:3]

        merchant = re.sub(r"\s+", " ", description)[:140]
        cat = "income" if is_income else _guess_category(merchant, description)

        out.append({
            "tx_date": iso_date,
            "merchant": merchant,
            "description": description[:500],
            "amount": abs(amount_value),
            "category": cat,
            "currency": currency or None,
        })
    return out


# ─────────────────────────────────────────────────────────────
# OFX / QFX
# ─────────────────────────────────────────────────────────────
OFX_TX_RE = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.DOTALL | re.IGNORECASE)
OFX_FIELD_RE = re.compile(r"<([A-Z0-9]+)>\s*([^<\r\n]*)", re.IGNORECASE)


def parse_ofx(content: str) -> List[dict]:
    """Parse OFX/QFX file → list of transactions."""
    out = []
    # Default currency
    ccy_match = re.search(r"<CURDEF>\s*([A-Z]{3})", content, re.IGNORECASE)
    default_ccy = ccy_match.group(1).upper() if ccy_match else ""

    for m in OFX_TX_RE.finditer(content):
        block = m.group(1)
        fields = {}
        for fm in OFX_FIELD_RE.finditer(block):
            key = fm.group(1).upper()
            val = fm.group(2).strip()
            if key and key not in fields:
                fields[key] = val

        dtposted = fields.get("DTPOSTED", "")[:8]
        iso = None
        if len(dtposted) == 8 and dtposted.isdigit():
            try:
                iso = datetime.strptime(dtposted, "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                iso = None
        if not iso:
            continue

        amt_raw = fields.get("TRNAMT", "")
        amt = _parse_amount(amt_raw)
        if amt is None or abs(amt) < 0.005:
            continue

        tx_type = (fields.get("TRNTYPE") or "").upper()
        is_income = amt > 0 or tx_type in {"CREDIT", "DEP", "DIRECTDEP", "INT", "DIV"}

        name = (fields.get("NAME") or fields.get("PAYEE") or "").strip()
        memo = (fields.get("MEMO") or "").strip()
        description = name or memo
        merchant = re.sub(r"\s+", " ", description)[:140]
        cat = "income" if is_income else _guess_category(merchant, memo)

        out.append({
            "tx_date": iso,
            "merchant": merchant,
            "description": (name + (" | " + memo if memo and memo != name else ""))[:500],
            "amount": abs(amt),
            "category": cat,
            "currency": default_ccy or None,
        })
    return out


# ─────────────────────────────────────────────────────────────
# PDF parser (best-effort)
# ─────────────────────────────────────────────────────────────
def parse_pdf(content: bytes) -> List[dict]:
    """
    Extract transactions from a PDF bank statement using line heuristics.

    PDFs are unstructured — we look for lines that contain a date + amount.
    Works on most major US/EU/LatAm bank PDF statements where each tx is
    on its own line. Falls back to returning [] if extraction fails.
    """
    try:
        from pdfminer.high_level import extract_text
    except Exception:
        log.warning("pdfminer.six not installed — cannot parse PDF")
        return []

    try:
        text = extract_text(io.BytesIO(content)) or ""
    except Exception as e:
        log.warning("PDF extraction failed: %s", e)
        return []

    if not text.strip():
        return []

    # Date patterns in many formats
    date_re = re.compile(
        r"\b("
        r"\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}"  # 12/03/2024, 12-3-24
        r"|\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}"   # 2024-03-12
        r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Ene|Abr|Ago|Dic)[a-z]*\s+\d{2,4}"
        r")\b",
        re.IGNORECASE,
    )
    # Amount at end of line: 1,234.56 / -1.234,56 / (123.45) optional currency
    amount_re = re.compile(
        r"(?P<sign>-)?\s*(?P<paren>\()?"
        r"(?:[A-Z$€£¥]{1,4}\s*)?"
        r"(?P<num>\d{1,3}(?:[.,\s]\d{3})*[.,]\d{2}|\d+[.,]\d{2})"
        r"(?P<paren2>\))?"
        r"\s*(?:[A-Z]{3})?\s*$"
    )

    out: List[dict] = []
    seen = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 8:
            continue
        dm = date_re.search(line)
        if not dm:
            continue
        am = amount_re.search(line)
        if not am:
            continue

        iso = _norm_date(dm.group(1))
        if not iso:
            continue
        amt = _to_float(am.group("num"))
        if amt is None or amt == 0:
            continue
        is_negative = bool(am.group("sign") or am.group("paren"))

        # Description: text between date and amount
        try:
            desc = line[dm.end():am.start()].strip(" \t-:|")
        except Exception:
            desc = line
        desc = re.sub(r"\s+", " ", desc)[:140] or "Statement entry"

        # Heuristic: lines with "credit", "deposit", "abono", "ingreso", "salary",
        # "payroll", "nomina" → income; otherwise expense.
        income_kw = ("credit", "deposit", "abono", "ingreso", "salary", "payroll",
                     "nomina", "nómina", "transfer in", "refund", "reembolso")
        is_income = any(k in desc.lower() for k in income_kw)
        if is_negative and is_income:
            is_income = False  # explicit minus wins

        cat = "income" if is_income else _guess_category(desc, "")

        key = (iso, round(abs(amt), 2), desc[:40].lower())
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "tx_date": iso,
            "merchant": desc,
            "description": line[:500],
            "amount": abs(amt),
            "category": cat,
            "currency": None,
        })

    log.info("PDF parser extracted %d transactions", len(out))
    return out


# ─────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────
def parse_statement(filename: str, content: bytes) -> List[dict]:
    """Auto-detect format from filename + content, return list of tx dicts."""
    name = (filename or "").lower()

    # PDF first — binary, can't decode as text
    if name.endswith(".pdf") or content[:5] == b"%PDF-":
        return parse_pdf(content)

    # Decode bytes with best-effort
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")

    if name.endswith((".ofx", ".qfx")) or "<OFX>" in text.upper():
        return parse_ofx(text)
    # default: CSV
    return parse_csv(text)
