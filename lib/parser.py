import re
from .normalize import (parse_amount, parse_date, norm_ref, looks_like_ref,
                        infer_type, is_total_row, is_opening_row,
                        SCI_NOTATION, unwrap_pdf_breaks, canon_type,
                        ref_candidates, clean_cell, is_doc_code)

# Types that increase what is owed. A row the statement labels one of these
# can never carry a negative amount.
DEBIT_TYPES = ('Bill', 'Invoice', 'Debit Note')

# First cell of a row that introduces the account rather than transacting on it.
ACCOUNT_HEADER = re.compile(
    r'^\s*(customer|supplier|vendor|account|party|debtor|creditor)\s*:?\s*$', re.I)

HEADER_SYNONYMS = {
    'date':   ['date', 'doc date', 'document date', 'txn date', 'transaction date',
               'invoice date', 'bill date', 'posting date', 'entry date'],
    'ref':    ['reference', 'ref', 'ref no', 'ref#', 'reference number', 'invoice', 'invoice no',
               'invoice #', 'inv no', 'bill no', 'bill number', 'document', 'document no', 'doc no',
               'voucher', 'voucher no', 'voucher number', 'vch no', 'vch number',
               'number', 'transaction#', 'transaction no', 'particulars ref'],
    'type':   ['type', 'transaction type', 'doc type', 'document type', 'txn type',
               'transaction', 'transactions'],
    'debit':  ['debit', 'debits', 'debit amount', 'dr', 'dr amount', 'invoice amount', 'charges'],
    'credit': ['credit', 'credits', 'credit amount', 'cr', 'cr amount', 'payment amount', 'payments'],
    # 'deb cred' is SAP Business One's single signed-amount column
    # ("Deb./Cred. (LC)") — debits positive, credits negative, one column.
    'amount': ['amount', 'amount aed', 'net amount', 'value', 'total', 'total amount',
               'amount (aed)', 'aed', 'gross amount', 'balance amount',
               'deb cred', 'debit credit', 'dr cr', 'signed amount'],
    'desc':   ['description', 'narration', 'details', 'particulars', 'memo', 'remarks'],
}

# A running balance is not a transaction amount. An SAP B1 export puts
# "Deb./Cred. (LC)" next to "Cumulative Balance (LC)", and reading the second
# one turns every row into the account balance at that point — a ledger of six
# transactions summed to 5.1m instead of its true -1.75m movement. Nothing
# about the output looks wrong, which is what makes it dangerous.
_BALANCE_COL = re.compile(r'\b(cumulative|running|closing|opening)\b|\bbalance\b', re.I)


