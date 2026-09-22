import re
from datetime import datetime, timedelta

CURRENCY = re.compile(r'(AED|SAR|USD|EUR|INR|GBP|Dhs?\.?|درهم|[£€₹$])', re.I)
# Order matters: alternation is first-match-wins, so the multi-segment form is
# tried BEFORE the short one. Otherwise "CODR2026/HO/105" matches the short
# alternative at "CODR2026" and the rest of the reference is thrown away —
# which silently turns eight distinct payment vouchers into one.
REF_HINT = re.compile(r'([A-Z]+\d*(?:[-/][A-Z]+\d*)*[-/]\d+(?:[-/]\d+)*|[A-Z]{2,}[-/#]?\d{3,}|\d{8}-\d{4,}|GKVW#\S+|PHUB\d+|(?<![\d.,])\d(?:\.\d+)?[Ee][+-]\d{2,}(?![\d.,])|(?<![\d.,])0\d{6,}(?![\d.,])|(?<![\d.,/-])\d{2,4}/\d{2,4}/\d{3,}(?:/\d+)*(?![\d.,])|(?<![\d.,])\d{9,}(?![\d.,])|(?:INVOICE|INV|BILL|VOUCHER|DOC|REF|NO)\s*(?:NO)?[.#:\s]*\d{3,})', re.I)

def parse_amount(val):
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
    s = s.replace(',', '').replace(' ', '').replace(' ', '').strip()
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
    if val is None:
        return None, ''
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d'), str(val)
    raw = str(val).strip()
    if not raw:
        return None, raw
    if isinstance(val, (int, float)) and not isinstance(val, bool) and 20000 < float(val) < 60000:
        try:
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

SCI_NOTATION = re.compile(r'^\s*\d(?:\.\d+)?[Ee][+-]\d{2,}\s*$')

# Statement exports leak their own encoding artefacts into cell text: Excel writes
# carriage returns as the literal _x000D_, and Zoho embeds HTML in detail cells.
_XML_ESCAPE = re.compile(r'_x00[0-9A-Fa-f]{2}_')
_HTML_BREAK = re.compile(r'</?(?:div|br|p|li|tr|td)[^>]*>', re.I)
_HTML_TAG = re.compile(r'<[^>]{0,200}>')

def clean_cell(text):
    """Strip export artefacts so a reference is not carried around with markup
    or an _x000D_ glued to it."""
    s = str(text or '')
    s = _XML_ESCAPE.sub(' ', s)
    s = _HTML_BREAK.sub('\n', s)
    s = _HTML_TAG.sub(' ', s)
    s = s.replace('&amp;', '&').replace('&nbsp;', ' ').replace('&#39;', "'")
    return s

# A row's own identity ends where its allocation narration begins. "PO0194 …
# Rs.3,832.40 for payment of INS/25-26/0151" is a credit note called PO0194 that
# settles INS/25-26/0151 — it is not called INS/25-26/0151.
ALLOCATION_PHRASE = re.compile(
    r'\b(?:for|from|against|towards?|adjusted\s+(?:against|with))\s+'
    r'(?:the\s+)?(?:payment|part[\s-]?payment|invoice|bill|settlement)s?\b'
    r'|\bon\s+account\s+of\b|\bpayment\s+of\b|\bin\s+excess\s+payments?\b', re.I)

def split_allocation(text):
    """(own, allocation) — text before the first allocation phrase, and after.

    References in the first part identify this row. References in the second
    identify other rows it settles; they are useful as links but must never be
    mistaken for this row's own reference."""
    s = clean_cell(text)
    m = ALLOCATION_PHRASE.search(s)
    if not m:
        return s, ''
    return s[:m.start()], s[m.start():]

def ref_candidates(text, limit=6):
    """(own, allocation) reference tokens found in a cell.

    `own` identifies this row and is what matching may use. Statements routinely
    carry more than one identifier for the same transaction — a bank reference
    and a voucher number, a PO number and a credit-note number — and the two
    sides of a reconciliation rarely quote the same one, so all of them are kept.

    `allocation` names the rows this one settles. It is recorded for audit and
    never matched on: a credit note that pays down an invoice is not that
    invoice, and treating it as one silently merges the two."""
    own_txt, alloc_txt = split_allocation(text)
    own, alloc = [], []
    for part, out in ((own_txt, own), (alloc_txt, alloc)):
        for m in REF_HINT.finditer(part):
            tok = norm_ref(m.group(0))
            if tok and tok not in out:
                out.append(tok)
                if len(out) >= limit:
                    break
    return own, [a for a in alloc if a not in own]

def unwrap_pdf_breaks(text):
    """PDF line-wrapping splits refs after a hyphen/slash ("SO-\n0800").
    Rejoin those so reference patterns can match across the break."""
    return re.sub(r'([-/])[ \t]*\n[ \t]*', r'\1', str(text or ''))

def norm_ref(val):
    if val is None:
        return ''
    return re.sub(r'\s+', '', str(val)).upper().strip('.,;:')

def looks_like_ref(val):
    if val is None:
        return False
    s = str(val)
    if parse_date(s)[0] is not None:
        return False
    return bool(REF_HINT.search(s))

