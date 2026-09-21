"""v2.12 — SAP Business One general-ledger exports.

A six-transaction ledger extracted as a total of 5,127,459.22 when its true
movement was -1,748,166.00. The parser had read the "Cumulative Balance (LC)"
column as the transaction amount: every row was the account balance at that
point, and the output looked entirely plausible.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.parser import parse_grid, _match_header
from lib.normalize import infer_type


# The real export, trimmed to the columns that matter.
HEADER = ['Posting Date', 'Due Date', 'Document Date', 'Series', 'Doc. No.',
          'Trans. No.', 'Remarks', 'Offset Acct', 'Offset Acct Name',
          'Deb./Cred. (LC)', 'Cumulative Balance (LC)']
ROWS = [
    ['Customer', 'C000124', '', '', '', '', 'Kuwa Food Suppliments', '', '',
     '', 1929045.86],
    ['13/04/26', '13/04/26', '13/04/26', 'REC2627', 'RC 15400071', 79175,
     'Incoming Payments - C000124', 'ACABA1072', 'KOTAK MAHINDRA BANK',
     -1897405.84, 31640.02],
    ['24/04/26', '24/05/26', '24/04/26', 'TIEXDL26', 'IN 1300027', 85004,
     'A/R Invoices - C000124', 'ISASR0000', 'Revenue From Sales',
     246007.30, 277647.32],
    ['07/05/26', '06/06/26', '07/05/26', 'TIEXDL26', 'IN 1300025', 84997,
     'A/R Invoices - C000124', 'ISASR0000', 'Revenue From Sales',
     329715.40, 607362.72],
    ['27/05/26', '26/06/26', '27/05/26', 'TIEXDL26', 'IN 1300024', 84995,
     'A/R Invoices - C000124', 'ISASR0000', 'Revenue From Sales',
     352639.00, 960001.72],
    ['30/06/26', '30/06/26', '30/06/26', 'TIEXDL26', 'IN 1300026', 85346,
     'A/R Invoices - C000124', 'ISASP0000', 'Revenue From Sales',
     180880.00, 1140881.72],
    ['10/07/26', '10/07/26', '10/07/26', 'REC2627', 'RC 15400514', 85207,
     'Incoming Payments - C000124', 'ACABA1072', 'KOTAK MAHINDRA BANK',
     -960001.86, 180879.86],
]
GRID = [HEADER] + ROWS


def _parsed():
    return parse_grid(GRID)


# ── the headline failure ─────────────────────────────────────────────────
def test_the_signed_amount_column_is_used_not_the_running_balance():
    rows, meta = _parsed()
    total = round(sum(r['amount'] for r in rows), 2)
    assert total == -1748166.00, f'got {total}, expected the true movement'


def test_movement_reconciles_opening_to_closing_balance():
    """1,929,045.86 opening - 1,748,166.00 movement = 180,879.86 closing,
    which is the last Cumulative Balance in the file."""
    rows, _ = _parsed()
    closing = round(1929045.86 + sum(r['amount'] for r in rows), 2)
    assert closing == 180879.86, closing


def test_a_balance_column_is_never_inferred_as_the_amount():
    grid = [['Date', 'Doc', 'Movement', 'Cumulative Balance'],
            ['01/01/2026', 'X-1', 100.00, 100.00],
            ['02/01/2026', 'X-2', 250.00, 350.00]]
    rows, _ = parse_grid(grid)
    assert round(sum(r['amount'] for r in rows), 2) == 350.00


# ── header matching ──────────────────────────────────────────────────────
def test_currency_brackets_are_stripped_from_headers():
    assert _match_header('Deb./Cred. (LC)') == 'amount'
    assert _match_header('Amount (AED)') == 'amount'


def test_a_balance_header_is_not_an_amount_header():
    assert _match_header('Cumulative Balance (LC)') != 'amount'


# ── references ───────────────────────────────────────────────────────────
def test_a_document_number_containing_a_space_is_a_reference():
    """"RC 15400071" was falling through to the Series column, so six
    transactions came back labelled REC2627 / ISASR0000."""
    rows, _ = _parsed()
    refs = [r['ref'] for r in rows]
    assert any('15400071' in r for r in refs), refs
    assert not any(r.startswith('REC2627') for r in refs), refs


def test_no_row_is_left_unreferenced():
    rows, meta = _parsed()
    assert meta['unreferencedRows'] == 0, [r['ref'] for r in rows]


# ── types and signs ──────────────────────────────────────────────────────
def test_incoming_payments_plural_is_read_as_a_payment():
    assert infer_type('Incoming Payments - C000124') == 'Payment'
    assert infer_type('A/R Invoices - C000124') == 'Invoice'


def test_payments_keep_their_negative_sign():
    rows, _ = _parsed()
    pays = [r for r in rows if r['type'] == 'Payment']
    assert len(pays) == 2, [r['type'] for r in rows]
    assert all(r['amount'] < 0 for r in pays)


# ── the opening row is dropped, and counted ──────────────────────────────
def test_the_account_header_row_is_not_a_transaction():
    rows, meta = _parsed()
    assert len(rows) == 6, [r['ref'] for r in rows]
    assert meta['rowsAccountedFor'] is True
    assert not any(abs(r['amount'] - 1929045.86) < 0.01 for r in rows), \
        'the opening balance was kept as a transaction'


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
