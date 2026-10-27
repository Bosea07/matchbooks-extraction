"""v2.19 — open-items statements (Humantra / Kuwa, 29 Sep 2026).

Humantra sends not a ledger but a list of what is still unpaid: eight invoices,
each with its Original and Remaining value. v2.18 read it as follows:

  * the "Document" column (holding the word "Invoice" on every row) as the
    reference, so all eight rows collapsed into one reference, INVOICE;
  * the PO Number column as the amount — 1,421.00 for a 19,477.50 invoice —
    because neither "Original (AED)" nor "Remaining (AED)" was recognised; and
    it reported 0.9 confidence;
  * then compared the result with every bill and payment of the year, so
    every settled bill and every payment became "missing in vendor".

Result: 0 matched, 28 missing, a "difference" of -474,056.20. The real
difference is -47,418.15, and every cent of it has a cause. These tests hold
the real rows.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.parser import parse_grid
from lib.reconcile import reconcile

HEAD = ['Posting Date', 'Document', 'Shopify Ord.', 'PO Number', 'Due Date',
        'Days OD', 'Bucket', 'Original (AED)', 'Remaining (AED)']
ROWS = [
    ['2026-04-01', 'Invoice', '#2391', 1421, '2026-04-01', 181, 'Over 120 Days', 19477.5, 0.35],
    ['2026-04-09', 'Invoice', '#2405', 1446, '2026-04-09', 173, 'Over 120 Days', 15067.5, 0.5],
    ['2026-07-11', 'Invoice', '#2750', 1759, '2026-07-11', 80, '61-90 Days', 3822, 3822],
    ['2026-08-08', 'Invoice', '#2850', 1870, '2026-08-08', 52, '31-60 Days', 67620, 67620],
    ['2026-08-13', 'Invoice', '#2870', 1888, '2026-08-13', 47, '31-60 Days', 78645, 78645],
    ['2026-08-15', 'Invoice', '#2888', 1902, '2026-08-15', 45, '31-60 Days', 1837.5, 1837.5],
    ['2026-09-05', 'Invoice', '#2982', 1993, '2026-09-05', 24, '1-30 Days', 8820, 8820],
    ['2026-09-10', 'Invoice', '#3014', 2004, '2026-09-10', 19, '1-30 Days', 68250, 68250],
]
GRID = ([['HUMANTRA', None, 'STATEMENT OF ACCOUNT'] + [None] * 6,
         [None] * 7 + ['Currency: AED', None],
         ['CUSTOMER'] + [None] * 5 + ['Amount Due:', None, None],
         ['Kuwa Food'] + [None] * 5 + ['Statement Date:', None, '29/09/2026'],
         [None] * 9, HEAD] + ROWS +
        [[None] * 9, [None] * 5 + ['Aging Summary', None, None, None],
         [None] * 5 + ['Total Due (AED)', None, None, None]])

# Our books, KUWA, 2026 — as the platform's Zoho side sends them.
# (ref, date, total, balance, status, vendor record)
BILLS = [
    ('AEB2142', '2026-01-07', 12495, 0, 'paid', 'D'),
    ('AEB2245', '2026-02-07', 5880, 0, 'paid', 'D'),
    ('AEB2303', '2026-02-26', 8085, 0, 'paid', 'D'),
    ('AEB2370', '2026-03-17', 9555, 0, 'paid', 'D'),
    ('AEB2391', '2026-04-01', 19477.5, 0, 'paid', 'D'),
    ('AEB2405', '2026-04-07', 15067.5, 1, 'overdue', 'D'),
    ('AEB2439', '2026-04-20', 20580, 20580, 'pending_approval', 'D'),
    ('AE82439', '2026-04-20', 20580, 0, 'paid', 'D'),
    ('AEB2448', '2026-04-22', 59535, 0, 'paid', 'D'),
    ('AEB2650', '2026-06-10', 61005, 0, 'paid', 'D'),
    ('AEB2750', '2026-07-08', 3822, 0, 'paid', 'D'),
    ('AEB2850', '2026-08-07', 67620, 67620, 'overdue', 'D'),
    ('AEB2870', '2026-08-12', 78645, 78645, 'overdue', 'D'),
    ('AEB2888', '2026-08-14', 1837.5, 1837.5, 'overdue', 'D'),
    ('AEB2932', '2026-08-26', 16380, 16380, 'overdue', 'U'),
    ('AEB2933', '2026-08-26', 5460, 5460, 'overdue', 'U'),
    ('AEB2982', '2026-09-03', 8820, 8820, 'overdue', 'D'),
    ('AEB2982', '2026-09-03', 8820, 8820, 'overdue', 'U'),
    ('AEB3014', '2026-09-08', 68250, 68250, 'overdue', 'U'),
    ('AEB3152', '2026-10-05', 152334, 152334, 'open', 'D'),
    ('AEB3214', '2026-10-16', 129049.2, 129049.2, 'pending_approval', 'D'),
]
PAYMENTS = [
    ('PHUB594718813', 'KWA-VP-6033', '2026-01-07', 69457.5),
    ('PHUB610214337', 'KWA-VP-2665', '2026-02-05', 12495),
    ('PHUB641883819', 'KWA-VP-6091', '2026-04-10', 13965),
    ('PHUB660364310', 'KWA-VP-2976', '2026-05-19', 44099),
    ('PHUB680341928', 'KWA-VP-6162', '2026-06-30', 20580),
    ('PHUB686636785', 'KWA-VP-6278', '2026-07-14', 59535),
    ('PHUB693390348', 'KWA-VP-6247', '2026-07-29', 61005),
    ('PHUB731836525', 'KWA-VP-6494', '2026-10-09', 3822),
]


def _zoho(bills=BILLS, payments=PAYMENTS, with_balance=True):
    out = []
    for i, (ref, d, total, bal, st, v) in enumerate(bills):
        row = dict(ref=ref, date=d, type='Bill', amount=total, docType='Bill',
                   docId=f'b{i}', docNo=ref, status=st,
                   vendor='Humantra DMCC' if v == 'D' else 'Humantra UAE')
        if with_balance:
            row['balance'] = bal
        out.append(row)
    for i, (ref, no, d, amt) in enumerate(payments):
        out.append(dict(ref=ref, date=d, type='Payment', amount=-amt,
                        docType='Payment', docId=f'p{i}', docNo=no, status=None,
                        balance=None, vendor='Humantra DMCC'))
    return out


def _parsed():
    return parse_grid([list(r) for r in GRID], {})


def _run():
    recs, meta = _parsed()
    return reconcile(recs, _zoho(), vendor_statement_date=meta['statementDate']), meta


def _by_zref(out):
    return {r['zohoRef']: r for r in out['results'] if r.get('zohoRef')}


# ── extraction ───────────────────────────────────────────────────────────
def test_a_document_column_of_type_words_is_not_the_reference():
    recs, meta = _parsed()
    assert meta['refCol'] == 2 and meta['typeCol'] == 1
    assert [r['ref'] for r in recs] == ['2391', '2405', '2750', '2850',
                                        '2870', '2888', '2982', '3014']
    assert any("holds document types" in w for w in meta['warnings'])


def test_original_is_the_amount_and_remaining_is_the_open_balance():
    recs, meta = _parsed()
    assert round(sum(r['amount'] for r in recs), 2) == 263539.50
    assert recs[0]['amount'] == 19477.5 and recs[0]['openAmount'] == 0.35
    assert meta['statementKind'] == 'open_items'
    assert meta['openTotal'] == 228995.35


def test_po_numbers_are_never_read_as_money():
    recs, _ = _parsed()
    assert 1421.0 not in [r['amount'] for r in recs]


def test_statement_date_is_read_from_the_header_block():
    _, meta = _parsed()
    assert meta['statementDate'] == '2026-09-29'


def test_a_guessed_amount_column_cannot_look_confident():
    head = ['Date', 'Invoice No', 'PO Number', 'Mystery']
    grid = [head] + [[f'2026-01-0{i}', f'INV-00{i}', 1400 + i, 100 * i] for i in range(1, 6)]
    recs, meta = parse_grid(grid, {})
    assert meta['confidence'] <= 0.5
    assert any('AMOUNT COLUMN NOT RECOGNISED' in w for w in meta['warnings'])


# ── reconciliation ───────────────────────────────────────────────────────
def test_humantra_ties_to_the_cent():
    out, _ = _run()
    s = out['summary']
    assert s['statementKind'] == 'open_items'
    # 228,995.35 they say is unpaid, 276,413.50 we say is unpaid by 29 Sep
    assert s['comparableDifference'] == -47418.15
    assert s['invariantsOk'] is True


def test_their_numbers_match_ours_through_the_AEB_prefix():
    out, _ = _run()
    z = _by_zref(out)
    for ref in ('AEB2850', 'AEB2870', 'AEB2888', 'AEB3014'):
        assert z[ref]['status'] == 'MATCHED', ref
        assert 'prefix AEB' in z[ref]['note']
    assert out['summary']['matched'] == 6        # + the two rounding residues


def test_settled_bills_and_payments_are_not_missing():
    out, _ = _run()
    s = out['summary']
    assert s['settled'] == 15                    # 7 paid bills + 8 payments
    assert s['paymentsNotCompared'] == 8
    pays = [r for r in out['results'] if r['type'] == 'Payment']
    assert len(pays) == 8 and all(r['status'] == 'SETTLED' and r['zohoAmt'] is None
                                  for r in pays)
    assert _by_zref(out)['AEB2448']['category'] == 'NOISE'


def test_a_bill_booked_twice_is_called_a_duplicate():
    out, _ = _run()
    r = _by_zref(out)['AEB2982']
    assert r['zohoDuplicate'] == 2 and r['category'] == 'DUPLICATE'
    assert r['vendorAmt'] == 8820 and r['zohoAmt'] == 17640


def test_a_one_character_twin_of_a_paid_bill_is_a_duplicate():
    out, _ = _run()
    r = _by_zref(out)['AEB2439']
    assert r['probableDuplicateOf'] == 'AE82439'
    assert r['category'] == 'DUPLICATE'
    assert r.get('zohoNotApproved') is True


def test_paid_after_the_statement_is_timing():
    out, _ = _run()
    r = _by_zref(out)['AEB2750']
    assert r['category'] == 'TIMING'
    assert r['paidAfterStatement']['ref'] == 'KWA-VP-6494'


def test_bills_dated_after_the_statement_are_set_aside():
    out, _ = _run()
    s = out['summary']
    assert s['afterStatement'] == 2 and s['afterStatementValue'] == 281383.20
    assert _by_zref(out)['AEB3152']['status'] == 'AFTER_STATEMENT'


def test_open_in_our_books_but_not_on_theirs_is_to_query():
    out, _ = _run()
    z = _by_zref(out)
    for ref in ('AEB2932', 'AEB2933'):
        assert z[ref]['category'] == 'TO_QUERY'
        assert 'Humantra UAE' in z[ref]['note']


def test_the_difference_is_fully_explained():
    out, _ = _run()
    by = {}
    for r in out['results']:
        if r['status'] in ('SETTLED', 'AFTER_STATEMENT'):
            continue
        by[r['category']] = round(by.get(r['category'], 0)
                                  + (r['vendorAmt'] or 0) - (r['zohoAmt'] or 0), 2)
    # timing +3,822 · duplicates -29,400 · not on their list -21,840 · rounding -0.15
    assert by['TIMING'] == 3822.0
    assert by['DUPLICATE'] == -29400.0
    assert by['TO_QUERY'] == -21840.0
    assert round(sum(by.values()), 2) == -47418.15


def test_two_vendor_records_are_reported():
    out, _ = _run()
    assert any('2 vendor records' in f for f in out['summary']['findings'])


def test_without_our_balances_it_falls_back_and_says_so():
    recs, meta = _parsed()
    out = reconcile(recs, _zoho(with_balance=False),
                    vendor_statement_date=meta['statementDate'])
    s = out['summary']
    assert s['statementKind'] == 'ledger' and s['openItemsUnavailable'] is True
    assert any('OPEN-ITEMS statement' in f for f in s['findings'])


def test_void_bills_are_not_in_the_books():
    recs, meta = _parsed()
    bills = BILLS + [('VAWB-030624-1', '2026-05-01', 7000, 7000, 'void', 'D')]
    out = reconcile(recs, _zoho(bills=bills), vendor_statement_date=meta['statementDate'])
    assert out['summary']['voidExcluded'] == 1
    assert 'VAWB-030624-1' not in _by_zref(out)
    assert out['summary']['comparableDifference'] == -47418.15


def test_a_learned_prefix_matches_short_numbers_too():
    # three long numbers teach the prefix; the 3-digit one is too short for
    # the digit forms alone
    v = [{'ref': r, 'amount': a, 'dateISO': '2026-05-01', 'type': 'Invoice'}
         for r, a in (('2391', 10.0), ('2405', 20.0), ('2750', 30.0), ('999', 40.0))]
    z = [{'ref': 'AEB' + x['ref'], 'amount': x['amount'], 'dateISO': '2026-05-01',
          'type': 'Bill'} for x in v]
    out = reconcile(v, z)
    assert out['summary']['matched'] == 4
    r = [x for x in out['results'] if x['zohoRef'] == 'AEB999'][0]
    assert 'prefix AEB' in r['note']


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