def _match_header(cell):
    if cell is None:
        return None
    s = re.sub(r'[^a-z#() ]', ' ', str(cell).lower()).strip()
    # Currency qualifiers are noise: "Amount (AED)", "Deb./Cred. (LC)",
    # "Total (FC)" all mean what they say without the bracket.
    s = re.sub(r'\s*\([^)]*\)', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
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
        nxt = grid[i + 1] if i + 1 < len(grid) and grid[i + 1] else []
        if nxt:
            nxt_is_data = any(parse_date(c)[0] for c in nxt if c not in (None, '')) or \
                          sum(1 for c in nxt if parse_amount(c) is not None) >= 2
            if nxt_is_data:
                nxt = []
        if nxt:
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


def _colmap_score(grid, start, colmap):
    """How well does this column map actually fit the data beneath it?

    A header can sit in different columns from its own values when the export
    merges cells, and a map that looks right from the header row alone then
    reads dates out of the amount column. Checking against the data is the only
    way to find out."""
    rows = [r for r in grid[start:start + 40] if any(c not in (None, '') for c in r)]
    if not rows:
        return 0.0
    hits = 0
    for r in rows[:30]:
        j = colmap.get('date')
        if j is not None and 0 <= j < len(r) and parse_date(r[j])[0]:
            hits += 2
        for key in ('amount', 'debit', 'credit'):
            j = colmap.get(key)
            if j is not None and 0 <= j < len(r) and parse_amount(r[j]) is not None:
                hits += 1
    return hits / min(len(rows), 30)


def _compacted_colmap(header_cells):
    """Map the i-th header LABEL to the i-th column.

    Merged header cells leave gaps: "Doc No." spanning five columns is stored
    once with four blanks after it, so every later label ends up well to the
    right of the values it names. Taken in order and re-indexed densely, the
    labels line up with their data again."""
    out = {}
    labels = [c for c in header_cells if c not in (None, '')]
    for i, c in enumerate(labels):
        key = _match_header(c)
        if key and key not in out:
            out[key] = i
    return out


def _infer_columns(grid, start, known=None, header_row=None):
    known = known or {}
    from collections import Counter
    votes = {'date': Counter(), 'ref': Counter(), 'amount': Counter()}
    rows = [r for r in grid[start:start + 40] if any(c not in (None, '') for c in r)]
    width = max((len(r) for r in rows), default=0)
    # Columns the header names as a balance are barred from becoming the
    # amount, however numeric they look. A running balance is numeric in every
    # row, so on votes alone it wins — and produces a total that is nonsense.
    banned = set()
    if header_row is not None and 0 <= header_row < len(grid):
        for j, c in enumerate(grid[header_row]):
            if c and _BALANCE_COL.search(str(c)):
                banned.add(j)
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
            if j not in used and j not in banned:
                colmap['amount'] = j
                break
    return colmap

def parse_grid(grid, reader_meta=None):
    reader_meta = reader_meta or {}
    grid = [list(r) for r in grid if r is not None]
    header_row, colmap = find_header(grid)
    realigned = False
    # Merged header cells push every label right of its own data. Trust the
    # data: if re-indexing the labels densely fits it better, the header was
    # merged and the literal positions are wrong.
    if header_row >= 0 and colmap and header_row < len(grid):
        alt = _compacted_colmap(grid[header_row])
        if alt and alt != colmap:
            lit_score = _colmap_score(grid, header_row + 1, colmap)
            alt_score = _colmap_score(grid, header_row + 1, alt)
            if alt_score > lit_score + 0.5:
                colmap, realigned = alt, True
    inferred = False
    if header_row < 0 or ('amount' not in colmap and not ('debit' in colmap and 'credit' in colmap)):
        inferred_map = _infer_columns(grid, header_row + 1 if header_row >= 0 else 0,
                                      known=colmap, header_row=header_row)
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

    # Columns the header calls a balance, barred everywhere — not just from
    # inference. The last-numeric-cell fallback below would otherwise pick the
    # running balance for any row whose amount cell is blank, which is exactly
    # what an account-header row looks like.
    balance_cols = set()
    if 0 <= header_row < len(grid):
        for j, c in enumerate(grid[header_row]):
            if c and _BALANCE_COL.search(str(c)):
                balance_cols.add(j)

    records, warnings = [], []
    data_rows = invalid_rows = unreferenced = 0
    sign_conflicts = []
    doc_total = None
    opening_balance = None
    for idx, row in enumerate(grid[start:], start=start):
        if not any(c not in (None, '') for c in row):
            continue
        # An account-header row names the party and carries its opening
        # balance; it is not a transaction. Pharmatrade writes "Customer" in
        # the first cell with the balance further along, which no
        # opening-balance wording catches.
        if ACCOUNT_HEADER.match(str(row[0] or '')) \
                and not parse_date(cell(row, 'date'))[0]:
            nums = [parse_amount(c) for c in row]
            nums = [n for n in nums if n is not None]
            if nums and opening_balance is None:
                opening_balance = nums[-1]
            warnings.append(
                f'Row {idx + 1}: account-header row skipped'
                + (f' (opening balance {opening_balance:,.2f})'
                   if opening_balance is not None else ''))
            continue
        if is_opening_row(row):
            nums = [parse_amount(c) for c in row]
            nums = [n for n in nums if n is not None]
            if nums and opening_balance is None:
                opening_balance = nums[-1]
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
                    if j in (colmap.get('date'), colmap.get('ref')) or j in balance_cols:
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
                # a scientific-notation cell (5.00E+15) is a long reference
                # mangled by Excel, never a real statement amount — accept it
                # even though parse_amount() can read it as a number
                if looks_like_ref(c) and (parse_amount(c) is None
                                          or SCI_NOTATION.match(str(c))):
                    raw_ref = c
                    break
        if raw_ref in (None, ''):
            for jj, c in enumerate(row):
                if jj in (colmap.get('date'), colmap.get('amount'),
                          colmap.get('debit'), colmap.get('credit')):
                    continue
                if c in (None, '') or parse_amount(c) is not None:
                    continue
                first = str(c).strip().split('\n')[0].strip().split(' ')[0]
                if re.fullmatch(r'\d{4,9}', first):
                    raw_ref = first
                    break
        # A row can carry several identifiers at once, and the two sides of a
        # reconciliation rarely quote the same one. Keep them all, in priority
        # order, and keep the references it settles separately.
        aliases, allocation = [], []
        if raw_ref not in (None, ''):
            s_ref = unwrap_pdf_breaks(str(raw_ref))
            aliases, allocation = ref_candidates(s_ref)
            if not aliases:
                # nothing reference-shaped: accept a short leading label
                # ("Shortages"), never the whole narration
                lead = clean_cell(s_ref).strip().split('\n')[0].strip().split('\t')[0].strip()
                lead = lead.split('  ')[0].strip()
                # A bare 4-9 digit run in the reference cell of a dated row is
                # a document number, not money: the total-row guard above has
                # already removed the rows where a number there IS an amount.
                # UK suppliers number invoices exactly so ("15195"), and
                # rejecting them loses the row's identity altogether.
                # Receipt vouchers are numbered from 1, so "119" and "73" are
                # document numbers as much as "10496459" is. On a dated row in
                # the reference column a bare number is an identifier: the
                # total-row guard above has already taken out the rows where a
                # number there means money.
                numeric_docno = (re.fullmatch(r'\d{2,9}', lead) is not None
                                 and parse_date(cell(row, 'date'))[0] is not None)
                # Some systems put a space inside the document number — SAP B1
                # writes "RC 15400071" and "IN 1300027". Allow one, but only
                # here, where the cell is already known to be the reference
                # column; loosening REF_HINT globally would start matching
                # "of 15400071" out of narration.
                shaped = re.fullmatch(
                    r'[A-Za-z0-9][A-Za-z0-9._/#-]{1,15}(?: [A-Za-z0-9._/#-]{2,15})?', lead)
                if shaped and (parse_amount(lead) is None or numeric_docno):
                    aliases = [norm_ref(lead)]
        # sweep the rest of the row for identifiers the reference cell missed
        if len(aliases) < 2:
            for jj, c in enumerate(row):
                if c in (None, '') or jj == colmap.get('ref'):
                    continue
                if jj in (colmap.get('date'), colmap.get('amount'),
                          colmap.get('debit'), colmap.get('credit')):
                    continue
                extra_own, extra_alloc = ref_candidates(unwrap_pdf_breaks(str(c)))
                for t in extra_own:
                    if t not in aliases:
                        aliases.append(t)
                for t in extra_alloc:
                    if t not in allocation and t not in aliases:
                        allocation.append(t)
        ref = aliases[0] if aliases else ''
        if amount is None:
            invalid_rows += 1
            continue
        if not ref:
            # A row with a real amount is never discarded just because no
            # reference token could be found. It gets a synthetic ref so it
            # still reaches the payment and value lanes, and the caller is
            # told loudly. Synthetic refs are marked with a leading '~' and
            # are excluded from reference-based matching downstream.
            # A ZERO amount with no reference carries no information - that is
            # a wrapped-text artifact (seen in Bionutri PDFs), not money.
            if round(amount, 2) == 0.0:
                invalid_rows += 1
                continue
            ref = f'~ROW{idx + 1}'
            unreferenced += 1
            raw_ref = raw_ref if raw_ref not in (None, '') else ''
        iso, raw_date = parse_date(cell(row, 'date'))
        raw_type = cell(row, 'type')
        # a real type column is authoritative; narration is only a fallback
        ttype = canon_type(raw_type) if raw_type not in (None, '') else \
            infer_type(' '.join(str(c) for c in row if c is not None))
        from .normalize import type_from_ref
        pref_type = type_from_ref(ref)
        if pref_type:
            ttype = pref_type
        # Statements that label a row "Payment Made" put the figure in a
        # payments column as a positive number, so it has to be negated. A
        # ledger posting document-type CODES does not: its amount column is
        # already signed, and negating a positive DZ turns a reversal into a
        # second payment. Nine rows flipped that way once, moving a ledger's
        # net from -79,037.83 to -2,757,610.49.
        if ttype in ('Credit Note', 'Payment') and amount is not None and amount > 0 \
                and not (parse_amount(cell(row, 'credit')) or 0) \
                and not is_doc_code(raw_type):
            amount = -amount
        # ── sign invariant ────────────────────────────────────────────────
        # A row the statement itself calls a Bill, Invoice or Debit Note
        # INCREASES what is owed; it cannot be negative. When the two
        # disagree, the amount was taken from the wrong place - typically an
        # allocation fragment ("AED1,765.00 from payment KWA-VP-6210") lifted
        # out of a neighbouring payment's narration. Silently keeping it
        # produces a difference of exactly twice the invoice, which is
        # arithmetically impossible between two same-direction documents.
        if ttype in DEBIT_TYPES and amount is not None and amount < 0:
            if is_doc_code(raw_type):
                # A posting code is a category, not a direction. SAP books a
                # reversal under the same code with the sign flipped, so a
                # negative here is the document telling us what it is.
                ttype = 'Credit Note'
            else:
                sign_conflicts.append((idx + 1, ttype, round(amount, 2), str(raw_ref)[:40]))
                invalid_rows += 1
                continue
        records.append({
            'ref': ref, 'refAliases': aliases, 'allocationRefs': allocation,
            'refRaw': clean_cell(raw_ref).strip()[:120],
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
    if realigned:
        warnings.append(
            'Header cells are merged — column labels sit right of their own '
            'data, so the map was rebuilt by label order. Spot-check a row.')
    if unreferenced:
        conf -= 0.10
        val = round(sum(r['amount'] for r in records if r['ref'].startswith('~')), 2)
        warnings.append(
            f'{unreferenced} row(s) kept without a readable reference '
            f'(net {val}) — matched by amount only, verify before posting')
    if sign_conflicts:
        conf -= 0.25
        detail = '; '.join(f'row {r} {t} {a}' for r, t, a, _ in sign_conflicts[:4])
        warnings.append(
            f'{len(sign_conflicts)} row(s) dropped: the statement types them as a '
            f'bill/invoice/debit note but the amount read as negative, so the figure '
            f'was taken from the wrong cell ({detail}). These documents are NOT fully '
            f'extracted — do not rely on this reconciliation until they are.')

    # ── row accounting: every data row must be accounted for ─────────────
    # Three payments once vanished from a Zoho statement with no diagnostic
    # at all, because nothing checked that what went in came out. It does now.
    accounted = len(records) + invalid_rows
    if accounted != data_rows:
        missing = data_rows - accounted
        conf = min(conf, 0.4)
        warnings.append(
            f'ROW ACCOUNTING FAILED: {data_rows} data rows read, {len(records)} kept, '
            f'{invalid_rows} rejected — {missing} unaccounted for. Rows have been lost '
            f'silently; treat every total below as incomplete.')
    totals_check = None
    if doc_total is not None and records:
        s = round(sum(r['amount'] for r in records), 2)
        # A ledger's stated total is a CLOSING BALANCE, not the sum of its
        # movements: opening + movement = closing. Comparing the movement
        # straight to the closing balance fails on every ledger that opens with
        # a balance, which buries the one check worth having.
        expected = doc_total if opening_balance is None \
            else round(doc_total - opening_balance, 2)
        ok = abs(s - expected) <= max(1.0, abs(expected) * 0.001)
        totals_check = {'documentTotal': doc_total, 'extractedSum': s,
                        'openingBalance': opening_balance,
                        'expectedMovement': expected, 'ok': ok}
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
        'unreferencedRows': unreferenced,
        # reported on its own as well as inside totalsCheck: a statement can
        # state an opening balance without stating a closing one
        'openingBalance': opening_balance,
        'signConflicts': len(sign_conflicts),
        'rowsAccountedFor': len(records) + invalid_rows == data_rows,
        'confidence': round(conf, 3), 'warnings': warnings, 'totalsCheck': totals_check,
    }
    meta.update({k: v for k, v in reader_meta.items() if k in ('sheet', 'reader', 'tables', 'textPages', 'scanned')})
    return records, meta
