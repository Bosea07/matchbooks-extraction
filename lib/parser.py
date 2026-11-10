import re
from .normalize import (parse_amount, parse_date, norm_ref, looks_like_ref,
                        infer_type, is_total_row, is_opening_row,
                        SCI_NOTATION, unwrap_pdf_breaks, canon_type,
                        ref_candidates, clean_cell, is_doc_code, detect_currency,
                        guess_currency_from_issuer)

# Types that increase what is owed. A row the statement labels one of these
# can never carry a negative amount.
DEBIT_TYPES = ('Bill', 'Invoice', 'Debit Note')

# First cell of a row that introduces the account rather than transacting on it.
ACCOUNT_HEADER = re.compile(
    r'^\s*(customer|supplier|vendor|account|party|debtor|creditor)\s*:?\s*$', re.I)

HEADER_SYNONYMS = {
    'date':   ['date', 'doc date', 'document date', 'txn date', 'transaction date',
               'invoice date', 'bill date', 'posting date', 'entry date'],
    # A due date is not the document date. Mapped on its own so a sheet with
    # both reads the right one; a sheet with ONLY a due date still uses it as
    # the date (see _map_row).
    'due':    ['due date', 'due on', 'payment due date', 'due dt', 'maturity date'],
    'ref':    ['reference', 'ref', 'ref no', 'ref#', 'reference number', 'invoice', 'invoice no',
               'invoice #', 'inv no', 'bill no', 'bill number', 'document', 'document no', 'doc no',
               'voucher', 'voucher no', 'voucher number', 'vch no', 'vch number',
               'number', 'transaction#', 'transaction no', 'particulars ref',
               # e-commerce suppliers number invoices by store order (Shopify
               # "#2391"), and that order number is what we book as the bill
               'order', 'order no', 'order number', 'order #', 'ord', 'ord no',
               'so no', 'sales order', 'shopify order', 'shopify ord'],
    'type':   ['type', 'transaction type', 'doc type', 'document type', 'txn type',
               'transaction', 'transactions'],
    'debit':  ['debit', 'debits', 'debit amount', 'dr', 'dr amount', 'invoice amount', 'charges'],
    'credit': ['credit', 'credits', 'credit amount', 'cr', 'cr amount', 'payment amount', 'payments'],
    # 'deb cred' is SAP Business One's single signed-amount column
    # ("Deb./Cred. (LC)") — debits positive, credits negative, one column.
    # An open-items statement lists each unpaid document twice over: what it
    # was raised for and what is still unpaid. Matched to our books those are
    # two different comparisons, so they are two different keys. 'open' must
    # sit before 'amount' — "amount due" is an outstanding figure, not a
    # document amount.
    'open':   ['remaining', 'remaining amount', 'remaining balance', 'outstanding',
               'outstanding amount', 'outstanding balance', 'open amount',
               'open balance', 'amount due', 'due amount', 'balance due',
               'pending amount', 'unpaid', 'unpaid amount', 'amount outstanding'],
    'amount': ['amount', 'amount aed', 'net amount', 'value', 'total', 'total amount',
               'amount (aed)', 'aed', 'gross amount', 'balance amount',
               'original', 'original amount', 'invoice value', 'document amount',
               'doc amount', 'original value',
               'deb cred', 'debit credit', 'dr cr', 'signed amount'],
    'desc':   ['description', 'narration', 'details', 'particulars', 'memo', 'remarks'],
    'currency': ['currency', 'document currency', 'currency code', 'curr', 'ccy',
                 'txn currency', 'transaction currency'],
}

# A running balance is not a transaction amount. An SAP B1 export puts
# "Deb./Cred. (LC)" next to "Cumulative Balance (LC)", and reading the second
# one turns every row into the account balance at that point — a ledger of six
# transactions summed to 5.1m instead of its true -1.75m movement. Nothing
# about the output looks wrong, which is what makes it dangerous.
_BALANCE_COL = re.compile(r'\b(cumulative|running|closing|opening)\b|\bbalance\b', re.I)


