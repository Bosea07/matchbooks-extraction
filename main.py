"""MatchBooks extraction service.
POST /extract (multipart 'file', optional 'engine' field) -> {filename, records[], meta{}}
engine: auto (default) | parser | claude | verify
  auto   - deterministic parser; Claude fallback below the confidence threshold
  parser - deterministic only
  claude - Claude-first (vendor SOAs); parser as fallback if Claude unavailable/fails
  verify - run BOTH and cross-check; disagreements are flagged, never silent"""
import copy, datetime, hashlib, os, re, threading, time
from collections import OrderedDict
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from lib.readers import read_any
from lib.parser import parse_grid
from lib import claude_extract
from lib.reconcile import reconcile as run_reconcile
from lib.export_xlsx import build_workbook, to_bytes

MAX_BYTES = int(os.environ.get('MAX_FILE_MB', '10')) * 1024 * 1024
CONF_THRESHOLD = float(os.environ.get('CONFIDENCE_THRESHOLD', '0.75'))

app = FastAPI(title='matchbooks-extraction', version='2.22.0')
app.add_middleware(CORSMiddleware,
                   allow_origins=os.environ.get('ALLOWED_ORIGINS', '*').split(','),
                   allow_methods=['*'], allow_headers=['*'], expose_headers=['Server-Timing', 'X-Extract-Cache'])


@app.middleware('http')
async def timing(request: Request, call_next):
    """Every response carries its duration (Server-Timing) and is logged, so slow steps show in Railway."""
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    response.headers['Server-Timing'] = f'app;dur={ms:.0f}'
    if request.url.path != '/health':
        print(f'[timing] {request.method} {request.url.path} {response.status_code} {ms:.0f} ms', flush=True)
    return response


# ---- extraction cache ----------------------------------------------------
# The same file re-uploaded (a re-run, a second reconciliation, a page reload) is answered from memory instead of
# being read again by Claude. Keyed by the file's bytes and the engine; only successful reads are kept; lost on restart.
_CACHE_MAX = int(os.environ.get('EXTRACT_CACHE_SIZE', '64'))
_cache: 'OrderedDict[str, dict]' = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
        return copy.deepcopy(hit) if hit is not None else None


def _cache_put(key, value):
    if _CACHE_MAX <= 0:
        return
    with _cache_lock:
        _cache[key] = copy.deepcopy(value)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)

@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'matchbooks-extraction',
            'version': '2.22.0',
            'claudeFallback': claude_extract.available(),
            'time': datetime.datetime.now(datetime.timezone.utc).isoformat()}

def _run_parser(filename, data):
    grid, reader_meta = read_any(filename, data)
    records, meta = parse_grid(grid, reader_meta)
    meta['engine'] = 'parser'
    return records, meta

def _base_cols(meta):
    return {k: meta.get(k, -1) for k in
            ('headerRow', 'totalRows', 'dateCol', 'refCol', 'typeCol',
             'debitCol', 'creditCol', 'amountCol')}

def _ai_failure(warn):
    """A short, actionable reason for a failed AI read (shown to the user)."""
    w = str(warn)
    if 'authentication_error' in w or 'invalid x-api-key' in w or ' 401' in w:
        return 'The AI reader could not sign in: ANTHROPIC_API_KEY on the extraction service is invalid or revoked — set a new key in Railway'
    if 'password protected' in w.lower():
        return 'The PDF is password-protected — open it, Print → Save as PDF, and upload the unlocked copy'
    if 'not_found_error' in w or 'model:' in w:
        return 'The AI model configured for extraction is no longer available — update CLAUDE_MODEL in Railway'
    if 'rate_limit' in w or 'overloaded' in w or ' 429' in w or ' 529' in w:
        return 'The AI reader is busy — try again in a minute'
    return f'The AI reader failed: {w[:300]}'


def _keyset(records):
    return {(r['ref'], round(float(r['amount']), 2)) for r in records}

@app.post('/extract')
async def extract(file: UploadFile = File(...), engine: str = Form('auto')):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, detail=f'File exceeds {MAX_BYTES // (1024*1024)} MB limit')
    if not data:
        raise HTTPException(400, detail='Empty file')
    filename = file.filename or 'upload'
    engine = (engine or 'auto').lower()
    if engine not in ('auto', 'parser', 'claude', 'verify'):
        raise HTTPException(400, detail="engine must be auto | parser | claude | verify")

    key = f'{engine}:{hashlib.sha256(data).hexdigest()}'
    cached = _cache_get(key)
    if cached is not None:
        cached['filename'] = filename
        cached.setdefault('meta', {})['cached'] = True
        return cached
    # Parsing and the Claude call block; run them on a worker thread so several files (and /reconcile, /health)
    # are served at the same time instead of queueing behind one PDF.
    resp = await run_in_threadpool(_extract_uncached, filename, data, engine)
    if resp and resp.get('records'):
        _cache_put(key, resp)
    return resp


