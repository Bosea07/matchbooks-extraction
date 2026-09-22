"""v2.14 — merged header cells (Pharmatrade / SHIFA customer ledger).

A 64-row ledger produced ZERO records. "Doc No." was a merged cell spanning
five columns, so every later label sat four columns right of its own data: the
parser read Doc Date out of the Debit column, found a number where a date
should be, and discarded every line as a document-total row.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.parser import parse_grid, _compacted_colmap, _colmap_score


# The real export: 9 labels, but "Doc No." is merged across columns 0-4.
HEADER = ['Doc No.', '', '', '', '', 'Doc Type', 'Doc Date', 'Due Date',
          'Narration', 'Opening Balance', 'Debit', 'Credit', 'Closing Balance']
ROWS = [
    ['Customer', '', '', '', '', 405960, 'SHIFA HOME HEALTH CARE L.L.C',
     -1723.93, '', '', '', '', ''],
    [10496459, 'IN', '2026-01-15', '2026-01-15', 'PO2051', '',
     228962.90, 0, 227238.97, '', '', '', ''],
    [119, 'RV', '2026-01-20', '', '', '', 0, 228962.90, -1723.93,
     '', '', '', ''],
    [10499161, 'IN', '2026-01-28', '2026-01-28', 'PO2056', '',
     295436.00, 0, 293712.07, '', '', '', ''],
    [120, 'RV', '2026-01-30', '', '', '', 0, 295436.00, -1723.93,
     '', '', '', ''],
    [4032417, 'CN', '2026-03-23', '2026-03-23', 'PO2070', '',
     0, 324979.60, 500517.27, '', '', '', ''],
]
GRID = [['PHARMATRADE LLC'], ['CUSTOMER LEDGER REPORT AS ON 01-JAN-26'],
        ['Report Generation Date: 9/2/2026'], HEADER] + ROWS


def _parsed():
    return parse_grid(GRID)


# ── the headline failure ─────────────────────────────────────────────────
def test_rows_are_extracted_at_all():
    rows, meta = _parsed()
    assert len(rows) >= 5, f'{len(rows)} records, meta={meta["warnings"]}'


def test_the_realignment_is_reported_not_silent():
    _, meta = _parsed()
    assert any('merged' in w.lower() for w in meta['warnings'])


def test_amounts_come_from_debit_and_credit_not_from_a_balance():
    rows, _ = _parsed()
    amounts = sorted(round(r['amount'], 2) for r in rows)
    assert 228962.90 in amounts, amounts
    assert -228962.90 in amounts, amounts
    assert not any(abs(a - 227238.97) < 0.01 for a in amounts), \
        'a closing-balance figure was read as a transaction'


def test_invoices_are_positive_and_receipts_negative():
    rows, _ = _parsed()
    inv = [r for r in rows if r['ref'] in ('10496459', '10499161')]
    rec = [r for r in rows if r['ref'] in ('119', '120')]
    assert inv and all(r['amount'] > 0 for r in inv), inv
    assert rec and all(r['amount'] < 0 for r in rec), rec


def test_a_credit_note_is_negative():
    rows, _ = _parsed()
    cn = [r for r in rows if r['ref'] == '4032417']
    assert cn and cn[0]['amount'] == -324979.60


def test_the_customer_header_row_is_not_a_transaction():
    """It names the party and carries the opening balance. Read as a
    transaction it arrived as +1,723.93, the opening balance with its sign
    reversed by the credit column."""
    rows, meta = _parsed()
    assert not any(abs(abs(r['amount']) - 1723.93) < 0.01 for r in rows), \
        'the opening balance was kept as a transaction'
    assert not any(r['ref'] == 'CUSTOMER' for r in rows)
    assert any('account-header' in w for w in meta['warnings'])


def test_the_opening_balance_is_captured():
    """Reported in its own right — a statement can state an opening balance
    without ever stating a closing one."""
    _, meta = _parsed()
    assert meta['openingBalance'] == -1723.93


def test_receipt_voucher_numbers_survive_as_references():
    """119, 120, 73, 74 - receipt vouchers are numbered from 1, and a 4-digit
    minimum turned every one of them into a synthetic ~ROW reference."""
    rows, meta = _parsed()
    refs = [r['ref'] for r in rows]
    assert '119' in refs and '120' in refs, refs
    assert meta['unreferencedRows'] == 0, [r for r in refs if r.startswith('~')]


def test_closing_balance_ties_to_opening_plus_movement():
    """The check that matters: a ledger states a CLOSING balance, so the
    comparison is opening + movement = closing, not movement = closing."""
    grid = [['Doc No.', '', 'Doc Type', 'Doc Date', 'Debit', 'Credit'],
            ['Customer', '', '', '', '', -1000.00],
            [101, 'IN', '2026-01-10', 2500.00, 0],
            [102, 'RV', '2026-01-20', 0, 500.00],
            ['Closing Balance', '', '', '', '', 1000.00]]
    _, meta = parse_grid(grid)
    tc = meta['totalsCheck']
    assert tc['openingBalance'] == -1000.00
    assert tc['expectedMovement'] == 2000.00
    assert tc['extractedSum'] == 2000.00
    assert tc['ok'] is True


def test_rows_are_still_all_accounted_for():
    rows, meta = _parsed()
    assert meta['rowsAccountedFor'] is True
    assert len(rows) + meta['invalidRows'] == meta['dataRows']


# ── the mechanism ────────────────────────────────────────────────────────
def test_compacted_map_indexes_labels_densely():
    m = _compacted_colmap(HEADER)
    assert m['ref'] == 0
    assert m['type'] == 1
    assert m['date'] == 2
    assert m['debit'] == 6
    assert m['credit'] == 7


def test_the_compacted_map_scores_better_than_the_literal_one():
    from lib.parser import find_header
    hdr, literal = find_header(GRID)
    alt = _compacted_colmap(GRID[hdr])
    assert _colmap_score(GRID, hdr + 1, alt) > _colmap_score(GRID, hdr + 1, literal)


def test_an_unmerged_header_is_left_alone():
    """Realignment must only fire when the data says the labels are displaced."""
    grid = [['Date', 'Type', 'Reference', 'Debit', 'Credit'],
            ['01/06/2026', 'Bill', 'INV-1', 100.00, 0],
            ['02/06/2026', 'Payment Made', 'PMT-1', 0, 60.00]]
    rows, meta = parse_grid(grid)
    assert not any('merged' in w.lower() for w in meta['warnings'])
    assert round(sum(r['amount'] for r in rows), 2) == 40.00


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