_STMT_DATE = re.compile(r'\b(statement\s+date|as\s+(?:of|at|on)|date\s+of\s+statement)\b\s*:?', re.I)


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
    if 'due' in colmap and 'date' not in colmap:
        colmap['date'] = colmap.pop('due')
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
            if j is None or not 0 <= j < len(r) or r[j] in (None, ''):
                continue
            # a money column holding a voucher number is the wrong column
            hits += 1 if parse_amount(r[j]) is not None else -1
    return hits / min(len(rows), 30)


def _shifted_colmaps(header_cells, colmap):
    """The opposite of a compacted header: a merged cell that spans MORE
    columns than the header leaves blank. Tally prints "Particulars" across
    three data columns (Dr/Cr, ledger, PO) but stores one blank after it, so
    every later label — Vch Type, Vch No., Debit, Credit — sits one column
    LEFT of its data. Each blank in the header is tried as the point after
    which labels move right by one or two."""
    out = []
    for p, c in enumerate(header_cells):
        if c not in (None, '') or not any(j > p for j in colmap.values()):
            continue
        for s in (1, 2):
            out.append({k: (j + s if j > p else j) for k, j in colmap.items()})
    return out


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


# Labels that name a number which is not money. A column of PO numbers or
# days-overdue is numeric in every row and, left to a vote, wins the amount
# column outright — the Humantra SOA was read with its PO numbers as invoice
# values (1,421.00 for a 19,477.50 invoice) and reported 0.9 confidence.
_NOT_MONEY_COL = re.compile(
    r'\b(days?|od|qty|quantity|no|nos|number|num|po|age|aging|ageing|bucket|'
    r'rate|%|percent|pcs|units?|lpo|order|ord|ref|reference|code|id)\b', re.I)

# Words that name a document TYPE. A column full of them is a type column
# whatever its header says ("Document" holding "Invoice" on every row).
_TYPE_WORD = re.compile(
    r'^\s*(tax\s+)?(invoices?|bills?|payments?(\s+(made|received))?|receipts?|'
    r'credit\s*notes?|debit\s*notes?|journals?|refunds?|sales?\s*returns?|'
    r'opening\s+balance|advance|credits?|debits?)\s*$', re.I)


def _column_values(grid, start, j, n=30):
    out = []
    for r in grid[start:start + 60]:
        if not any(c not in (None, '') for c in r):
            continue
        if j < len(r) and r[j] not in (None, ''):
            out.append(r[j])
        if len(out) >= n:
            break
    return out


def _validate_ref_column(grid, header_row, colmap, warnings):
    """A header that says 'Document' may hold document numbers or document
    TYPES. Read the data before trusting the label: when the column is mostly
    type words it becomes the type column, and the reference is looked for in
    the other columns the header names as one."""
    j = colmap.get('ref')
    if j is None or header_row < 0:
        return colmap
    vals = _column_values(grid, header_row + 1, j)
    if not vals:
        return colmap
    typed = sum(1 for v in vals if _TYPE_WORD.match(str(v)))
    if typed < max(2, 0.6 * len(vals)):
        return colmap
    colmap = dict(colmap)
    del colmap['ref']
    if 'type' not in colmap:
        colmap['type'] = j
    used = set(colmap.values())
    head = grid[header_row]
    best = None
    for k, c in enumerate(head):
        if k in used or _match_header(c) != 'ref':
            continue
        cv = _column_values(grid, header_row + 1, k)
        good = sum(1 for v in cv if str(v).strip().lstrip('#').strip()
                   and not _TYPE_WORD.match(str(v)))
        if cv and good >= 0.6 * len(cv):
            best = k
            break
    if best is not None:
        colmap['ref'] = best
    warnings.append(
        f"Column '{head[j]}' holds document types, not references — used as the "
        f"type column" + (f"; references read from '{head[best]}'" if best is not None
                          else '; no other reference column found'))
    return colmap


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
            # a labelled column that says it is something other than money
            elif c and (_NOT_MONEY_COL.search(str(c)) or
                        _match_header(c) in ('date', 'ref', 'type', 'desc', 'currency')):
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

