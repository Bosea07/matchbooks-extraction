"""Normalization layer — dates, amounts, references, transaction types.
The accuracy core: every reader feeds raw cells through these."""
import re
from datetime import datetime, timedelta

CURRENCY = re.compile(r'(AED|SAR|USD|EUR|INR|GBP|Dhs?\.?|درهم)', re.I)
REF_HINT = re.compile(r'([A-Z]{2,}[-/#]?\d{3,}|\d{8}-\d{4,}|GKVW#\S+|PHUB\d+|[A-Z]+\d*[-/]\d+([-/]\d+)*)', re.I)

def parse_amount(val):
    """'1,234.50' | '(500)' | 'AED 1.2' | '1234.50 DR' | 500.0 -> float | None"""
    if val is None:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = str(val).strip()
    if not s or s in ('-', '—', '–', 'nil', 'NIL'):
        return None
    neg = False
    if s.startswith('(') and s.endswith(')'):
        neg, s = True, s[1:-1]
    m = re.search(r'\b(DR|CR)\b\.?$', s, re.I)
    if m:
        if m.group(1).upper() == 'CR':
            neg = not neg
        s = s[:m.start()]
    s = CURRENCY.sub('', s)
    s = s.replace(',', '').replace(' ', '').replace(' ', '').strip()
    if s.startswith('-'):
        neg, s = not neg, s[1:]
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v

_DATE_PATTERNS = [
    ('%d-%m-%Y', re.compile(r'^\d{1,2}-\d{1,2}-\d{4}$')),
    ('%d/%m/%Y', re.compile(r'^\d{1,2}/\d{1,2}/\d{4}$')),
    ('%Y-%m-%d', re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$')),
    ('%Y/%m/%d', re.compile(r'^\d{4}/\d{1,2}/\d{1,2}$')),
    ('%d-%m-%y', re.compile(r'^\d{1,2}-\d{1,2}-\d{2}$')),
    ('%d/%m/%y', re.compile(r'^\d{1,2}/\d{1,2}/\d{2}$')),
    ('%d %b %Y', re.compile(r'^\d{1,2} [A-Za-z]{3} \d{4}$')),
    ('%d %B %Y', re.compile(r'^\d{1,2} [A-Za-z]{4,9} \d{4}$')),
    ('%b %d, %Y', re.compile(r'^[A-Za-z]{3} \d{1,2}, \d{4}$')),
    ('%d-%b-%Y', re.compile(r'^\d{1,2}-[A-Za-z]{3}-\d{4}$')),
    ('%d-%b-%y', re.compile(r'^\d{1,2}-[A-Za-z]{3}-\d{2}$')),
]

def parse_date(val):
    """Returns (iso_string, raw_string) or (None, raw). Handles datetimes,
    Excel serials and the common written formats. DD/MM assumed over MM/DD (UAE)."""
    if val is None:
        return None, ''
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d'), str(val)
    raw = str(val).strip()
    if not raw:
        return None, raw
    if isinstance(val, (int, float)) and not isinstance(val, bool) and 20000 < float(val) < 60000:
        try:  # Excel serial
            d = datetime(1899, 12, 30) + timedelta(days=float(val))
            return d.strftime('%Y-%m-%d'), raw
        except Exception:
            pass
    s = re.sub(r'\s+', ' ', raw)
    for fmt, rx in _DATE_PATTERNS:
        if rx.match(s):
            try:
                return datetime.strptime(s, fmt).strftime('%Y-%m-%d'), raw
            except ValueError:
                continue
    return None, raw

def norm_ref(val):
    """Canonical reference: uppercase, no whitespace, strip trailing punctuation."""
    if val is None:
        return ''
    s = re.sub(r'\s+', '', str(val)).upper().strip('.,;:')
    return s

def looks_like_ref(val):
    if val is None:
        return False
    return bool(REF_HINT.search(str(val)))

def infer_type(row_text):
    t = row_text.lower()
    if re.search(r'\bpayment\b|\bpmt\b|\breceipt\b|\btrf\b|\btransfer\b', t):
        return 'Payment'
    if re.search(r'credit\s*note|\bcn\b|\bcrn\b', t):
        return 'Credit Note'
    if re.search(r'debit\s*note|\bdn\b', t):
        return 'Debit Note'
    if re.search(r'\bbill\b', t):
        return 'Bill'
    return 'Invoice'

TOTAL_ROW = re.compile(r'\b(sub\s*-?\s*total|total|closing\s+balance|balance\s+(c/?f|carried)|grand\s+total|net\s+(balance|total|amount\s+due))\b', re.I)
OPENING_ROW = re.compile(r'\b(opening\s+balance|balance\s+(b/?f|brought)|bal\s+b/?f)\b', re.I)

def is_total_row(cells):
    txt = ' '.join(str(c) for c in cells if c is not None)
    return bool(TOTAL_ROW.search(txt))

def is_opening_row(cells):
    txt = ' '.join(str(c) for c in cells if c is not None)
    return bool(OPENING_ROW.search(txt))