def _extract_uncached(filename, data, engine):
    # ---- deterministic pass (needed by every mode except pure-claude success)
    p_records, p_meta, p_error = [], {}, None
    try:
        p_records, p_meta = _run_parser(filename, data)
    except ValueError as e:
        p_error = str(e)
        if 'Unsupported file type' in p_error or 'Legacy .doc' in p_error:
            raise HTTPException(415, detail=p_error)
    except Exception as e:
        p_error = f'Parser failed: {e}'

    def parser_response(extra_warn=None):
        if not p_records:
            detail = p_error or 'No transactions could be extracted from this file'
            if not claude_extract.available():
                detail += ' (AI fallback not configured — set ANTHROPIC_API_KEY to enable)'
            elif extra_warn:
                # The AI read was the only hope for this file; say why it failed instead of hiding it.
                detail += f'. {_ai_failure(extra_warn)}'
            raise HTTPException(422, detail=detail)
        if extra_warn:
            p_meta.setdefault('warnings', []).append(extra_warn)
        if p_meta.get('confidence', 0) < CONF_THRESHOLD:
            p_meta.setdefault('warnings', []).append(
                f"Low confidence ({p_meta.get('confidence')}) — review before reconciling")
            p_meta['needsReview'] = True
        return {'filename': filename, 'records': p_records, 'meta': p_meta}

    def claude_response():
        result = claude_extract.extract(filename, data)
        if not result:
            return None
        c_records, c_meta = result
        out = {**_base_cols(p_meta), **c_meta,
               'parserConfidence': p_meta.get('confidence', 0.0),
               'parserWarnings': p_meta.get('warnings', []),
               'parserError': p_error}
        return {'filename': filename, 'records': c_records, 'meta': out}

    # ---- mode routing -------------------------------------------------
    if engine == 'parser':
        return parser_response()

    if engine == 'claude':
        if claude_extract.available():
            try:
                resp = claude_response()
                if resp:
                    return resp
            except Exception as e:
                return parser_response(f'Claude extraction failed ({e}) — deterministic result returned')
        return parser_response('AI extraction requested but ANTHROPIC_API_KEY is not configured')

    if engine == 'verify':
        if not claude_extract.available():
            return parser_response('Verify mode requested but ANTHROPIC_API_KEY is not configured')
        try:
            c_resp = claude_response()
        except Exception as e:
            return parser_response(f'Verify mode: Claude failed ({e}) — deterministic result returned')
        if not c_resp:
            return parser_response('Verify mode: Claude unavailable')
        pk, ck = _keyset(p_records), _keyset(c_resp['records'])
        agree = pk & ck
        verification = {
            'agreementRate': round(len(agree) / max(len(pk | ck), 1), 3),
            'agreedRows': len(agree),
            'parserOnly': sorted([f'{r} {a}' for r, a in (pk - ck)])[:20],
            'claudeOnly': sorted([f'{r} {a}' for r, a in (ck - pk)])[:20],
        }
        # prefer the deterministic result when both fully agree; else Claude's
        # richer read wins but the response is flagged for review
        if verification['agreementRate'] >= 0.999 and p_records:
            resp = parser_response()
            resp['meta']['engine'] = 'verified'
            resp['meta']['confidence'] = 1.0
        else:
            resp = c_resp
            resp['meta']['engine'] = 'claude+review'
            resp['meta']['needsReview'] = True
            resp['meta'].setdefault('warnings', []).append(
                f"Parser and Claude disagree on {len(pk ^ ck)} rows — review before reconciling")
        resp['meta']['verification'] = verification
        return resp

    # ---- auto (default): parser first, Claude below threshold ---------
    confident = p_records and p_meta.get('confidence', 0) >= CONF_THRESHOLD
    if not confident and claude_extract.available():
        try:
            resp = claude_response()
            if resp:
                return resp
        except Exception as e:
            p_meta.setdefault('warnings', []).append(f'Claude fallback failed: {e}')
            if not p_records:
                return parser_response(f'Claude fallback failed: {e}')
    return parser_response()


from pydantic import BaseModel
from typing import Optional

class ReconcileBody(BaseModel):
    vendorTransactions: list
    zohoTransactions: list
    tolerance: Optional[float] = None
    dateWindow: Optional[int] = None
    enableCombos: Optional[bool] = True
    # From /extract meta.statementDate. On an open-items statement it is the
    # day the balances were struck; anything after it cannot be on the page.
    vendorStatementDate: Optional[str] = None
    # The vendor's currency as the platform knows it (its Zoho contact) or as
    # the user selected it; our books' currency (the entity's base currency).
    # A currency printed on the statement itself still wins over these.
    vendorCurrency: Optional[str] = None
    zohoCurrency: Optional[str] = None
    # Vendor currency per ONE unit of ours — e.g. 23.9 for INR against AED.
    # Optional: pegged pairs (AED/USD/SAR) need none, and with enough rows
    # matched by reference the engine infers it.
    fxRate: Optional[float] = None