# ── sectioned statements ────────────────────────────────────────────────
# Some suppliers send one sheet in blocks — "Invoice Summary", "Payments",
# "Credit Notes" — each under its own header row, with its own column order.
# Valeo-Kuwa's template keeps the invoice amount in column 4 and the payment
# amount in column 3. Read through the first header alone, all 7 payments lost
# their amount and all 13 credit notes were rejected as negative invoices.
_SECTION_KINDS = (
    (re.compile(r'credit\s*notes?|\bcn\b', re.I), 'Credit Note'),
    (re.compile(r'debit\s*notes?|\bdn\b', re.I), 'Debit Note'),
    (re.compile(r'\bpayments?\b|\breceipts?\b|\bremittances?\b', re.I), 'Payment'),
    (re.compile(r'\binvoices?\b|\bbills?\b', re.I), 'Invoice'),
)
_MONEY_KEYS = ('amount', 'debit', 'credit', 'open')
# In a payment or credit-note block, a second document-number column naming an
# invoice or bill says what the row SETTLES — never what the row is.
_SETTLES_COL = re.compile(r'\b(invoice|bill|against|applied)\b', re.I)


def _section_kind(text):
    for rx, kind in _SECTION_KINDS:
        if rx.search(text or ''):
            return kind
    return None


def _is_text_only(row):
    cells = [c for c in row if c not in (None, '')]
    return bool(cells) and all(parse_amount(c) is None and not parse_date(c)[0]
                               for c in cells)


def _unglue_titles(grid):
    """PDF text extraction often glues a block title onto the last cell of the
    row above it: "08/06/2026\\nCredit Notes". That breaks the row's date and
    hides the title. Split it off — only when the cell's first part is a date or
    an amount and the next non-empty row is a header (text only), so ordinary
    two-line descriptions are never touched. Returns the cleaned grid and
    {row index: title}."""
    out, glued = [list(r) for r in grid], {}
    for i, row in enumerate(out):
        nxt = next((r for r in out[i + 1:i + 3] if any(c not in (None, '') for c in r)), None)
        if not nxt or not _is_text_only(nxt):
            continue
        for j, c in enumerate(row):
            if not isinstance(c, str) or '\n' not in c:
                continue
            head, _, tail = c.rpartition('\n')
            head, tail = head.strip(), tail.strip()
            if not head or not tail or len(tail) > 40 or not _section_kind(tail):
                continue
            if parse_amount(tail) is not None or parse_date(tail)[0]:
                continue
            if not (parse_date(head)[0] or parse_amount(head) is not None):
                continue
            row[j] = head
            glued[i] = tail
            break
    return out, glued


