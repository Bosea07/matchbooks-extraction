"""v2.11 — the Reverse Parcel failures.

A reconciliation of a Zoho SOA against a vendor ledger matched 15 of 21
references, invented one match between unrelated documents, dropped three
payments totalling 23,233.62 without a word, and missed the only real
discrepancy in the file. Every case below is taken from that run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.parser import parse_grid
from lib.readers import _merge_allocation_fragments
from lib.normalize import REF_HINT, norm_ref
from lib.reconcile import reconcile


# ── 1 · allocation fragments are not transactions ────────────────────────
def test_allocation_lines_fold_into_the_payment_above():
    rows = [
        ['30 Apr 2026', 'Payment Made', 'CODR2026/HO/166', '', '8,923.31'],
        ['', '', 'KWA-VP-6209', '', ''],
        ['', '', 'AED4,015.00 for payment of REVERSEPARCEL/Invoice/2026/4897', '', ''],
        ['', '', 'AED2,763.30 for payment of REVERSEPARCEL/Invoice/2026/4954', '', ''],
    ]
    out = _merge_allocation_fragments(rows)
    assert len(out) == 1, f'allocation lines became rows: {out}'
    assert '4897' in str(out[0]), 'the allocation text must be kept, not discarded'
    assert 'KWA-VP-6209' in str(out[0]), 'the voucher number must survive too'


def test_a_header_row_is_never_swallowed():
    rows = [
        ['Statement of Accounts'],
        ['Date', 'Transactions', 'Details', 'Amount', 'Payments', 'Balance'],
        ['15 Jan 2026', 'Bill', 'INV-1', '100.00', '', '100.00'],
    ]
    out = _merge_allocation_fragments(rows)
    assert any('Transactions' in str(r) and 'Balance' in str(r) for r in out), \
        'the header row was folded away'


def test_excess_payment_line_is_not_a_row():
    rows = [
        ['15 Apr 2026', 'Payment Made', 'CODR2026/HO/169', '', '8,450.93'],
        ['', '', 'AED5,834.21 in excess payments', '', ''],
    ]
    assert len(_merge_allocation_fragments(rows)) == 1


def test_from_payment_line_is_not_a_row():
    rows = [
        ['30 Apr 2026', 'Bill', 'DN/HO/2026/199 - due on 30 Apr 2026', '1,765.00'],
        ['', '', 'AED1,765.00 from payment KWA-VP-6210', ''],
    ]
    assert len(_merge_allocation_fragments(rows)) == 1


def test_a_real_row_is_never_folded():
    rows = [
        ['15 Apr 2026', 'Bill', 'REVERSEPARCEL/Invoice/2026/5019', '2,541.00'],
        ['30 Apr 2026', 'Bill', 'REVERSEPARCEL/Invoice/2026/5095', '3,195.80'],
    ]
    assert len(_merge_allocation_fragments(rows)) == 2


# ── 2 · a bill can never be negative ─────────────────────────────────────
def test_a_bill_with_a_negative_amount_is_rejected_not_kept():
    """Diff came out as 5,082.00 on a 2,541.00 invoice — exactly twice — which
    only happens when one side's sign is inverted."""
    grid = [
        ['Date', 'Type', 'Reference', 'Amount'],
        ['15/04/2026', 'Bill', 'REVERSEPARCEL/Invoice/2026/5019', '2541.00'],
        ['15/04/2026', 'Bill', 'REVERSEPARCEL/Invoice/2026/5020', '-2541.00'],
    ]
    rows, meta = parse_grid(grid)
    assert all(r['amount'] > 0 for r in rows), 'a negative bill survived'
    assert meta['signConflicts'] == 1
    assert any('negative' in w for w in meta['warnings'])


def test_a_payment_may_of_course_be_negative():
    grid = [
        ['Date', 'Type', 'Reference', 'Amount'],
        ['28/02/2026', 'Payment Made', 'KWA-VP-6205', '265.56'],
    ]
    rows, _ = parse_grid(grid)
    assert rows[0]['amount'] == -265.56


