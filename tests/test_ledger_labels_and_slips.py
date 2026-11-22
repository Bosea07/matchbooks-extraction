"""Regression tests for v2.24 — a Tally ledger reconciled against a Zoho
statement reported a difference twelve times the real one. Six separate
misreadings, each reproduced here in miniature.

Run: py -B tests\\test_ledger_labels_and_slips.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.parser import parse_grid
from lib.reconcile import reconcile


HEAD = ['Date', 'Particulars', '', 'Vch Type', 'Vch No.', 'Debit', 'Credit']


def _ledger(extra_rows=()):
    """A two-column ledger: receipts into one bank account, sales, a closing
    block of bare totals and a worded closing balance."""
    rows = [
        HEAD,
        ['01-Apr-2025', 'By', 'Opening Balance', '', '', 1000.00, ''],
        ['15-Apr-2025', 'To', 'Export Sales', 'SALE', 'E25-001', 5000.00, ''],
        ['20-Apr-2025', 'By', 'ICICI Bank-10579', 'RCPT', 'RCPTN/676', '', 2000.00],
        ['06-Oct-2025', 'By', 'ICICI Bank-10579', 'RCPT', '', '', 700.00],
        ['06-Oct-2025', 'By', 'ICICI Bank-10579', 'RCPT', '', '', 900.00],
        ['10-Oct-2025', 'By', 'ICICI Bank-10579', 'RCPT', 'RCPTN/900', '', 400.00],
        ['12-Oct-2025', 'By', 'ICICI Bank-10579', 'RCPT', 'RCPTN/901', '', 100.00],
    ]
    rows += list(extra_rows)
    deb = sum(r[5] for r in rows[1:] if r[5] != '')
    cred = sum(r[6] for r in rows[1:] if r[6] != '')
    rows.append(['', '', '', '', '', deb, cred])
    rows.append(['', 'By', 'Closing Balance', '', '', '', deb - cred])
    return rows


# ── 1. the bare totals row ──────────────────────────────────────────────
def test_bare_column_totals_are_not_a_transaction():
    recs, meta = parse_grid(_ledger())
    assert all(r['amount'] not in (6000.00, 4100.00, 1900.00) for r in recs), recs
    assert meta['totalsCheck'] and meta['totalsCheck']['ok'], meta['totalsCheck']
    assert meta['rowsAccountedFor']
    assert any('column-total' in w for w in meta['warnings'])


# ── 2. an unlabelled credit ─────────────────────────────────────────────
def test_unlabelled_credit_is_kept_not_dropped_as_a_sign_error():
    extra = [['05-May-2025', 'To', 'ICICI Bank-10579', 'PYMT Mumbai', 'PYMT_MH/00286/25-26', 650.00, ''],
             ['05-May-2025', 'By', 'Marketing fee', '', '', '', 650.00]]
    recs, meta = parse_grid(_ledger(extra))
    fee = [r for r in recs if r['amount'] == -650.00]
    assert len(fee) == 1 and fee[0]['type'] == 'Credit Note', recs
    assert meta['signConflicts'] == 0
    assert any('Credit column' in w for w in meta['warnings'])


def test_a_typed_bill_that_reads_negative_is_still_refused():
    """The sign invariant stays: a row the statement itself CALLS a sale
    cannot be a credit — that is a figure read from the wrong cell."""
    extra = [['05-May-2025', 'By', 'Export Sales', 'SALE', 'E25-009', '', 650.00]]
    recs, meta = parse_grid(_ledger(extra))
    assert meta['signConflicts'] == 1
    assert not [r for r in recs if r['ref'] == 'E25-009']


# ── 3. the bank account is not a reference ──────────────────────────────
def test_a_repeated_column_label_is_not_a_reference():
    recs, _ = parse_grid(_ledger())
    assert not any('BANK-10579' in (r['refAliases'] or []) for r in recs), recs
    same_day = [r for r in recs if r['dateISO'] == '2025-10-06']
    assert len(same_day) == 2 and all(r['ref'].startswith('~') for r in same_day), same_day


def test_two_unnumbered_receipts_on_one_day_stay_two_payments():
    vendor, _ = parse_grid(_ledger())
    ours = [{'ref': 'KWA-VP-1', 'amount': -700.00, 'dateISO': '2025-10-03', 'type': 'Payment'}]
    out = reconcile(vendor, ours)
    hit = [r for r in out['results'] if r.get('status') == 'MATCHED'
           and 'KWA-VP-1' in str(r.get('ref'))]
    assert hit, [(r.get('status'), r.get('ref')) for r in out['results']]
    # the other receipt of that day is still there, on its own
    assert [r for r in out['results'] if r.get('status') == 'EXTRA_IN_VENDOR'
            and str(r.get('ref')).startswith('~')], out['results']


# ── 4. references Excel flattened ───────────────────────────────────────
def test_a_flattened_reference_never_leads_when_a_real_one_is_present():
    grid = [['Date', 'Transactions', 'Details', 'Amount', 'Payments'],
            ['18 Jun 2026', 'Payment Made', '5.00E+15\nKWA-VP-6149', '', '409.00'],
            ['25 Jun 2026', 'Payment Made', '5.00E+15\nKWA-VP-6132', '', '249.00']]
    recs, _ = parse_grid(grid)
    assert [r['ref'] for r in recs] == ['KWA-VP-6149', 'KWA-VP-6132'], recs


def test_same_reference_on_different_dates_is_not_one_payment():
    vendor = [{'ref': '5228', 'amount': -409.00, 'dateISO': '2026-06-22', 'type': 'Payment'},
              {'ref': '5471', 'amount': -249.00, 'dateISO': '2026-06-25', 'type': 'Payment'}]
    ours = [{'ref': '5.00E+15', 'amount': -409.00, 'dateISO': '2026-06-18', 'type': 'Payment'},
            {'ref': '5.00E+15', 'amount': -249.00, 'dateISO': '2026-06-25', 'type': 'Payment'}]
    s = reconcile(vendor, ours)['summary']
    assert s['matched'] == 2 and s['missingInVendor'] == 0 and s['extraInVendor'] == 0, s


def test_clearing_rows_on_one_date_are_still_summed():
    """The SAP case _fold_payments exists for must keep working."""
    vendor = [{'ref': '1400013121', 'amount': -460.00, 'dateISO': '2026-03-01', 'type': 'Payment'},
              {'ref': '1400013121', 'amount': -790.00, 'dateISO': '2026-03-01', 'type': 'Payment'}]
    ours = [{'ref': 'KWA-VP-6428', 'amount': -1250.00, 'dateISO': '2026-03-01', 'type': 'Payment'}]
    s = reconcile(vendor, ours)['summary']
    assert s['matched'] == 1 and s['netDifference'] == 0.0, s


# ── 5. a credit note is called by its own number ────────────────────────
def test_credit_note_keeps_its_own_number_over_the_bill_it_settles():
    grid = [['Date', 'Transactions', 'Details', 'Amount', 'Payments'],
            ['25 Feb 2026', 'Bill', 'E25-036 - due on 25 Feb 2026', '1,766.00', ''],
            ['25 Mar 2026', 'Credits', 'E25-036\nCN-008', '(57.04)', '']]
    recs, _ = parse_grid(grid)
    cn = [r for r in recs if r['type'] == 'Credit Note']
    assert len(cn) == 1 and cn[0]['ref'] == 'CN-008', recs
    assert 'E25-036' in cn[0]['allocationRefs'] and 'E25-036' not in cn[0]['refAliases']
    vendor = [{'ref': 'E25-036', 'amount': 1766.00, 'dateISO': '2026-02-25', 'type': 'Invoice'},
              {'ref': 'CN-008', 'amount': -57.04, 'dateISO': '2026-03-25', 'type': 'Credit Note'}]
    s = reconcile(vendor, recs)['summary']
    assert s['matched'] == 2 and s['amountDiff'] == 0, s


def test_credit_row_quoting_only_a_bill_number_is_unchanged():
    grid = [['Date', 'Transactions', 'Details', 'Amount', 'Payments'],
            ['25 Mar 2026', 'Credits', 'E25-036', '(57.04)', '']]
    recs, _ = parse_grid(grid)
    assert recs[0]['ref'] == 'E25-036' and recs[0]['type'] == 'Credit Note'


# ── 6. a year typed backwards ───────────────────────────────────────────
def test_future_date_is_set_aside_and_said_out_loud():
    extra = [['09-Sep-2062', 'By', 'ICICI Bank-10579', 'RCPT', '11914', '', 300.00]]
    recs, meta = parse_grid(_ledger(extra))
    row = [r for r in recs if r['ref'] == '11914'][0]
    assert row['dateISO'] is None and row['dateSuspect'] == '2062-09-09', row
    assert row.get('dateGuess') == '2026-09-09', row
    assert any('date in the future' in w for w in meta['warnings'])


def test_a_recent_date_is_not_suspect():
    soon = date.today() + timedelta(days=10)
    extra = [[soon.strftime('%d-%b-%Y'), 'By', 'ICICI Bank-10579', 'RCPT', '11915', '', 300.00]]
    recs, _ = parse_grid(_ledger(extra))
    row = [r for r in recs if r['ref'] == '11915'][0]
    assert row['dateISO'] == soon.isoformat() and 'dateSuspect' not in row, row


def test_mistyped_year_still_pairs_a_payment_that_differs_by_bank_charges():
    vendor = [{'ref': '11914', 'amount': -29449.17, 'dateISO': None, 'date': '2062-09-09',
               'dateSuspect': '2062-09-09', 'dateGuess': '2026-09-09', 'type': 'Payment'}]
    ours = [{'ref': 'KWA-VP-6520', 'amount': -29559.35, 'dateISO': '2026-09-04', 'type': 'Payment'}]
    s = reconcile(vendor, ours)['summary']
    assert s['extraInVendor'] == 0 and s['missingInVendor'] == 0, s


# ── 7. our payment the vendor never got, that we had already made once ──
def test_unreceived_payment_repeating_an_earlier_one_is_pointed_at_our_books():
    vendor = [{'ref': '2559', 'amount': -23408.48, 'dateISO': '2026-04-29', 'type': 'Payment'}]
    ours = [{'ref': 'LN-APR', 'amount': -23437.16, 'dateISO': '2026-04-27', 'type': 'Payment'},
            {'ref': 'LN-JUL', 'amount': -23437.16, 'dateISO': '2026-07-17', 'type': 'Payment'}]
    out = reconcile(vendor, ours)
    miss = [r for r in out['results'] if r['status'] == 'MISSING_IN_VENDOR']
    assert len(miss) == 1 and miss[0]['zohoRef'] == 'LN-JUL', out['results']
    assert miss[0]['possibleRepeatOf'] == 'LN-APR' and 'entered twice' in miss[0]['note']
    assert miss[0]['category'] == 'WE_SENT'
    assert any('entered twice' in f for f in out['summary']['findings'])


def test_unreceived_payment_of_a_new_amount_is_not_called_a_repeat():
    vendor = [{'ref': '2559', 'amount': -23408.48, 'dateISO': '2026-04-29', 'type': 'Payment'}]
    ours = [{'ref': 'LN-APR', 'amount': -23437.16, 'dateISO': '2026-04-27', 'type': 'Payment'},
            {'ref': 'KWA-VP-1872', 'amount': -5167.18, 'dateISO': '2025-08-06', 'type': 'Payment'}]
    out = reconcile(vendor, ours)
    miss = [r for r in out['results'] if r['status'] == 'MISSING_IN_VENDOR']
    assert len(miss) == 1 and 'possibleRepeatOf' not in miss[0], miss
    assert not any('entered twice' in f for f in out['summary']['findings'])


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