def _find_sections(grid, glued=None):
    """Header rows that each start a block of their own. Two or more with
    different layouts make a sectioned statement; a header repeated on every
    PDF page has one layout and is not one."""
    found = []
    for i, row in enumerate(grid):
        if not _is_text_only(row):
            continue
        colmap, hits = _map_row(row)
        nxt = next((r for r in grid[i + 1:i + 4] if any(c not in (None, '') for c in r)), None)
        if 'date' not in colmap and nxt and 'ref' in colmap:
            # PDFs cut or wrap header labels: "Credit Note Date" arrives as
            # "Credit Note", and the block was then not seen at all — its 13
            # credit notes were read as more payments. The labelled column the
            # first data row carries its date in is the date column.
            # "Credit Note" alone reads as a credit (money) column; the data
            # under it is a date, so the data decides.
            for j, c in enumerate(nxt):
                owner = next((k for k, v in colmap.items() if v == j), None)
                if owner not in (None, *_MONEY_KEYS, 'type') or j >= len(row) or row[j] in (None, '') \
                        or c in (None, '') or not parse_date(c)[0] or parse_amount(c) is not None and not parse_date(c)[0]:
                    continue
                if owner:
                    colmap.pop(owner)
                else:
                    hits += 1
                colmap['date'] = j
                # the money label it displaced may sit further along ("Credit Note Amount")
                if not any(k in colmap for k in _MONEY_KEYS):
                    taken = set(colmap.values())
                    for jj, cc in enumerate(row):
                        kk = _match_header(cc) if jj not in taken else None
                        if kk in _MONEY_KEYS:
                            colmap[kk] = jj
                            break
                break
        if hits < 2 or 'ref' not in colmap or 'date' not in colmap \
                or not any(k in colmap for k in _MONEY_KEYS):
            continue
        if not nxt or not any(parse_date(c)[0] for c in nxt if c not in (None, '')):
            continue
        # One amount column per block, taken from the header that says
        # "amount". Debit/credit semantics come from the block, not the label —
        # "Credit Note Amount" is not a credit column holding a debit.
        money = [j for k, j in colmap.items() if k in _MONEY_KEYS]
        said = [j for j in money if re.search(r'amount|value|total', str(row[j]), re.I)]
        amt = (said or money)[0]
        colmap = {k: j for k, j in colmap.items() if k not in _MONEY_KEYS}
        colmap['amount'] = amt
        title = ''
        for r in reversed(grid[max(0, i - 2):i]):
            cells = [str(c).strip() for c in r if c not in (None, '')]
            if len(cells) == 1 and _is_text_only(r):
                title = cells[0]
                break
        if not title and glued:
            # a title _unglue_titles split off the row above (or the one before it)
            title = glued.get(i - 1) or glued.get(i - 2) or ''
        header_text = ' '.join(str(c) for c in row if c not in (None, ''))
        kind = _section_kind(title) or _section_kind(header_text)
        settles = []
        if kind and kind not in DEBIT_TYPES:
            settles = [j for j, c in enumerate(row)
                       if c not in (None, '') and j != colmap['ref']
                       and _match_header(c) == 'ref' and _SETTLES_COL.search(str(c))]
        found.append({'headerRow': i, 'colmap': colmap, 'title': title or header_text,
                      'type': kind, 'settles': settles,
                      'layout': re.sub(r'\s+', ' ', header_text.lower())})
    if len({s['layout'] for s in found}) < 2:
        return []
    # Blocks of the same kind are pages, not sections. A Tally "bills
    # outstanding" print repeats one table as page (A) and page (B) under
    # slightly different labels; parsed as two sections every bill counted
    # twice. A card export split by month is one ledger, not five.
    if len({s['type'] for s in found if s['type']}) < 2:
        return []
    # Data above the first recognised header belongs to no block. Parsing by
    # sections would skip it without counting it, so read the sheet as one
    # table instead — the row-accounting invariant then sees every row.
    for row in grid[:found[0]['headerRow']]:
        if any(parse_date(c)[0] for c in row if c not in (None, '')) and \
                any(parse_amount(c) is not None and not parse_date(c)[0]
                    for c in row if c not in (None, '')):
            return []
    return found