REF_TYPE_PREFIX = [
    (re.compile(r'^(CRT|CN|CRN)\b', re.I), 'Credit Note'),
    (re.compile(r'^(RVT|RCT|RCPT|PMT|PYMT)\b', re.I), 'Payment'),
    (re.compile(r'^(DBN|DN)\b', re.I), 'Debit Note'),
]

def type_from_ref(ref):
    for rx, t in REF_TYPE_PREFIX:
        if rx.search(str(ref or '')):
            return t
    return None

_TYPE_CANON = [
    # returns before sales, so "Sales Return" is not read as a sale
    (re.compile(r'^(sales?\s*returns?|purchase\s*returns?|returns?)\b', re.I), 'Credit Note'),
    (re.compile(r'^(payments?\s*made|payments?|receipts?|rcpt|pmt)\b', re.I), 'Payment'),
    (re.compile(r'^(credits?\s*notes?|credits?|cn|crn)\b', re.I), 'Credit Note'),
    (re.compile(r'^(debits?\s*notes?|dn)\b', re.I), 'Debit Note'),
    (re.compile(r'^(bills?)\b', re.I), 'Bill'),
    (re.compile(r'^(invoices?|inv)\b', re.I), 'Invoice'),
    (re.compile(r'^(sales?\s*invoices?|tax\s*invoices?|sales?)\b', re.I), 'Invoice'),
]

# SAP posts a two-letter document type rather than a word. Left unmapped they
# are not recognised as payments, so an entire vendor ledger lands in the
# invoice lane and the payment comparison reads zero against our own — which
# is how a 303,757.05 "gap" appeared against a ledger holding 67 payments.
SAP_DOC_TYPES = {
    'RV': 'Invoice',      # billing document
    'RE': 'Invoice',      # gross vendor invoice
    'KR': 'Invoice',      # vendor invoice
    'DR': 'Invoice',      # customer invoice
    'DZ': 'Payment',      # customer payment
    'KZ': 'Payment',      # vendor payment
    'ZP': 'Payment',      # payment posting
    'DG': 'Credit Note',  # customer credit memo
    'KG': 'Credit Note',  # vendor credit memo
    'RA': 'Credit Note',  # invoice cancellation
    'AB': 'Journal',      # accounting document
    'SA': 'Journal',      # G/L account document
    'UE': 'Journal',      # data transfer / clearing
    'IN': 'Invoice',      # common short form in Gulf/India ERP ledgers
}
# 'RV' is genuinely ambiguous — a billing document in SAP, a receipt voucher in
# several other ERPs. It is left mapped to Invoice because the sign rule above
# rescues it: a receipt lands in the credit column, arrives negative, and a
# negative on a document CODE is reclassified as a credit note rather than
# being trusted as an invoice.


def is_doc_code(value):
    """True when the type came from a document-type CODE rather than a word.

    It matters for signs. "Bill" is a claim about direction and a negative one
    is an extraction error. 'RV' is only a posting category — SAP writes a
    reversal under the same code with the sign flipped — so there the sign is
    data, not a mistake."""
    return str(value or '').strip().upper() in SAP_DOC_TYPES


def canon_type(value):
    """Map a statement's own type-column wording onto the vocabulary the rest of
    the engine and the UI use: 'Payment Made' -> Payment, 'Credits' -> Credit
    Note. Unrecognised wording passes through untouched rather than being
    guessed at."""
    s = str(value or '').strip()
    if not s:
        return s
    if s.upper() in SAP_DOC_TYPES:
        return SAP_DOC_TYPES[s.upper()]
    for rx, t in _TYPE_CANON:
        if rx.match(s):
            return t
    return s

def infer_type(row_text):
    """Guess a row's type from its narration, used only when the statement has
    no type column of its own.

    Plurals matter more than they look: SAP B1 writes "Incoming Payments -
    C000124", and `\\bpayment\\b` does not match "Payments". Every payment in
    the ledger was then typed as an invoice and kept a positive sign."""
    t = row_text.lower()
    if re.search(r'\bpayments?\b|\bpmt\b|\breceipts?\b|\btrf\b|\btransfers?\b'
                 r'|\bincoming\s+payments?\b|\bremittances?\b', t):
        return 'Payment'
    if re.search(r'credits?\s*notes?|\bcn\b|\bcrn\b|\bsales?\s*returns?\b', t):
        return 'Credit Note'
    if re.search(r'debits?\s*notes?|\bdn\b', t):
        return 'Debit Note'
    if re.search(r'\bbills?\b', t):
        return 'Bill'
    return 'Invoice'

TOTAL_ROW = re.compile(r'\b(sub\s*-?\s*totals?|totals?|closing\s+balance|balance\s+(c/?f|carried)|grand\s+totals?|net\s+(balance|totals?|amount\s+due))\b', re.I)
OPENING_ROW = re.compile(r'\b(opening\s+balance|balance\s+(b/?f|brought|as\s+(on|at))|bal\s+b/?f)\b', re.I)

def is_total_row(cells):
    return bool(TOTAL_ROW.search(' '.join(str(c) for c in cells if c is not None)))

def is_opening_row(cells):
    return bool(OPENING_ROW.search(' '.join(str(c) for c in cells if c is not None)))