def _counterparty_warning(vendor_rows, zoho_rows):
    """Are these two documents even about the same vendor?

    Reconciling a statement from one supplier against the ledger of another
    produces a tidy-looking result in which every line is an exception — the
    most misleading output this service can emit, because nothing about it
    looks broken. Two documents for the same account always share at least a
    few reference tokens. Zero overlap across dozens of rows does not happen
    by chance."""
    def tokens(rows):
        out = set()
        for r in rows:
            for t in (r.get('refAliases') or [r.get('ref')]):
                t = str(t or '').strip().upper()
                if t and not t.startswith('~') and len(t) >= 4:
                    out.add(t)
                    for part in re.split(r'[-/]', t):
                        if len(part) >= 4:
                            out.add(part)
        return out
    v, z = tokens(vendor_rows), tokens(zoho_rows)
    if len(v) < 5 or len(z) < 5:
        return None                      # too little to judge
    if v & z:
        return None
    return ('The two files share no reference token at all across '
            f'{len(vendor_rows)} vendor row(s) and {len(zoho_rows)} of our own. '
            'That normally means they are statements for two DIFFERENT '
            'counterparties, in which case every line below is an exception '
            'and the reconciliation is meaningless. Check the vendor on each '
            'document before using this result.')


@app.post('/reconcile')
def reconcile_endpoint(body: ReconcileBody):
    if not isinstance(body.vendorTransactions, list) or not isinstance(body.zohoTransactions, list):
        raise HTTPException(400, detail='vendorTransactions and zohoTransactions must be arrays')
    if not body.vendorTransactions and not body.zohoTransactions:
        raise HTTPException(400, detail='Both transaction lists are empty')
    warn = _counterparty_warning(body.vendorTransactions, body.zohoTransactions)
    try:
        result = run_reconcile(
            body.vendorTransactions, body.zohoTransactions,
            tolerance=body.tolerance if body.tolerance is not None else 1.0,
            date_window=body.dateWindow if body.dateWindow is not None else 7,
            enable_combos=bool(body.enableCombos),
            vendor_statement_date=body.vendorStatementDate,
            vendor_currency=body.vendorCurrency, zoho_currency=body.zohoCurrency,
            fx_rate=body.fxRate)
    except AssertionError as e:
        raise HTTPException(500, detail=f'Reconciliation invariant failed: {e}')
    if warn:
        result.setdefault('summary', {})['counterpartyMismatch'] = True
        result['summary'].setdefault('findings', []).insert(0, warn)
    return result


class ExportBody(ReconcileBody):
    """Same payload as /reconcile, plus labels for the workbook header.

    `result` may be supplied when the caller has already reconciled, so the
    export is guaranteed to be the same numbers the user is looking at on
    screen rather than a second, separately-computed run."""
    result: Optional[dict] = None
    vendorName: Optional[str] = None
    entity: Optional[str] = None
    period: Optional[str] = None
    currency: Optional[str] = None
    filename: Optional[str] = None


@app.post('/export')
def export_endpoint(body: ExportBody):
    """Four tabs: Summary, Zoho Extraction, Vendor SOA Extraction, Mapping."""
    if not body.vendorTransactions and not body.zohoTransactions:
        raise HTTPException(400, detail='Both transaction lists are empty')
    try:
        result = body.result or run_reconcile(
            body.vendorTransactions, body.zohoTransactions,
            tolerance=body.tolerance if body.tolerance is not None else 1.0,
            date_window=body.dateWindow if body.dateWindow is not None else 7,
            enable_combos=bool(body.enableCombos),
            vendor_statement_date=body.vendorStatementDate,
            vendor_currency=body.vendorCurrency, zoho_currency=body.zohoCurrency,
            fx_rate=body.fxRate)
        if not isinstance(result, dict) or 'results' not in result:
            raise HTTPException(400, detail="'result' must be a /reconcile response")
        wb = build_workbook(
            body.vendorTransactions, body.zohoTransactions, result,
            {'vendorName': body.vendorName, 'entity': body.entity,
             'period': body.period, 'currency': body.currency,
             'vendorCurrency': body.vendorCurrency, 'zohoCurrency': body.zohoCurrency,
             'engineVersion': app.version})
        data = to_bytes(wb)
    except AssertionError as e:
        raise HTTPException(500, detail=f'Reconciliation invariant failed: {e}')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f'Export failed: {e}')

    safe = ''.join(c for c in (body.filename or 'Reconciliation')
                   if c.isalnum() or c in ' -_')[:80].strip() or 'Reconciliation'
    return Response(
        content=data,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{safe}.xlsx"'})