def _parse_sections(grid, reader_meta, sections):
    """Parse each block under its own header, then report as one statement.
    Row numbers stay those of the sheet: every block is parsed against the
    whole grid with the other blocks blanked out."""
    first = sections[0]['headerRow']
    records, metas = [], []
    for k, s in enumerate(sections):
        end = sections[k + 1]['headerRow'] if k + 1 < len(sections) else len(grid)
        sub = [row if (i < first or s['headerRow'] <= i < end) else []
               for i, row in enumerate(grid)]
        recs, m = parse_grid(sub, reader_meta, _section=s)
        metas.append((s, recs, m))

    # The same row in two blocks is a repeated page — dropped and counted, as
    # within a single table.
    seen, repeated = set(), 0
    for s, recs, m in metas:
        kept = []
        for r in recs:
            key = (r['ref'], r['amount'], r['dateISO'])
            if key in seen:
                repeated += 1
                continue
            seen.add(key)
            kept.append(r)
        m['dataRows'] -= len(recs) - len(kept)
        recs[:] = kept
        records += kept

    meta = dict(metas[0][2])
    for key in ('dataRows', 'invalidRows', 'unreferencedRows', 'signConflicts'):
        meta[key] = sum(m[key] for _, _, m in metas)
    meta['rowsAccountedFor'] = all(m['rowsAccountedFor'] for _, _, m in metas)
    rows = meta['dataRows']
    meta['confidence'] = round(
        sum(m['confidence'] * m['dataRows'] for _, _, m in metas) / rows, 3) if rows else 0.0
    ccy = {}
    for _, _, m in metas:
        for c, n in (m.get('currencyCounts') or {}).items():
            ccy[c] = ccy.get(c, 0) + n
    meta['currencyCounts'] = ccy
    meta['currency'] = max(ccy, key=ccy.get) if ccy else None
    meta['mixedCurrency'] = len(ccy) > 1
    meta['totalsCheck'] = next((m['totalsCheck'] for _, _, m in metas if m['totalsCheck']), None)
    if any(m['statementKind'] == 'open_items' for _, _, m in metas):
        meta['statementKind'] = 'open_items'
        meta['openTotal'] = round(sum(m['openTotal'] or 0 for _, _, m in metas), 2)

    warnings = [
        f'Sectioned statement: {len(sections)} blocks, each read under its own '
        f'header — ' + ' · '.join(
            f"{s['title']} (row {s['headerRow'] + 1}: {len(recs)} "
            f"{(s['type'] or 'row').lower()}{'s' if len(recs) != 1 else ''}, "
            f"{sum(r['amount'] for r in recs):,.2f})"
            for s, recs, _ in metas)]
    seen = set()
    for s, _, m in metas:
        for w in m['warnings']:
            if w in seen:
                continue
            seen.add(w)
            warnings.append(f"[{s['title']}] {w}")
    if repeated:
        warnings.append(f'{repeated} row(s) repeated in more than one block — '
                        f'dropped as repeated pages')
    if len(ccy) > 1:
        warnings.append('This statement mixes currencies across its blocks: ' +
                        ', '.join(f'{c}x{n}' for c, n in ccy.items()) +
                        '. Totals add them together and are NOT a monetary figure.')
    # One document number listed twice at different values. Both rows are
    # kept — the statement says both — but they will be summed under one
    # reference, so the reader has to know it happened.
    by_ref = {}
    for r in records:
        if not r['ref'].startswith('~'):
            by_ref.setdefault((r['ref'], r['type']), []).append(r)
    twins = [(ref, rs) for (ref, _), rs in by_ref.items()
             if len({x['amount'] for x in rs}) > 1]
    if twins:
        listed = []
        for ref, rs in twins[:4]:
            at = ', '.join(f"row {x['row']} {x['amount']:,.2f}" for x in rs)
            listed.append(f'{ref} ({at})')
        warnings.append(
            f'{len(twins)} document number(s) listed more than once at different '
            f'amounts — kept, and summed under one reference when reconciled: '
            + '; '.join(listed))
    meta['warnings'] = warnings
    meta['sections'] = [
        {'title': s['title'], 'type': s['type'], 'headerRow': s['headerRow'] + 1,
         'records': len(recs), 'total': round(sum(r['amount'] for r in recs), 2)}
        for s, recs, _ in metas]
    return records, meta


