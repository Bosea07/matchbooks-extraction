"""Reconcile two sheets of one workbook through the engine.

    py -B tools\\recon_workbook.py "book.xlsx" "VendorSheet" "ZohoSheet"

Parses each sheet with the engine's own parser, runs the reconciliation, and
prints both parser reports plus the result. Use it to check the engine against
a reconciliation someone has already done by hand in the same file.
"""
import os
import sys

# Findings carry em dashes and currency symbols. The Windows console defaults
# to cp1252 and renders them as mojibake, which makes a correct number look
# like a bug.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.parser import parse_grid
from lib.reconcile import reconcile


def grid_of(ws):
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    while rows and not any(c not in (None, '') for c in rows[-1]):
        rows.pop()
    return rows


def report(label, records, meta):
    print(f'\n--- {label} -----------------------------------------------')
    for k in ('dataRows', 'invalidRows', 'unreferencedRows', 'signConflicts',
              'rowsAccountedFor', 'confidence'):
        if k in meta:
            print(f'   {k:<18} {meta[k]}')
    print(f'   records            {len(records)}')
    print(f'   sum                {sum(r["amount"] for r in records):,.2f}')
    pos = [r for r in records if r['amount'] > 0]
    neg = [r for r in records if r['amount'] < 0]
    print(f'   positive           {len(pos):>4}  {sum(r["amount"] for r in pos):>16,.2f}')
    print(f'   negative           {len(neg):>4}  {sum(r["amount"] for r in neg):>16,.2f}')
    types = {}
    for r in records:
        types[r['type']] = types.get(r['type'], 0) + 1
    print(f'   types              {types}')
    for w in meta.get('warnings', []):
        print(f'   WARNING  {w}')


def main(path, vsheet, zsheet):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)

    vrec, vmeta = parse_grid(grid_of(wb[vsheet]))
    zrec, zmeta = parse_grid(grid_of(wb[zsheet]))
    report(f'VENDOR  ({vsheet})', vrec, vmeta)
    report(f'OURS    ({zsheet})', zrec, zmeta)

    out = reconcile(vrec, zrec)
    s = out['summary']
    print('\n--- RECONCILIATION ----------------------------------------')
    for k in ('matched', 'amountDiff', 'extraInVendor', 'missingInVendor',
              'netDifference', 'vendorNet', 'zohoNet', 'unreferencedRows',
              'droppedRows', 'currencyMismatch', 'impliedRate',
              'vendorPaymentTotal', 'zohoPaymentTotal', 'paymentGap',
              'paymentsUnmatched', 'vendorPeriod', 'zohoPeriod',
              'comparableWindow', 'vendorChargesAfterWindow',
              'vendorChargesAfterWindowValue', 'matchedByTier', 'invariantsOk'):
        if k in s:
            print(f'   {k:<20} {s[k]}')
    print('\n   FINDINGS:')
    for f in s.get('findings', []):
        print(f'     - {f}')

    rows = out['results']
    for status in ('AMOUNT_DIFF', 'EXTRA_IN_VENDOR', 'MISSING_IN_VENDOR'):
        sel = [r for r in rows if r['status'] == status]
        if not sel:
            continue
        tot = sum((r.get('vendorAmt') or r.get('zohoAmt') or 0) for r in sel)
        print(f'\n   {status}  ({len(sel)} rows, {tot:,.2f})')
        for r in sorted(sel, key=lambda x: -abs(x.get('vendorAmt')
                                                or x.get('zohoAmt') or 0))[:15]:
            v = r.get('vendorAmt')
            z = r.get('zohoAmt')
            print(f'      {str(r["ref"])[:34]:<36}'
                  f'{("" if v is None else f"{v:,.2f}"):>14}'
                  f'{("" if z is None else f"{z:,.2f}"):>14}')
        if len(sel) > 15:
            print(f'      ... {len(sel) - 15} more')


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
