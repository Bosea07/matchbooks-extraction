import io, csv, re

def read_csv(data: bytes):
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'cp1256', 'latin-1'):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError('Could not decode CSV file')
    sample = text[:4000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        class dialect:
            delimiter = ','
            quotechar = '"'
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=dialect.delimiter, quotechar=getattr(dialect, 'quotechar', '"') or '"')]
    return rows, {'reader': 'csv'}

def _score_sheet(grid):
    from .normalize import parse_amount
    score = 0
    for row in grid[:60]:
        nums = sum(1 for c in row if parse_amount(c) is not None)
        if nums >= 1 and sum(1 for c in row if c not in (None, '')) >= 2:
            score += 1 + min(nums, 3)
    return score

def read_xlsx(data: bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    best, best_grid, best_score = None, [], -1
    for ws in wb.worksheets:
        grid = [[c for c in row] for row in ws.iter_rows(values_only=True, max_row=5000)]
        s = _score_sheet(grid)
        if s > best_score:
            best, best_grid, best_score = ws.title, grid, s
    return best_grid, {'reader': 'xlsx', 'sheet': best}

def read_xls(data: bytes):
    import xlrd
    wb = xlrd.open_workbook(file_contents=data)
    best, best_grid, best_score = None, [], -1
    for ws in wb.sheets():
        grid = []
        for r in range(min(ws.nrows, 5000)):
            row = []
            for c in range(ws.ncols):
                cell = ws.cell(r, c)
                if cell.ctype == 3:
                    try:
                        import xlrd.xldate as xd
                        row.append(xd.xldate_as_datetime(cell.value, wb.datemode))
                    except Exception:
                        row.append(cell.value)
                else:
                    row.append(cell.value if cell.value != '' else None)
            grid.append(row)
        s = _score_sheet(grid)
        if s > best_score:
            best, best_grid, best_score = ws.name, grid, s
    return best_grid, {'reader': 'xls', 'sheet': best}

def read_docx(data: bytes):
    import docx
    doc = docx.Document(io.BytesIO(data))
    grid = []
    for table in doc.tables:
        for row in table.rows:
            grid.append([cell.text.strip() or None for cell in row.cells])
    if not grid:
        for p in doc.paragraphs:
            line = p.text.strip()
            if line:
                grid.append(_split_line(line))
    return grid, {'reader': 'docx', 'tables': len(doc.tables)}

_MULTISPACE = re.compile(r'\s{2,}|\t')

def _words_to_rows(words, line_tol=3.0):
    lines = []
    for w in sorted(words, key=lambda w: (round(w['top'], 1), w['x0'])):
        for ln in lines:
            if abs(ln['top'] - w['top']) <= line_tol:
                ln['words'].append(w)
                break
        else:
            lines.append({'top': w['top'], 'words': [w]})
    rows = []
    for ln in lines:
        ws = sorted(ln['words'], key=lambda w: w['x0'])
        widths = [(w['x1'] - w['x0']) / max(len(w['text']), 1) for w in ws]
        cw = sorted(widths)[len(widths) // 2] if widths else 5.0
        gap_thresh = max(cw * 1.8, 7.0)
        cells, cur = [], ws[0]['text']
        for a, b in zip(ws, ws[1:]):
            if b['x0'] - a['x1'] > gap_thresh:
                cells.append(cur)
                cur = b['text']
            else:
                cur += ' ' + b['text']
        cells.append(cur)
        rows.append([c.strip() or None for c in cells])
    return rows

def _split_line(line):
    parts = _MULTISPACE.split(line.strip())
    if len(parts) < 2:
        parts = line.strip().split()
    return [p.strip() or None for p in parts]

def _table_has_content(t):
    """A ruled but empty table — borders drawn, no extractable text — is not a
    usable table. Accepting one masks a scanned page as a successful read."""
    return any(str(c).strip() for row in t for c in row if c is not None)


# A payment on a Zoho statement carries its allocation inside the Details cell:
#
#   28 Feb 2026  Payment Made  CODR2026/HO/106
#                              KWA-VP-6206
#                              AED2,875.84 for payment of .../2025/4404
#                              AED1,355.98 for payment of .../2026/4495
#
# Read by word position, each of those visual lines becomes its own row, and a
# line like "AED2,541.00 for payment of REVERSEPARCEL/Invoice/2026/5019" then
# looks exactly like a transaction: it has an amount and a reference. It is
# neither. It is a breakdown of the payment above it, and emitting it as a row
# invents money that was never on the statement — and, being a payment
# fragment, invents it with the wrong sign.
_ALLOC_FRAGMENT = re.compile(
    r'^\s*(?:[A-Z]{3}\s*)?[\d,]+\.?\d*\s+(?:for\s+payment\s+of|from\s+payment|'
    r'in\s+excess\s+payments?)\b'
    r'|^\s*(?:for\s+payment\s+of|from\s+payment|in\s+excess\s+payments?)\b', re.I)
# A line that is nothing but the tail of a wrapped reference ("/2026/5019").
_REF_TAIL = re.compile(r'^\s*[/\-][A-Za-z0-9/\-]+\s*$')
# Column headings carry no date and no number either, so they look exactly like
# narration. Folding one away would take the column map with it and wreck the
# whole document, so any line reading like a header is never merged.
_HEADER_WORDS = re.compile(
    r'\b(date|type|transactions?|details|particulars|description|narration|'
    r'amount|payments?|balance|debit|credit|reference|ref|invoice|voucher)\b', re.I)


def _is_continuation(text, cells):
    """Is this visual line part of the row above it rather than a row itself?"""
    if len(_HEADER_WORDS.findall(text)) >= 2 and not _ALLOC_FRAGMENT.match(text):
        return False
    if _ALLOC_FRAGMENT.match(text) or _REF_TAIL.match(text):
        return True
    # A stray line carrying neither a date nor a number is narration — a
    # voucher number on its own line, a wrapped description. Restricted to one
    # or two cells so that a header split across few cells is not swallowed.
    if len(cells) <= 2:
        from .normalize import parse_amount, parse_date
        if not any(parse_amount(c) is not None for c in cells) \
                and not any(parse_date(c)[0] for c in cells):
            return True
    return False


def _merge_allocation_fragments(rows):
    """Fold allocation lines back into the row they belong to.

    Merged text is appended to the previous row's longest cell (its narration),
    so the reference stays readable and recorded while never becoming a row of
    its own with an amount attached."""
    out = []
    for row in rows:
        cells = [c for c in row if c not in (None, '')]
        text = ' '.join(str(c) for c in cells).strip()
        if out and text and _is_continuation(text, cells):
            prev = out[-1]
            widest, best = None, -1
            for j, c in enumerate(prev):
                n = len(str(c or ''))
                if n > best:
                    widest, best = j, n
            if widest is not None:
                prev[widest] = (str(prev[widest] or '') + '\n' + text).strip()
            else:
                prev.append(text)
            continue
        out.append(list(row))
    return out


def read_pdf(data: bytes):
    import pdfplumber
    grid, used_tables, text_pages = [], 0, 0
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            good = [t for t in tables if t and len(t) >= 2 and _table_has_content(t)]
            if good:
                used_tables += len(good)
                for t in good:
                    tbl = [[(c.strip() if isinstance(c, str) else c) or None for c in row]
                           for row in t]
                    # pdfplumber sometimes breaks a tall multi-line cell into
                    # separate rows; fold those back here too
                    grid.extend(_merge_allocation_fragments(tbl))
            else:
                words = page.extract_words() or []
                if words:
                    text_pages += 1
                    grid.extend(_merge_allocation_fragments(_words_to_rows(words)))
    # a grid of nothing but empty cells is still nothing
    scanned = not any(c not in (None, '') for row in grid for c in row)
    return grid, {'reader': 'pdf', 'tables': used_tables, 'textPages': text_pages, 'scanned': scanned}

READERS = {
    'csv': read_csv, 'txt': read_csv,
    'xlsx': read_xlsx, 'xlsm': read_xlsx, 'xltx': read_xlsx,
    'xls': read_xls,
    'docx': read_docx,
    'pdf': read_pdf,
}

def read_html_table(data: bytes):
    """Statements exported as HTML but named .xls - read every <table> row.
    Structured markup, so this is a safer parse than a PDF word-grid, but the
    caller is still told the file was not the format it claimed to be."""
    import re as _re
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'cp1256', 'latin-1'):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError('Could not decode HTML statement')
    rows, tables = [], _re.findall(r'<table.*?</table>', text, _re.I | _re.S)
    for tb in tables:
        for tr in _re.findall(r'<tr.*?</tr>', tb, _re.I | _re.S):
            cells = []
            for cell in _re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, _re.I | _re.S):
                v = _re.sub(r'<[^>]+>', ' ', cell)
                v = (v.replace('&nbsp;', ' ').replace('&amp;', '&')
                       .replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"'))
                v = _re.sub(r'\s+', ' ', v).strip()
                cells.append(v or None)
            if any(c for c in cells):
                rows.append(cells)
    if not rows:
        raise ValueError('This file is HTML and contains no readable table. '
                         'Re-export it as XLSX or CSV.')
    return rows, {'reader': 'html-table', 'tables': len(tables)}


READERS['html'] = read_html_table


def sniff(data: bytes):
    """What the bytes actually are, regardless of what the file is called.

    Accounting systems export XLSX under a .xls name, XLS under .xlsx, and CSV
    under both. Trusting the extension means the right reader is never tried."""
    if data[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        return 'xls'                      # OLE2 compound file
    if data[:2] == b'PK':
        head = data[:4000]
        if b'word/' in head or b'word/document.xml' in data[:20000]:
            return 'docx'
        return 'xlsx'                     # zip container — xlsx/xlsm/xltx
    if data[:5] == b'%PDF-':
        return 'pdf'
    if data[:5].lower() in (b'<?xml', b'<html') or data[:15].lower().startswith(b'<table'):
        return 'html'
    return None


def read_any(filename: str, data: bytes):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    actual = sniff(data)

    if ext == 'doc' and actual != 'docx':
        raise ValueError("Legacy .doc files aren't supported — please save the statement as .docx or PDF and re-upload.")

    # the bytes win over the name whenever they disagree
    order = []
    if actual and actual in READERS:
        order.append(actual)
    if ext in READERS and ext not in order:
        order.append(ext)
    if not order:
        if actual is None and ext not in READERS:
            # no signature and no usable extension — text formats have neither
            order = ['csv']
        else:
            raise ValueError(f"Unsupported file type '.{ext}'. Supported: CSV, XLSX, XLS, PDF, DOCX.")

    last = None
    for i, kind in enumerate(order):
        try:
            grid, meta = READERS[kind](data)
        except Exception as e:
            last = e
            # An HTML page is never also a valid spreadsheet - trying the other
            # readers would only replace a clear diagnosis with a vague one.
            if kind == 'html':
                raise
            continue
        if i > 0 or (actual and ext and actual != ext and ext in READERS):
            meta['extensionMismatch'] = f'named .{ext}, read as {kind}'
        return grid, meta
    raise ValueError(f'Could not read this file as {" or ".join(order)}: {last}')
