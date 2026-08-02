"""Claude fallback — used when the deterministic parser's confidence is low.
Sends the document to the Claude API and validates the returned JSON strictly.
No ANTHROPIC_API_KEY in the environment -> returns None (feature off)."""
import base64, json, os, re

MODEL = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514')
MAX_ROWS = 2000

PROMPT = """You are an accounts-payable data extractor. The attached document is a vendor
statement of account (SOA). Extract EVERY transaction line into JSON.

Return ONLY a JSON object, no prose, exactly this shape:
{"records":[{"ref":"<reference/invoice number>","date":"<as written>","dateISO":"<YYYY-MM-DD or null>",
"type":"<Invoice|Credit Note|Payment|Debit Note|Bill>","amount":<number>}],
"documentTotal":<closing balance or total if stated, else null>}

Rules:
- amount: positive for invoices/debits, negative for credit notes and payments.
- Skip opening-balance rows, subtotal rows and the closing-total row (report the total in documentTotal instead).
- ref: keep the vendor's reference exactly as written.
- Do not invent rows. If a value is unreadable, omit that row rather than guessing."""

def available():
    return bool(os.environ.get('ANTHROPIC_API_KEY'))

def extract(filename: str, data: bytes, text_hint: str | None = None):
    """Returns (records, meta) or raises. None if no API key."""
    if not available():
        return None
    import anthropic
    client = anthropic.Anthropic()
    ext = filename.rsplit('.', 1)[-1].lower()
    content = [{'type': 'text', 'text': PROMPT}]
    if ext == 'pdf':
        content.insert(0, {'type': 'document',
                           'source': {'type': 'base64', 'media_type': 'application/pdf',
                                      'data': base64.b64encode(data).decode()}})
    else:
        body = text_hint if text_hint else data.decode('utf-8', errors='replace')
        content.append({'type': 'text', 'text': 'DOCUMENT CONTENT:\n' + body[:150000]})
    msg = client.messages.create(model=MODEL, max_tokens=8192,
                                 messages=[{'role': 'user', 'content': content}])
    raw = ''.join(b.text for b in msg.content if b.type == 'text')
    m = re.search(r'\{.*\}', raw, re.S)
    if not m:
        raise ValueError('Claude returned no JSON')
    payload = json.loads(m.group(0))
    records = _validate(payload.get('records', []))
    if not records:
        raise ValueError('Claude returned no valid records')
    meta = {'engine': 'claude', 'model': MODEL,
            'documentTotal': payload.get('documentTotal'),
            'confidence': 0.9}
    total = payload.get('documentTotal')
    if total is not None:
        s = round(sum(r['amount'] for r in records), 2)
        ok = abs(s - float(total)) <= max(1.0, abs(float(total)) * 0.001)
        meta['totalsCheck'] = {'documentTotal': total, 'extractedSum': s, 'ok': ok}
        if not ok:
            meta['confidence'] = 0.7
    return records, meta

def _validate(rows):
    from .normalize import norm_ref
    out = []
    for r in rows[:MAX_ROWS]:
        try:
            ref = norm_ref(r.get('ref'))
            amount = float(r.get('amount'))
        except (TypeError, ValueError):
            continue
        if not ref:
            continue
        iso = r.get('dateISO')
        if iso and not re.match(r'^\d{4}-\d{2}-\d{2}$', str(iso)):
            iso = None
        out.append({'ref': ref, 'refRaw': str(r.get('ref', '')).strip(),
                    'date': str(r.get('date', '') or ''), 'dateISO': iso,
                    'type': str(r.get('type', 'Invoice') or 'Invoice'),
                    'amount': round(amount, 2), 'row': None})
    return out
