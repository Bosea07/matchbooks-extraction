"""Show exactly what the engine sees in a file, and where it goes wrong.

    py -B tools\\diagnose.py "C:\\path\\to\\statement.xlsx"

Prints the raw grid, the detected header and column map, then every record the
parser produced with its running total. Paste the output when a file extracts
badly — it is far more useful than the finished reconciliation, because it
shows the failure at the point it happens rather than three stages later.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.readers import read_any, sniff
from lib.parser import parse_grid, find_header

MAX_GRID_ROWS = 30
MAX_RECORDS = 40
MAX_CELL = 38


def cell(v):
    s = '' if v is None else str(v)
    s = s.replace('\n', '\\n').replace('\r', '')
    return s[:MAX_CELL]


def main(path):
    data = open(path, 'rb').read()
    print('=' * 78)
    print('FILE   ', os.path.basename(path))
    print('SIZE   ', f'{len(data):,} bytes')
    print('SNIFF  ', sniff(data))
    print('=' * 78)

    grid, meta = read_any(os.path.basename(path), data)
    print(f'\nREADER : {meta}')
    widths = {}
    for r in grid:
        widths[len(r)] = widths.get(len(r), 0) + 1
    print(f'GRID   : {len(grid)} rows · column counts {dict(sorted(widths.items()))}')

    print(f'\n--- FIRST {MAX_GRID_ROWS} ROWS ---------------------------------------')
    for i, row in enumerate(grid[:MAX_GRID_ROWS]):
        cells = ' | '.join(cell(c) for c in row)
        print(f'{i:>4}| {cells[:200]}')
    if len(grid) > MAX_GRID_ROWS:
        print(f'     ... {len(grid) - MAX_GRID_ROWS} more rows')

    hdr, colmap = find_header(grid)
    print(f'\nHEADER ROW : {hdr}')
    print(f'COLUMN MAP : {colmap}')
    if hdr >= 0 and hdr < len(grid):
        print(f'HEADER TEXT: {[cell(c) for c in grid[hdr]]}')
    for key in ('date', 'ref', 'type', 'amount', 'debit', 'credit'):
        if key not in colmap:
            print(f'   !! no {key} column detected')

    records, pmeta = parse_grid(grid, meta)

    print('\n--- PARSER META -------------------------------------------------')
    for k in ('dataRows', 'invalidRows', 'unreferencedRows', 'signConflicts',
              'rowsAccountedFor', 'confidence', 'totalsCheck', 'reader', 'sheet'):
        if k in pmeta:
            print(f'   {k:<18} {pmeta[k]}')
    for w in pmeta.get('warnings', []):
        print(f'   WARNING  {w}')

    print(f'\n--- RECORDS ({len(records)}) -------------------------------------------')
    print(f'{"#":>4} {"date":<12} {"type":<12} {"amount":>14}  reference')
    total = 0.0
    for i, r in enumerate(records[:MAX_RECORDS]):
        total += r['amount']
        print(f'{i:>4} {str(r.get("dateISO") or r.get("date") or "")[:10]:<12} '
              f'{str(r.get("type") or "")[:12]:<12} '
              f'{r["amount"]:>14,.2f}  {r["ref"]}')
    if len(records) > MAX_RECORDS:
        rest = sum(r['amount'] for r in records[MAX_RECORDS:])
        print(f'     ... {len(records) - MAX_RECORDS} more rows, subtotal {rest:,.2f}')
    print(f'\nSUM OF ALL RECORDS : {sum(r["amount"] for r in records):,.2f}')

    syn = [r for r in records if str(r['ref']).startswith('~')]
    if syn:
        print(f'\nUNREFERENCED ({len(syn)}, net {sum(r["amount"] for r in syn):,.2f}):')
        for r in syn[:12]:
            print(f'   row {r["row"]:>4}  {r["amount"]:>12,.2f}  raw={r.get("refRaw", "")[:50]!r}')

    neg = [r for r in records if r['amount'] < 0]
    print(f'\nNEGATIVE ROWS: {len(neg)}  ·  POSITIVE: {len(records) - len(neg)}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
