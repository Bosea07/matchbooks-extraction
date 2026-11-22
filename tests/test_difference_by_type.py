"""The net difference, split into invoices, credit notes and payments.

One figure for "how far apart are we" is three unrelated conversations with
the vendor. The split has to add up to the net difference to the cent, on the
screen and in the workbook, or it is a second opinion rather than a breakdown.

Run: py -B tests\\test_difference_by_type.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.export_xlsx import build_workbook
from lib.reconcile import reconcile, type_group


def _row(ref, typ, amount, date):
    return {'ref': ref, 'refRaw': ref, 'type': typ, 'amount': amount,
            'date': date, 'dateISO': date}


VENDOR = [
    _row('INV-1', 'Invoice', 1000.00, '2026-01-05'),
    _row('INV-2', 'Invoice', 2000.00, '2026-01-10'),      # we booked 1,950.00
    _row('INV-3', 'Invoice', 500.00, '2026-02-01'),       # not in our books
    _row('CN-1', 'Credit Note', -100.00, '2026-02-05'),   # not in our books
    _row('CN-2', 'Credit Note', -50.00, '2026-02-06'),
    _row('RCPT-1', 'Payment', -1000.00, '2026-02-10'),
]
OURS = [
    _row('INV-1', 'Bill', 1000.00, '2026-01-05'),
    _row('INV-2', 'Bill', 1950.00, '2026-01-10'),
    _row('CN-2', 'Credit Note', -50.00, '2026-02-06'),
    _row('RCPT-1', 'Payment', -1000.00, '2026-02-10'),
    _row('PAY-9', 'Payment', -700.00, '2026-02-20'),      # vendor has not recorded it
]


def test_groups():
    assert type_group('Invoice') == 'INVOICE'
    assert type_group('Bill') == 'INVOICE'
    assert type_group('Debit Note') == 'INVOICE'
    assert type_group('Credit Note') == 'CREDIT_NOTE'
    assert type_group('Payment') == 'PAYMENT'
    assert type_group('PYMT Mumbai') == 'PAYMENT'      # a ledger's own voucher name
    assert type_group('Receipt') == 'PAYMENT'
    assert type_group('Journal') == 'OTHER'
    assert type_group('Combination') == 'OTHER'
    assert type_group(None) == 'OTHER'
    print('  ok  every type lands in invoices, credit notes, payments or other')


def test_split_adds_up():
    s = reconcile(VENDOR, OURS)['summary']
    bt = s['byType']
    assert s['byTypeOrder'] == ['INVOICE', 'CREDIT_NOTE', 'PAYMENT'], s['byTypeOrder']
    inv, cn, pay = bt['INVOICE'], bt['CREDIT_NOTE'], bt['PAYMENT']

    assert inv['difference'] == 550.00, inv
    assert (inv['matched'], inv['amountDiff'], inv['extraInVendor']) == (1, 1, 1), inv
    assert inv['amountDiffValue'] == 50.00 and inv['extraInVendorValue'] == 500.00, inv

    assert cn['difference'] == -100.00, cn
    assert (cn['matched'], cn['extraInVendor']) == (1, 1), cn
    assert cn['extraInVendorValue'] == -100.00, cn

    assert pay['difference'] == 700.00, pay
    assert (pay['matched'], pay['missingInVendor']) == (1, 1), pay
    assert pay['missingInVendorValue'] == -700.00, pay

    total = round(sum(b['difference'] for b in bt.values()), 2)
    assert total == s['netDifference'] == 1150.00, (total, s['netDifference'])
    for b in bt.values():
        parts = round(b['amountDiffValue'] + b['extraInVendorValue']
                      - b['missingInVendorValue'] + b['setAsideValue'] + b['roundingValue'], 2)
        assert parts == b['difference'], b
    print('  ok  the three lines add up to the net difference, and each line to its parts')


def test_every_row_carries_its_group():
    out = reconcile(VENDOR, OURS)
    assert all(r.get('typeGroup') in ('INVOICE', 'CREDIT_NOTE', 'PAYMENT', 'OTHER')
               for r in out['results'])
    n = sum(b['rows'] for b in out['summary']['byType'].values())
    assert n == len(out['results']), (n, len(out['results']))
    print('  ok  every result row is counted in exactly one line')


def test_two_currencies_are_not_subtracted():
    v = [_row('INV-1', 'Invoice', 1000.00, '2026-01-05'),
         _row('INV-2', 'Invoice', 2000.00, '2026-01-10')]
    z = [_row('INV-1', 'Bill', 3670.00, '2026-01-05'),
         _row('INV-2', 'Bill', 7340.00, '2026-01-10')]
    s = reconcile(v, z, fx_rate=3.67)['summary'] if _takes_rate() else None
    if s is None:
        print('  --  skipped: this build takes no declared rate')
        return
    assert s['byType']['INVOICE']['difference'] is None, s['byType']['INVOICE']
    assert s['byType']['INVOICE']['vendorTotal'] == 3000.00
    print('  ok  across two currencies the totals are shown and no difference is struck')


def _takes_rate():
    import inspect
    return 'fx_rate' in inspect.signature(reconcile).parameters


def test_workbook_has_the_split_before_the_findings():
    out = reconcile(VENDOR, OURS)
    out['summary']['findings'] = ['a written finding']
    wb = build_workbook(VENDOR, OURS, out, {'vendorName': 'Test vendor'})
    sm = wb['Summary']
    col_a = {str(sm.cell(row=r, column=1).value): r for r in range(1, sm.max_row + 1)
             if sm.cell(row=r, column=1).value}
    split, find = col_a['DIFFERENCE BY DOCUMENT TYPE'], col_a['FINDINGS']
    assert find > split, (split, find)
    assert find == max(r for k, r in col_a.items() if k.isupper() and len(k) > 4), \
        'the written findings are the last section on the Summary tab'
    labels = [sm.cell(row=split + 2 + i, column=1).value for i in range(4)]
    assert labels == ['Invoices', 'Credit notes', 'Payments', 'Total'], labels
    assert str(sm.cell(row=split + 2, column=5).value).startswith('=C'), 'difference is a formula'
    mp = wb['Mapping']
    assert mp.cell(row=4, column=15).value == 'Document type'
    groups = {mp.cell(row=r, column=15).value for r in range(5, 5 + len(out['results']))}
    assert groups == {'Invoices', 'Credit notes', 'Payments'}, groups
    print('  ok  the workbook shows the split, with the findings as the last section')


if __name__ == '__main__':
    test_groups()
    test_split_adds_up()
    test_every_row_carries_its_group()
    test_two_currencies_are_not_subtracted()
    test_workbook_has_the_split_before_the_findings()
    print('\nall difference-by-type tests passed')
