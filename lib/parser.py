"""Grid -> records. Header detection, column mapping, row extraction,
confidence scoring and the document-totals self-check."""
import re
from .normalize import (parse_amount, parse_date, norm_ref, looks_like_ref,
                        infer_type, is_total_row, is_opening_row)

HEADER_SYNONYMS = {
    'date':   ['date', 'doc date', 'document date', 'txn date', 'transaction date',
               'invoice date', 'bill date', 'posting date', 'entry date'],
    'ref':    ['reference', 'ref', 'ref no', 'ref#', 'reference number', 'invoice', 'invoice no',
               'invoice #', 'inv no', 'bill no', 'bill number', 'document', 'document no', 'doc no',
               'voucher', 'voucher no', 'number', 'transaction#', 'transaction no', 'particulars ref'],
    'type':   ['type', 'transaction type', 'doc type', 'document type', 'txn type', 'transaction'],
    'debit':  ['debit', 'debits', 'debit amount', 'dr', 'dr amount', 'invoice amount', 'charges'],
    'credit': ['credit', 'credits', 'credit amount', 'cr', 'cr amount', 'payment amount', 'payments'],
    'amount': ['amount', 'amount aed', 'net amount', 'value', 'total', 'total amount',
               'amount (aed)', 'aed', 'gross amount', 'balance amount'],
    'desc':   ['description', 'narration', 'details', 'particulars', 'memo', 'remarks'],
}

def _match_header(cell):
    if cell is None:
        return None
    s = re.sub(r'[^a-z#() ]', ' ', str(cell).lower()).strip()
    s = re.sub(r'\s+', ' ', s)
    if not s:
        return None
    for key, names in HEADER_SYNONYMS.items():
        if s in names:
            return key
    for key, names in HEADER_SYNONYMS.items():
        for n in names:
            if len(s) > 2 and (s.startswith(n + ' ') or s.endswith(' ' + n) or n in s.split()):
                return key
    return None

def find_header(grid):
    """Scan the first 20 rows for the row that maps the most columns."""
    best_row, best_map, best_hits = -1, {}, 0
    for i, row in enumerate(grid[:20]):
        colmap, hits = {}, 0
        for j, cell in enumerate(row):
            key = _match_header(cell)
            if key and key not in colmap:
                colmap[key] = j
                hits += 1
        if hits > best_hits and ('ref' in colmap or 'amount' in colmap or ('debit' in colmap and 'credit' in colmap)):
            best_row, best_map, best_hits = i, colmap, hits
    return best_row, best_map

def _infer_columns(grid, start):
    """No usable header: vote per column on data shape."""
    from collections import Counter
    votes = {'date': Counter(), 'ref': Counter(), 'amount': Counter()}
    rows = [r for r in grid[start:start + 40] if any(c not in (None, '') for c in r)]
    width = max((len(r) for r in rows), default=0)
    for r in rows:
        for j in range(width):
            c = r[j] if j < len(r) else None
            if c in (None, ''):
                continue
            iso, _ = parse_date(c)
            if iso:
                votes['date'][j] += 1
            if looks_like_ref(c):
                votes['ref'][j] += 1
            if parse_amount(c) is not None and not iso:
                votes['amount'][j] += 1
    colmap = {}
    for key in ('date', 'ref'):
        if votes[key]:
            colmap[key] = votes[key].most_common(1)[0][0]
    if votes['amount']:
        for j, _ in sorted(votes['amount'].items(), key=lambda kv: -kv[1]):
            if j != colmap.get('ref') and j != colmap.get('date'):
                colmap['amount'] = j
                break
    return colmap

