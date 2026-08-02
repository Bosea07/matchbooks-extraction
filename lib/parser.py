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

def _map_row(row):
    colmap, hits = {}, 0
    for j, cell in enumerate(row):
        key = _match_header(cell)
        if key and key not in colmap:
            colmap[key] = j
            hits += 1
    return colmap, hits

def find_header(grid):
    """Scan the first 20 rows; also try merging each row with the next
    (two-line headers like DOCUMENT/DATE + DOCUMENT/NUMBER)."""
    best = (-1, {}, 0, 1)  # row, map, hits, span
    for i, row in enumerate(grid[:20]):
        candidates = [(row, 1)]
        if i + 1 < len(grid) and grid[i + 1]:
            nxt = grid[i + 1]
            width = max(len(row), len(nxt))
            merged = []
            for j in range(width):
                a = row[j] if j < len(row) else None
                b = nxt[j] if j < len(nxt) else None
                merged.append(((str(a) + ' ' if a else '') + (str(b) if b else '')).strip() or None)
            candidates.append((merged, 2))
        for cand, span in candidates:
            colmap, hits = _map_row(cand)
            usable = 'ref' in colmap or 'amount' in colmap or ('debit' in colmap and 'credit' in colmap)
            if usable and hits > best[2]:
                best = (i, colmap, hits, span)
    row, colmap, hits, span = best
    return (row + span - 1 if row >= 0 else -1), colmap


def _infer_columns(grid, start, known=None):
    known = known or {}
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
            if parse_amount(c) is not None and not iso and not looks_like_ref(c):
                votes['amount'][j] += 1
    colmap = dict(known)
    for key in ('date', 'ref'):
        if key not in colmap and votes[key]:
            colmap[key] = votes[key].most_common(1)[0][0]
    used = {v for v in colmap.values()}
    if votes['amount']:
        for j, _ in sorted(votes['amount'].items(), key=lambda kv: -kv[1]):
            if j not in used:
                colmap['amount'] = j
                break
    return colmap

def parse_grid(grid, reader_meta=None):
    reader_meta = reader_meta or {}
    grid = [list(r) for r in grid if r is not None]
    header_row, colmap = find_header(grid)
    inferred = False
    if header_row < 0 or ('amount' not in colmap and not ('debit' in colmap and 'credit' in colmap)):
        inferred_map = _infer_columns(grid, header_row + 1 if header_row >= 0 else 0, known=colmap)
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
        has_signal = any(parse_amount(c) is not None for c in row) or \
                     any(parse_date(c)[0] for c in row if c not in (None, ''))
        if not has_signal:
            continue
        data_rows += 1
        if 'debit' in colmap or 'credit' in colmap:
            deb = parse_amount(cell(row, 'debit')) or 0.0
            cred = parse_amount(cell(row, 'credit')) or 0.0
            amount = deb - cred if (deb or cred) else None
            if amount is None and 'amount' in colmap:
                amount = parse_amount(cell(row, 'amount'))
        else:
            amount = parse_amount(cell(row, 'amount'))
            if amount is None:
                for j in range(len(row) - 1, -1, -1):
                    if j in (colmap.get('date'), colmap.get('ref')):
                        continue
                    v = parse_amount(row[j])
                    if v is not None and parse_date(row[j])[0] is None:
                        amount = v
                        break
        raw_ref = cell(row, 'ref')
        if raw_ref not in (None, '') and parse_amount(raw_ref) is not None \
                and parse_date(cell(row, 'date'))[0] is None and not looks_like_ref(raw_ref):
            amts = [parse_amount(c) for c in row]
            amts = [a for a in amts if a is not None]
            if amts:
                doc_total = amts[-1]
            data_rows -= 1
            continue
        if raw_ref in (None, ''):
            for c in row:
                if looks_like_ref(c) and parse_amount(c) is None:
                    raw_ref = c
                    break
        if raw_ref not in (None, ''):
            s_ref = str(raw_ref)
            if (' ' in s_ref.strip() or '\n' in s_ref) :
                from .normalize import REF_HINT
                mm = REF_HINT.search(s_ref)
                if mm:
                    raw_ref = mm.group(0)
        ref = norm_ref(raw_ref)
        if amount is None or not ref:
            invalid_rows += 1
            continue
        iso, raw_date = parse_date(cell(row, 'date'))
        ttype = cell(row, 'type')
        ttype = str(ttype).strip() if ttype not in (None, '') else infer_type(' '.join(str(c) for c in row if c is not None))
        from .normalize import type_from_ref
        pref_type = type_from_ref(ref)
        if pref_type:
            ttype = pref_type
        if ttype in ('Credit Note', 'Payment') and amount is not None and amount > 0 \
                and not (parse_amount(cell(row, 'credit')) or 0):
            amount = -amount
        records.append({
            'ref': ref, 'refRaw': str(raw_ref).strip(),
            'date': raw_date or (iso or ''), 'dateISO': iso,
            'type': ttype, 'amount': round(amount, 2), 'row': idx + 1,
        })

    seen, deduped, dropped = set(), [], 0
    for r in records:
        key = (r['ref'], r['amount'], r['dateISO'])
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        deduped.append(r)
    if dropped:
        warnings.append(f'{dropped} duplicate rows dropped (repeated pages)')
        data_rows -= dropped
    records = deduped
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
            conf = max(conf, 0.9)
        else:
            conf -= 0.15
            warnings.append(f'Extracted sum {s} != document total {doc_total}')
    if reader_meta.get('scanned'):
        conf = 0.0
        warnings.append('PDF appears scanned (no extractable text)')
    conf = max(0.0, min(1.0, conf))

    meta = {
        'headerRow': header_row, 'totalRows': len(grid),
        'dateCol': colmap.get('date', -1), 'refCol': colmap.get('ref', -1),
        'typeCol': colmap.get('type', -1), 'debitCol': colmap.get('debit', -1),
        'creditCol': colmap.get('credit', -1), 'amountCol': colmap.get('amount', -1),
        'dataRows': data_rows, 'invalidRows': invalid_rows,
        'confidence': round(conf, 3), 'warnings': warnings, 'totalsCheck': totals_check,
    }
    meta.update({k: v for k, v in reader_meta.items() if k in ('sheet', 'reader', 'tables', 'textPages', 'scanned')})
    return records, meta