def parse_grid(grid, reader_meta=None, _section=None):
    reader_meta = reader_meta or {}
    grid = [list(r) for r in grid if r is not None]
    if _section is None:
        grid, glued = _unglue_titles(grid)
        sections = _find_sections(grid, glued)
        if sections:
            return _parse_sections(grid, reader_meta, sections)
        header_row, colmap = find_header(grid)
    else:
        header_row, colmap = _section['headerRow'], dict(_section['colmap'])
    realigned = False
    # Merged header cells push every label right of its own data. Trust the
    # data: if re-indexing the labels densely fits it better, the header was
    # merged and the literal positions are wrong.
    if _section is None and header_row >= 0 and colmap and header_row < len(grid):
        lit_score = _colmap_score(grid, header_row + 1, colmap)
        best, best_score = None, lit_score + 0.5
        alt = _compacted_colmap(grid[header_row])
        cands = ([('left', alt)] if alt and alt != colmap else []) + \
            [('right', m) for m in _shifted_colmaps(grid[header_row], colmap)]
        for side, m in cands:
            sc = _colmap_score(grid, header_row + 1, m)
            if sc > best_score:
                best, best_score = (side, m), sc
        if best:
            realigned, colmap = best
    pre_warnings = []
    colmap = _validate_ref_column(grid, header_row, colmap, pre_warnings)
    # A statement with only an outstanding column states nothing else to use:
    # its outstanding figure IS the amount.
    if 'open' in colmap and 'amount' not in colmap and \
            not ('debit' in colmap or 'credit' in colmap):
        colmap['amount'] = colmap['open']
    inferred = False
    amount_guessed_under_header = None
    if header_row < 0 or ('amount' not in colmap and not ('debit' in colmap and 'credit' in colmap)):
        inferred_map = _infer_columns(grid, header_row + 1 if header_row >= 0 else 0,
                                      known=colmap, header_row=header_row)
        if 'amount' in inferred_map:
            inferred = True
            if header_row >= 0 and 'amount' not in colmap:
                lab = grid[header_row][inferred_map['amount']] \
                    if inferred_map['amount'] < len(grid[header_row]) else None
                amount_guessed_under_header = str(lab or f"column {inferred_map['amount'] + 1}")
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

    records, warnings = [], list(pre_warnings)
    data_rows = invalid_rows = unreferenced = 0
    sign_conflicts = []
    column_totals = 0
    doc_total = None
    opening_balance = None

    # ── what currency is this statement in? ──────────────────────────────
    # Two sources, in order of trust. A currency COLUMN is a per-row
    # declaration and is authoritative. Failing that, the symbols and codes
    # printed around the document — "Balance Due $ 127,883.28" in a summary
    # block, "Dhs" beside a figure — say what the whole document is in.
    # Never guessed from the amounts themselves.
    ccy_counts = {}
    has_ccy_col = 'currency' in colmap
    if not has_ccy_col:
        for r in grid[:200]:
            for c in r:
                cc = detect_currency(c)
                if cc:
                    ccy_counts[cc] = ccy_counts.get(cc, 0) + 1
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
        # Tally prints column totals with the grand total in the DATE column
        # ("1388300.76 | … | 1150538.17") and no words at all. Kept, each
        # became an unreferenced "invoice" of over a million.
        d_cell = cell(row, 'date')
        if raw_ref in (None, '') and d_cell not in (None, '') \
                and parse_date(d_cell)[0] is None and parse_amount(d_cell) is not None \
                and all(c in (None, '') or parse_amount(c) is not None for c in row):
            column_totals += 1
            data_rows -= 1
            continue
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
                # "#2391" — a store order number. The hash is punctuation.
                lead = lead.lstrip('#').strip()
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
        # what a payment or credit note settles, from the column that says so
        settles = (_section or {}).get('settles', ())
        for jj in settles:
            if jj < len(row) and row[jj] not in (None, ''):
                for t in ref_candidates(unwrap_pdf_breaks(str(row[jj])))[0]:
                    if t not in allocation and t not in aliases:
                        allocation.append(t)
        # sweep the rest of the row for identifiers the reference cell missed
        if len(aliases) < 2:
            for jj, c in enumerate(row):
                if c in (None, '') or jj == colmap.get('ref') or jj in settles:
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
        # A block titled "Payments" or "Credit Notes" states the type of every
        # row in it, unless the block has a type column of its own.
        if _section and _section.get('type') and raw_type in (None, ''):
            ttype = _section['type']
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
        open_amt = None
        if 'open' in colmap:
            open_amt = parse_amount(cell(row, 'open'))
            if open_amt is None:
                open_amt = 0.0
            # same direction as the document it belongs to
            if amount is not None and amount < 0 and open_amt > 0:
                open_amt = -open_amt
        row_ccy = detect_currency(cell(row, 'currency')) if has_ccy_col else None
        if row_ccy:
            ccy_counts[row_ccy] = ccy_counts.get(row_ccy, 0) + 1
        records.append({
            'ref': ref, 'refAliases': aliases, 'allocationRefs': allocation,
            'refRaw': clean_cell(raw_ref).strip()[:120],
            'date': raw_date or (iso or ''), 'dateISO': iso,
            'type': ttype, 'amount': round(amount, 2), 'row': idx + 1,
            'currency': row_ccy,
            **({'dueDateISO': parse_date(cell(row, 'due'))[0]} if 'due' in colmap and parse_date(cell(row, 'due'))[0] else {}),
            **({'openAmount': round(open_amt, 2)} if open_amt is not None else {}),
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
    if amount_guessed_under_header:
        # The header row was found, yet no column in it says "amount". Taking
        # the most numeric column instead is a guess, and a guess must never
        # look like a 0.9-confidence read.
        conf = min(conf, 0.5)
        warnings.append(
            f"AMOUNT COLUMN NOT RECOGNISED: the header names no amount column, so "
            f"figures were read from '{amount_guessed_under_header}'. Check one row "
            f"against the document before reconciling.")
    if realigned == 'left':
        warnings.append(
            'Header cells are merged — column labels sit right of their own '
            'data, so the map was rebuilt by label order. Spot-check a row.')
    elif realigned == 'right':
        warnings.append(
            'Header cells are merged — column labels sit left of their own '
            'data (a label spans more columns than the header shows), so the '
            'map was shifted to fit the data. Spot-check a row.')
    if column_totals:
        warnings.append(f'{column_totals} column-total row(s) skipped (a bare '
                        f'number in the date column, no reference or text)')
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
    # dominant currency, and whether the document mixes more than one
    currency = None
    currency_source = 'stated'
    mixed_currency = False
    if ccy_counts:
        ranked = sorted(ccy_counts.items(), key=lambda kv: -kv[1])
        currency = ranked[0][0]
        # One or two stray rows in another currency is a real thing (Honasa's
        # USD ledger carries two INR postings) and must be said out loud —
        # summing across currencies produces a number that means nothing.
        minor = [c for c, n in ranked[1:] if n > 0]
        if minor and has_ccy_col:
            mixed_currency = True
            conf -= 0.10
            warnings.append(
                'This statement mixes currencies: ' +
                ', '.join(f'{c}x{n}' for c, n in ranked) +
                '. Totals below add them together and are NOT a monetary '
                'figure until the minor rows are converted or excluded.')
    else:
        # Nothing states a currency. The issuer's country may still say it —
        # reported as an inference, never as a declaration.
        # Only the letterhead above the header row identifies the issuer. Our
        # own AP reports list dozens of Indian vendors in their data rows; read
        # from there, every one of them was stamped INR.
        guess, basis = guess_currency_from_issuer(
            c for r in grid[:max(header_row, 0)] for c in r)
        if guess:
            currency, currency_source = guess, 'inferred'
            for r in records:
                r.setdefault('currency', None)
                if not r['currency']:
                    r['currency'] = guess
                    r['currencySource'] = 'inferred'
            warnings.append(
                f'No currency is stated on this statement. The issuer looks '
                f'{guess}-based ({", ".join(basis)}), so it is treated as {guess} '
                f'— an inference, not a declaration. Confirm or change the '
                f'vendor currency before reconciling.')
        else:
            warnings.append(
                'No currency found on this statement — nothing states a code or a '
                'symbol. Amounts are reported as plain numbers; confirm the '
                'currency before comparing them with anything.')

    totals_check = None
    if doc_total is not None and records:
        s = round(sum(r['amount'] for r in records), 2)
        # A Payments or Credit Notes block prints its total as a plain figure
        # ("TOTAL 26,677.00"); its rows reduce the balance and are held negative.
        if _section and _section.get('type') in ('Payment', 'Credit Note'):
            doc_total = -abs(doc_total)
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

    # The date the statement is drawn up to. An open-items statement lists
    # only what was unpaid ON that date, so anything we booked or paid after
    # it cannot appear there — the reconciliation needs to know where that
    # line falls, and the last posting date is only a lower bound for it.
    statement_date = None
    stop = header_row if header_row > 0 else 25
    for r in grid[:stop]:
        for k, c in enumerate(r):
            if c and _STMT_DATE.search(str(c)):
                tail = str(c)[_STMT_DATE.search(str(c)).end():]
                cand = [tail] + [x for x in r[k + 1:] if x not in (None, '')][:2]
                for x in cand:
                    iso, _ = parse_date(str(x).strip(' :') if isinstance(x, str) else x)
                    if iso:
                        statement_date = iso
                        break
            if statement_date:
                break
        if statement_date:
            break
    open_items = 'open' in colmap and any('openAmount' in r for r in records)
    if open_items:
        warnings.append(
            'Open-items statement: each row carries an outstanding balance, so '
            'it will be reconciled against what is still unpaid in our books, '
            'not against every bill and payment ever posted.')

    meta = {
        'headerRow': header_row, 'totalRows': len(grid),
        'statementKind': 'open_items' if open_items else 'ledger',
        'statementDate': statement_date,
        'openCol': colmap.get('open', -1),
        'openTotal': round(sum(r.get('openAmount', 0) for r in records), 2) if open_items else None,
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
        'currency': currency, 'currencyCounts': ccy_counts,
        'currencySource': currency_source if currency else None,
        'mixedCurrency': mixed_currency,
        'confidence': round(conf, 3), 'warnings': warnings, 'totalsCheck': totals_check,
    }
    meta.update({k: v for k, v in reader_meta.items() if k in ('sheet', 'reader', 'tables', 'textPages', 'scanned')})
    return records, meta
