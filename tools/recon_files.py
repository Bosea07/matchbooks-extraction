"""Reconcile two statement FILES through the engine, exactly as the platform
does, and show why each row landed where it did.

    py -B tools\\recon_files.py "vendor.xlsx" "ours.pdf"

Argument order is vendor first, our books second. Any format the engine reads
is accepted on either side.
"""
import collections
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.readers import read_any
from lib.parser import parse_grid
from lib.reconcile import reconcile


def load(path, label):
    data = open(path, 'rb').read()
    grid, rmeta = read_any(os.path.basename(path), data)
    rows, meta = parse_grid(grid, rmeta)
    print(f'\n--- {label}: {os.path.basename(path)} ---------------------')
    print(f'   reader             {rmeta.get("reader")}  rows={len(grid)}')
    for k in ('dataRows', 'invalidRows', 'unreferencedRows', 'signConflicts',
              'rowsAccountedFor', 'openingBalance', 'confidence'):
        if k in meta:
            print(f'   {k:<18} {meta[k]}')
    if meta.get('totalsCheck'):
        print(f'   totalsCheck        {meta["totalsCheck"]}')
    print(f'   records            {len(rows)}   sum {sum(r["amount"] for r in rows):,.2f}')
    types = collections.Counter(r['type'] for r in rows)
    print(f'   types              {dict(types)}')
    dates = sorted(r['dateISO'] for r in rows if r.get('dateISO'))
    if dates:
        print(f'   period             {dates[0]} .. {dates[-1]}')
    for w in meta.get('warnings', []):
        print(f'   WARNING  {w}')
    return rows


def main(vendor_path, ours_path):
    v = load(vendor_path, 'VENDOR')
    z = load(ours_path, 'OURS')

    out = reconcile(v, z)
    s = out['summary']

    print('\n--- RECONCILIATION ------------------------------------------')
    for k in ('matched', 'amountDiff', 'extraInVendor', 'missingInVendor',
              'netDifference', 'vendorNet', 'zohoNet', 'matchedByTier',
              'combosMatched', 'unreferencedRows', 'droppedRows',
              'contraPaired', 'contraPairs', 'contraPairedValue',
              'vendorCurrency', 'zohoCurrency', 'currencyDeclaredMismatch',
              'currencyContradiction',
              'vendorPaymentTotal', 'zohoPaymentTotal', 'paymentGap',
              'paymentsUnmatched', 'vendorPeriod', 'zohoPeriod',
              'comparableWindow', 'vendorChargesAfterWindow',
              'vendorChargesAfterWindowValue', 'years', 'crossYearPairs',
              'counterpartyMismatch', 'invariantsOk'):
        if k in s:
            print(f'   {k:<30} {s[k]}')

    print('\n   FINDINGS:')
    for f in s.get('findings', []):
        print(f'     - {f}')

    # Why are the unmatched rows unmatched? Split them by what they are and
    # when they happened — a hundred exceptions with one cause is one problem.
    rows = out['results']
    for status in ('EXTRA_IN_VENDOR', 'MISSING_IN_VENDOR', 'CONTRA_PAIRED'):
        sel = [r for r in rows if r['status'] == status]
        if not sel:
            continue
        side = 'zohoAmt' if status == 'MISSING_IN_VENDOR' else 'vendorAmt'
        tot = sum(r.get(side) or 0 for r in sel)
        print(f'\n   {status} — {len(sel)} rows, net {tot:,.2f}')

        by_type = collections.Counter(r['type'] for r in sel)
        print(f'      by type : {dict(by_type)}')
        by_year = collections.Counter((r.get('dateISO') or '????')[:4] for r in sel)
        print(f'      by year : {dict(sorted(by_year.items()))}')
        by_lane = collections.Counter(r.get('lane') or '?' for r in sel)
        print(f'      by lane : {dict(by_lane)}')

        print(f'      largest:')
        for r in sorted(sel, key=lambda x: -abs(x.get(side) or 0))[:12]:
            print(f'        {str(r.get("dateISO") or ""):<12}'
                  f'{str(r["ref"])[:30]:<32}{(r.get(side) or 0):>14,.2f}  {r["type"]}')

    print(f'\n   AMOUNT_DIFF rows:')
    for r in sorted((x for x in rows if x['status'] == 'AMOUNT_DIFF'),
                    key=lambda x: -abs(x.get('diff') or 0)):
        print(f'      {str(r.get("dateISO") or ""):<12}{str(r["ref"])[:34]:<36}'
              f'{(r.get("vendorAmt") or 0):>13,.2f}{(r.get("zohoAmt") or 0):>13,.2f}'
              f'{(r.get("diff") or 0):>11,.2f}')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