# ── 3 · every row is accounted for ───────────────────────────────────────
def test_row_accounting_holds():
    grid = [
        ['Date', 'Type', 'Reference', 'Amount'],
        ['15/01/2026', 'Bill', 'INV-1', '100.00'],
        ['31/01/2026', 'Bill', 'INV-2', '200.00'],
        ['28/02/2026', 'Payment Made', 'PMT-1', '150.00'],
    ]
    rows, meta = parse_grid(grid)
    assert meta['rowsAccountedFor'] is True
    assert len(rows) + meta['invalidRows'] == meta['dataRows']
    assert not any('ROW ACCOUNTING FAILED' in w for w in meta['warnings'])


# ── 4 · references survive their slashes ─────────────────────────────────
def test_multi_segment_payment_reference_is_not_truncated():
    """CODR2026/HO/105 was being cut to CODR2026, collapsing eight distinct
    vouchers into one."""
    m = REF_HINT.search('CODR2026/HO/105')
    assert m and m.group(0) == 'CODR2026/HO/105', f'got {m and m.group(0)!r}'


def test_receipt_reference_still_parses():
    m = REF_HINT.search('MR/HO/2026/10521')
    assert m and m.group(0) == 'MR/HO/2026/10521'


def test_plain_reference_still_parses():
    m = REF_HINT.search('Invoice PEXQA-INV-1432 due')
    assert m and 'PEXQA-INV-1432' in m.group(0)


# ── 5 · near-amount pairing must not invent matches ──────────────────────
def _row(ref, amt, date, typ='Invoice'):
    return {'ref': ref, 'refRaw': ref, 'amount': amt, 'date': date,
            'dateISO': date, 'type': typ}


def test_two_unrelated_documents_are_not_paired_on_closeness():
    """DN/HO/2026/335 (1,806.99) was paired with invoice 5439 (1,771.00) —
    35.99 apart, three months apart, different documents entirely."""
    vendor = [_row('DN/HO/2026/335', 1806.99, '2026-07-31')]
    zoho = [_row('REVERSEPARCEL/INVOICE/2026/5439', 1771.00, '2026-07-15')]
    out = reconcile(vendor, zoho)
    assert out['summary']['matched'] == 0, 'unrelated rows were paired'
    assert out['summary']['amountDiff'] == 0
    assert out['summary']['extraInVendor'] == 1
    assert out['summary']['missingInVendor'] == 1


def test_a_bank_charge_still_pairs():
    vendor = [_row('INV-900', 1_340_000.00, '2026-05-02', 'Invoice')]
    zoho = [_row('INV-900', 1_339_990.95, '2026-05-02', 'Invoice')]
    out = reconcile(vendor, zoho)
    assert out['summary']['matched'] + out['summary']['amountDiff'] == 1


# ── 6 · the payment gap is stated, not left to be added up by hand ───────
def test_unmatchable_payment_lane_reports_its_aggregate():
    vendor = [_row('MR/HO/2026/10520', -5222.48, '2026-02-17', 'Payment'),
              _row('MR/HO/2026/10521', -18819.10, '2026-02-17', 'Payment')]
    zoho = [_row('KWA-VP-6205', -265.56, '2026-02-28', 'Payment'),
            _row('KWA-VP-6206', -4231.82, '2026-02-28', 'Payment'),
            _row('KWA-VP-6207', -7351.81, '2026-03-15', 'Payment'),
            _row('KWA-VP-6208', -5859.38, '2026-03-31', 'Payment')]
    out = reconcile(vendor, zoho)
    s = out['summary']
    assert s['paymentGap'] is not None
    # vendor 24,041.58 against our 17,708.57
    assert abs(abs(s['paymentGap']) - 6333.01) < 0.01, s['paymentGap']
    assert any('Payments do not agree' in f for f in s['findings'])


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