def parse_grid(grid, reader_meta=None):
    reader_meta = reader_meta or {}
    grid = [list(r) for r in grid if r is not None]
    header_row, colmap = find_header(grid)
    inferred = False
    if header_row < 0 or ('amount' not in colmap and not ('debit' in colmap and 'credit' in colmap)):
        inferred_map = _infer_columns(grid, header_row + 1 if header_row >= 0 else 0)
        if 'amount' in inferred_map:
            inferred = True
            for k, v in inferred_map.items():
                colmap.setdefault(k, v)
            if header_row < 0:
                header_row = -1
    start = header_row + 1

    def cell(row, key):
        j = colmap.get(key, -1)
        return row[j] if 0 <= j < len(row) else None

    records, warnings = [], []
    data_rows = invalid_rows = 0
    doc_total = None
    for idx, row in enumerate(grid[start:], start=start):
        if not any(c not in (None, '') for c in row):
            continue
        if is_opening_row(row):
            warnings.append(f'Row {idx + 1}: opening-balance row skipped')
            continue
        if is_total_row(row):
            amts = [parse_amount(c) for c in row]
            amts = [a for a in amts if a is not None]
            if amts:
                doc_total = amts[-1]
            continue
        data_rows += 1
        # amount
        if 'debit' in colmap or 'credit' in colmap:
            deb = parse_amount(cell(row, 'debit')) or 0.0
            cred = parse_amount(cell(row, 'credit')) or 0.0
            amount = deb - cred if (deb or cred) else None
        else:
            amount = parse_amount(cell(row, 'amount'))
            if amount is None:
                # column shift (e.g. wrapped type text) — take the right-most
                # parseable amount that isn't the date or ref cell
                for j in range(len(row) - 1, -1, -1):
                    if j in (colmap.get('date'), colmap.get('ref')):
                        continue
                    v = parse_amount(row[j])
                    if v is not None and parse_date(row[j])[0] is None:
                        amount = v
                        break
        # reference
        raw_ref = cell(row, 'ref')
        if raw_ref in (None, '') :
            for c in row:
                if looks_like_ref(c) and parse_amount(c) is None:
                    raw_ref = c
                    break
        ref = norm_ref(raw_ref)
        if amount is None or not ref:
            invalid_rows += 1
            continue
        iso, raw_date = parse_date(cell(row, 'date'))
        ttype = cell(row, 'type')
        ttype = str(ttype).strip() if ttype not in (None, '') else infer_type(' '.join(str(c) for c in row if c is not None))
        records.append({
            'ref': ref,
            'refRaw': str(raw_ref).strip(),
            'date': raw_date or (iso or ''),
            'dateISO': iso,
            'type': ttype,
            'amount': round(amount, 2),
            'row': idx + 1,
        })

    # ---- confidence -----------------------------------------------------
    conf = (len(records) / data_rows) if data_rows else 0.0
    if 'date' not in colmap:
        conf -= 0.10
        warnings.append('No date column detected')
    if inferred:
        conf -= 0.10
        warnings.append('Headers not found — columns inferred from data shape')
    totals_check = None
    if doc_total is not None and records:
        s = round(sum(r['amount'] for r in records), 2)
        ok = abs(s - doc_total) <= max(1.0, abs(doc_total) * 0.001)
        totals_check = {'documentTotal': doc_total, 'extractedSum': s, 'ok': ok}
        if ok:
            conf = min(1.0, conf + 0.05)
        else:
            conf -= 0.15
            warnings.append(f'Extracted sum {s} != document total {doc_total}')
    if reader_meta.get('scanned'):
        conf = 0.0
        warnings.append('PDF appears scanned (no extractable text)')
    conf = max(0.0, min(1.0, conf))

    meta = {
        'headerRow': header_row,
        'totalRows': len(grid),
        'dateCol': colmap.get('date', -1),
        'refCol': colmap.get('ref', -1),
        'typeCol': colmap.get('type', -1),
        'debitCol': colmap.get('debit', -1),
        'creditCol': colmap.get('credit', -1),
        'amountCol': colmap.get('amount', -1),
        'dataRows': data_rows,
        'invalidRows': invalid_rows,
        'confidence': round(conf, 3),
        'warnings': warnings,
        'totalsCheck': totals_check,
    }
    meta.update({k: v for k, v in reader_meta.items() if k in ('sheet', 'reader', 'tables', 'textPages', 'scanned')})
    return records, meta
