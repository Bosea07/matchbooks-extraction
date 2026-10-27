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
- If the same transactions repeat on continuation pages, list each transaction once.
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
    records, counts = _validate(payload.get('records', []))
    if not records:
        raise ValueError('Claude returned no valid records')
    warnings = []
    if counts['dropped']:
        warnings.append(f"{counts['dropped']} row(s) returned by the model were "
                        f"unreadable and discarded")
    if counts['unreferenced']:
        warnings.append(f"{counts['unreferenced']} row(s) had no reference and were "
                        f"kept with a synthetic id — matched by amount only")
    meta = {'engine': 'claude', 'model': MODEL,
            'documentTotal': payload.get('documentTotal'),
            'rowsReturned': counts['returned'], 'rowsKept': counts['kept'],
            'invalidRows': counts['dropped'],
            'unreferencedRows': counts['unreferenced'],
            'rowsAccountedFor': counts['kept'] + counts['dropped'] == counts['returned'],
            'warnings': warnings,
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
    """Validate Claude's rows under the same rule as the parser: nothing is
    discarded silently.

    This used to `continue` past any row without a reference — the exact bug
    fixed in the deterministic parser, where unreferenced rows are kept with a
    synthetic '~ROW' id and reported. A statement line with real money on it
    and no readable reference is still money, and dropping it produced totals
    that looked clean and were short."""
    from .normalize import norm_ref
    out, dropped, unreferenced = [], 0, 0
    for i, r in enumerate(rows[:MAX_ROWS]):
        try:
            ref = norm_ref(r.get('ref'))
            amount = float(r.get('amount'))
        except (TypeError, ValueError):
            dropped += 1
            continue
        if not ref:
            if round(amount, 2) == 0.0:
                dropped += 1
                continue
            ref = f'~ROW{i + 1}'
            unreferenced += 1
        iso = r.get('dateISO')
        if iso and not re.match(r'^\d{4}-\d{2}-\d{2}$', str(iso)):
            iso = None
        out.append({'ref': ref, 'refRaw': str(r.get('ref', '')).strip(),
                    'date': str(r.get('date', '') or ''), 'dateISO': iso,
                    'type': str(r.get('type', 'Invoice') or 'Invoice'),
                    'amount': round(amount, 2), 'row': None})
    return out, {'returned': len(rows), 'kept': len(out),
                 'dropped': dropped, 'unreferenced': unreferenced}
