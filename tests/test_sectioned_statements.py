"""v2.20 — sectioned statements (Valeo-Kuwa SOA template, INR).

The template is one sheet in three blocks — Invoice Summary, Payments, Credit
Notes — each under its own header row and in its own column order: the
invoice amount sits in column 4, the payment amount in column 3. v2.19 read the
whole sheet through the first header, so:

  * all 7 payments lost their amount (column 4 is blank in that block) and
    were rejected;
  * all 13 credit notes were typed as invoices, read as negative, and rejected
    by the sign invariant.

39 of 59 rows survived, every one of them an invoice. These tests hold the real
rows: invoices 4,830,591.33 · payments -2,650,656.72 · credit notes
-481,761.68, which tie to the sheet's own column totals.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.parser import parse_grid
from lib.reconcile import reconcile

INV_HEAD = ['Invoice Date', 'Invoice Number', 'PO Number ', 'Invoice Amount ',
            'Currency', 'BL Date', 'Due Date']
INVOICES = [
    ['03/07/2026', 'MF/MH26-27/2953', None, 32229.63, 'INR', None, '2026-07-18'],
    ['13/07/2026', 'MFN/DL26-27/1009', None, 162221.4, 'INR', None, '2026-07-13'],
    ['24/08/2026', 'MF/MH26-27/5227', None, 94264.8, 'INR', None, '2026-08-24'],
    ['24/08/2026', 'MF/HR26-27/07844', None, 29941.15, 'INR', None, '2026-09-08'],
    ['27/08/2026', 'MF/HR26-27/07939', None, 31421.25, 'INR', None, '2026-09-11'],
    ['29/08/2026', 'MF/MH26-27/5427', None, 100337.55, 'INR', None, '2026-09-13'],
    ['31/08/2026', 'MFN/DL26-27/1450', None, 178260.95, 'INR', None, '2026-08-31'],
    ['02/09/2026', 'MFN/DL26-27/1506', None, 445462.55, 'INR', None, '2026-09-02'],
    ['2026-07-03', 'MF/MH26-27/2953', 'GBIZ660657', 29166.56, 'INR', None, '2026-07-18'],
    ['2026-06-30', 'MF/MH26-27/2802', 'PO1656', 29166.75, 'INR', None, '2026-06-30'],
    ['2026-05-16', 'MF/MH26-27/1231', 'GBIZ642606', 12389.79, 'INR', None, '2026-05-31'],
    ['2026-05-15', 'MF/HR26-27/02724', 'GBIZ642613', 29174.55, 'INR', None, '2026-05-30'],
    ['2026-05-13', 'MFN/DL26-27/0407', 'PO1559', 227565.1, 'INR', None, '2026-05-28'],
    ['2026-05-12', 'MF/HR26-27/02538', 'PO1558', 30537.3, 'INR', None, '2026-05-27'],
    ['2026-05-12', 'MF/MH26-27/1067', 'PO1558', 12389.9, 'INR', None, '2026-05-27'],
    ['2026-04-30', 'MFN/DL26-27/0302', 'PO1498', 71953, 'INR', None, '2026-05-15'],
    ['2026-04-29', 'MF/HR26-27/01724', None, 16637.5, 'INR', None, '2026-05-14'],
    ['2026-04-24', 'MF/HR26-27/01333', 'GBIZ634944', 16637.17, 'INR', None, '2026-05-09'],
    ['2026-04-22', 'MF/HR26-27/01242', 'PO1497', 27815.9, 'INR', None, '2026-05-07'],
    ['2026-04-16', 'MF/HR26-27/00803', 'GBIZ631533', 86894, 'INR', None, '2026-05-01'],
    ['2026-04-15', 'MFN/DL26-27/0113', 'PO1398', 74933.45, 'INR', None, '2026-04-15'],
    ['2026-04-14', 'MF/HR26-27/00706', 'PO1397', 87005.71, 'INR', None, '2026-04-14'],
    ['2026-04-09', 'MR/KR26-27/0265', 'PO1397', 116602.01, 'INR', None, '2026-04-09'],
    ['2026-02-25', 'MFN/DL25-26/2565', 'PO1300', 228092, 'INR', None, '2026-02-25'],
    ['2026-02-19', 'MF/HR25-26/08388', 'PO1193', 23686.45, 'INR', None, '2026-02-19'],
    ['2026-02-18', 'MF/HR25-26/08349', 'GBIZ611172', 23665, 'INR', None, '2026-03-05'],
    ['2026-02-16', 'MFN/DL25-26/2473', 'PO1193', 23686.45, 'INR', None, '2026-03-03'],
    ['2025-10-09', 'MFN/MH25-26/6235', 'GBIZ559644', 84100, 'INR', None, '2025-10-24'],
    ['2025-10-03', 'MFN/DL25-26/1416', 'PO0711', 4894.56, 'INR', None, '2025-10-03'],
    ['2025-10-03', 'MFN/MH25-26/6054', 'GBIZ547078', 97329.75, 'INR', None, '2025-10-03'],
    ['2025-09-30', 'MFN/DL25-26/1374', 'PO0909', 807558.55, 'INR', None, '2025-09-30'],
    ['2025-09-16', 'MFN/MH25-26/5570', 'GBIZ547078', 49035, 'INR', None, '2025-09-16'],
    ['2025-09-04', 'MFN/DL25-26/1160', 'PO0798', 243285.6, 'INR', None, '2025-09-04'],
    ['2025-08-16', 'MFN/DL25-26/1032', 'PO0711', 173697.5, 'INR', None, '2025-08-16'],
    ['2025-06-17', 'MFN/DL25-26/0531', None, 212840, 'INR', None, '2025-06-17'],
    ['2025-04-17', 'MFN/MH25-26/0743', 'GBIZ477806', 117595, 'INR', None, '2025-04-17'],
    ['2025-04-17', 'MFN/DL25-26/0064', None, 268702.5, 'INR', None, '2025-04-17'],
    ['2025-03-28', 'MF/DL24-25/7449', None, 260712.5, 'INR', None, '2025-03-28'],
    ['2025-03-28', 'MF/DL24-25/7448', None, 268702.5, 'INR', None, '2025-03-28'],
]
PAY_HEAD = ['Payment Date', 'Payment Ref No', 'Payment Amount', 'Currency']
PAYMENTS = [
    ['11/04/2025', 'S72062227', 386297.5, 'INR'],
    ['25/06/2025', 'S47019015', 271504.5, 'INR'],
    ['19/08/2025', 'S72982645', 173697.5, 'INR'],
    ['18/09/2025', 'S55438377', 292320.6, 'INR'],
    ['23/01/2026', 'S49032278', 890760.37, 'INR'],
    ['09/04/2026', 'S91259717', 276839.25, 'INR'],
    ['08/06/2026', 'S43043139', 359237, 'INR'],
]
CN_HEAD = ['Credit Note Date', 'Credit Note Number', 'Invoice Number',
           'Credit Note Amount', 'Currency']
CREDITS = [
    ['11/04/2025', 'C/MKDL25-26/0011', 'MF/DL24-25/7449', -260712.5, 'INR'],
    ['27/05/2025', 'C/MKDL25-26/0072\n', None, -498, 'INR'],
    ['23/07/2025', 'C/MKMH25-26/0539\n', None, -910, 'INR'],
    ['03/10/2025', 'C/MKDL25-26/0386', 'MFN/DL25-26/1032', -16237.21, 'INR'],
    ['10/10/2025', 'C/MKDL25-26/0509', 'MFN/DL25-26/1160', -1999.91, 'INR'],
    ['06/11/2025', 'C/MKMH25-26/1303', 'MFN/MH25-26/6054', -3040, 'INR'],
    ['06/11/2025', 'C/MKDL25-26/0556', 'MFN/DL25-26/1374', -818.42, 'INR'],
    ['07/11/2025', 'C/MKMH25-26/1307', 'MFN/MH25-26/6054', -15858.33, 'INR'],
    ['21/11/2025', 'C/MKMH25-26/1349', 'MFN/MH25-26/6054', -10465.92, 'INR'],
    ['19/02/2026', 'C/MKDL25-26/0735', 'MFN/DL25-26/2473', -23686.45, 'INR'],
    ['14/04/2026', 'C/MKKA26-27/0095', 'MR/KR26-27/0265', -116602.01, 'INR'],
    ['30/04/2026', 'C/MKHR26-27/0264', 'MF/HR26-27/01242', -27815.9, 'INR'],
    ['21/08/2026', 'C/MKMH26-27/0393', 'MF/MH26-27/2802', -3117.03, 'INR'],
]
BLANK = [None] * 7


def _grid(titles=True):
    """The sheet as the xlsx reader returns it. titles=False is the same
    statement as a PDF table extractor tends to see it: block titles sit
    outside the tables and are lost, only the header rows remain."""
    g = []
    if titles:
        g.append(['Invoice Summary'] + [None] * 6)
    g += [INV_HEAD] + INVOICES + [BLANK]
    if titles:
        g.append(['Payments '] + [None] * 6)
    g += [PAY_HEAD + [None] * 3] + [p + [None] * 3 for p in PAYMENTS] + [BLANK]
    if titles:
        g.append(['Credit Notes'] + [None] * 6)
    g += [CN_HEAD + [None] * 2] + [c + [None] * 2 for c in CREDITS]
    return g


def _by_type(records):
    out = {}
    for r in records:
        out.setdefault(r['type'], []).append(r)
    return out


def test_every_block_is_read_under_its_own_header():
    records, meta = parse_grid(_grid())
    t = _by_type(records)
    assert len(records) == 59, f'{len(records)} of 59 rows read'
    assert len(t['Invoice']) == 39
    assert len(t['Payment']) == 7, 'payment amounts sit in column 3, not 4'
    assert len(t['Credit Note']) == 13
    assert meta['invalidRows'] == 0 and meta['signConflicts'] == 0
    assert meta['rowsAccountedFor']
    assert [s['type'] for s in meta['sections']] == ['Invoice', 'Payment', 'Credit Note']


def test_block_totals_tie_to_the_sheet():
    records, _ = parse_grid(_grid())
    t = _by_type(records)
    assert round(sum(r['amount'] for r in t['Invoice']), 2) == 4830591.33
    assert round(sum(r['amount'] for r in t['Payment']), 2) == -2650656.72
    assert round(sum(r['amount'] for r in t['Credit Note']), 2) == -481761.68
    # the sheet's own column totals: invoices + credit notes share column 4
    assert round(sum(r['amount'] for r in t['Invoice'] + t['Credit Note']), 2) == 4348829.65


def test_payments_and_credit_notes_reduce_the_balance():
    """Payments are listed positive in their block; what they do is reduce
    what is owed. Credit notes are listed negative already and stay so."""
    records, _ = parse_grid(_grid())
    for r in records:
        if r['type'] in ('Payment', 'Credit Note'):
            assert r['amount'] < 0, r
        else:
            assert r['amount'] > 0, r


def test_the_invoice_a_credit_note_settles_is_not_its_identity():
    """C/MKDL25-26/0011 settles MF/DL24-25/7449. Taken as an alias it would
    pair the credit note with the invoice and net the two to nothing."""
    records, _ = parse_grid(_grid())
    cn = next(r for r in records if r['ref'] == 'C/MKDL25-26/0011')
    assert 'MF/DL24-25/7449' not in cn['refAliases']
    assert cn['allocationRefs'] == ['MF/DL24-25/7449']


def test_wrapped_credit_note_numbers_are_clean():
    records, _ = parse_grid(_grid())
    refs = {r['ref'] for r in records}
    assert 'C/MKDL25-26/0072' in refs and 'C/MKMH25-26/0539' in refs


def test_the_same_invoice_listed_twice_at_two_values_is_reported():
    """MF/MH26-27/2953 appears in the summary at 32,229.63 and in the list at
    29,166.56. Both are kept — the statement says both — and the reader is told."""
    records, meta = parse_grid(_grid())
    assert sum(1 for r in records if r['ref'] == 'MF/MH26-27/2953') == 2
    assert any('MF/MH26-27/2953' in w and 'more than once' in w for w in meta['warnings'])


def test_without_block_titles_the_headers_still_say_what_each_block_is():
    records, meta = parse_grid(_grid(titles=False))
    t = _by_type(records)
    assert (len(t['Invoice']), len(t['Payment']), len(t['Credit Note'])) == (39, 7, 13)


def test_a_header_repeated_on_every_page_is_not_a_section():
    """A ledger reprints the same header on each PDF page. Same layout every
    time, so it is one table — and its type column must still be obeyed."""
    head = ['Date', 'Invoice No', 'Type', 'Debit', 'Credit']
    grid = [head,
            ['2026-01-05', 'INV-1001', 'Invoice', 500, None],
            ['2026-01-09', 'PMT-2001', 'Payment', None, 500],
            head,
            ['2026-02-05', 'INV-1002', 'Invoice', 700, None]]
    records, meta = parse_grid(grid)
    assert 'sections' not in meta
    assert [r['type'] for r in records] == ['Invoice', 'Payment', 'Invoice']
    assert [r['amount'] for r in records] == [500, -500, 700]


def test_credit_note_does_not_match_the_invoice_it_settles():
    records, _ = parse_grid(_grid())
    vendor = [r for r in records if r['ref'] in ('MF/DL24-25/7449', 'C/MKDL25-26/0011')]
    ours = [{'ref': 'MF/DL24-25/7449', 'refAliases': ['MF/DL24-25/7449'],
             'type': 'Bill', 'amount': 260712.5, 'dateISO': '2025-03-28'},
            {'ref': 'C/MKDL25-26/0011', 'refAliases': ['C/MKDL25-26/0011'],
             'type': 'Credit Note', 'amount': -260712.5, 'dateISO': '2025-04-11'}]
    r = reconcile(vendor, ours)
    matched = {(x['vendorRef'], x['zohoRef']) for x in r['results']
               if x['status'] == 'MATCHED'}
    assert matched == {('MF/DL24-25/7449', 'MF/DL24-25/7449'),
                       ('C/MKDL25-26/0011', 'C/MKDL25-26/0011')}, matched


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
