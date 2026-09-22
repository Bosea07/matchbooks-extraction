"""v2.13 — SAP document-type codes, period mismatch, contra noise.

From the Honasa reconciliation: a vendor ledger of 153 rows containing 67
payments reported a payment total of 0.00, because SAP writes 'DZ' where a
statement would write 'Payment Made'. Every payment sat in the invoice lane
and the comparison against our own 303,757.05 read as a total gap.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.normalize import canon_type, is_doc_code
from lib.parser import parse_grid
from lib.reconcile import reconcile


# ── document-type codes ──────────────────────────────────────────────────
def test_sap_codes_map_to_the_engines_vocabulary():
    assert canon_type('RV') == 'Invoice'
    assert canon_type('DZ') == 'Payment'
    assert canon_type('KZ') == 'Payment'
    assert canon_type('DG') == 'Credit Note'
    assert canon_type('UE') == 'Journal'


def test_words_still_win_over_codes():
    assert canon_type('Payment Made') == 'Payment'
    assert canon_type('Bill') == 'Bill'
    assert canon_type('Credits') == 'Credit Note'


def test_unknown_wording_is_left_alone():
    assert canon_type('Settlement Voucher') == 'Settlement Voucher'


def test_is_doc_code_only_fires_on_codes():
    assert is_doc_code('DZ') and is_doc_code('rv')
    assert not is_doc_code('Bill')
    assert not is_doc_code('')


# ── the payment lane is populated ────────────────────────────────────────
SAP_GRID = [
    ['Document Number', 'Document Type', 'Document Date',
     'Amount in Doc. Curr.', 'Reference'],
    [8066000965, 'RV', '25/08/2026', 55491.55, '171/VALEO/UAE'],
    [8066000960, 'RV', '24/08/2026', 898.72, '172/VALEO/KSA'],
    [1400013121, 'DZ', '18/08/2026', -46046.34, '180826178829'],
    [1400010013, 'DZ', '17/07/2026', -11864.00, '170726149721'],
]


def test_sap_payments_reach_the_payment_lane():
    rows, _ = parse_grid(SAP_GRID)
    pays = [r for r in rows if r['type'] == 'Payment']
    assert len(pays) == 2, [r['type'] for r in rows]
    assert round(sum(r['amount'] for r in pays), 2) == -57910.34


def test_a_signed_amount_column_is_never_re_signed():
    """A statement writing "Payment Made" puts a positive figure in a payments
    column, so it must be negated. A code-posting ledger is already signed —
    negating a positive DZ turns a reversal into a second payment. Nine rows
    flipped that way once and moved a ledger's net by 2.7m."""
    grid = [['Document Number', 'Document Type', 'Document Date',
             'Amount in Doc. Curr.', 'Reference'],
            [1400008232, 'DZ', '23/06/2026', -7920.00, '230626242987'],
            [1400008374, 'DZ', '23/06/2026', 7920.00, '230626242987']]
    rows, _ = parse_grid(grid)
    assert round(sum(r['amount'] for r in rows), 2) == 0.00, \
        [(r['ref'], r['amount']) for r in rows]


def test_parsed_total_equals_the_signed_column_total():
    rows, _ = parse_grid(SAP_GRID)
    expected = 55491.55 + 898.72 - 46046.34 - 11864.00
    assert round(sum(r['amount'] for r in rows), 2) == round(expected, 2)


def test_a_negative_posting_code_is_a_credit_note_not_an_error():
    """'Bill' is a claim about direction, so a negative one is an extraction
    error. 'RV' is a posting category — SAP books reversals under the same
    code — so there the sign is data."""
    grid = [['Document Number', 'Document Type', 'Document Date',
             'Amount in Doc. Curr.'],
            [8066000900, 'RV', '01/06/2026', -5000.00]]
    rows, meta = parse_grid(grid)
    assert len(rows) == 1, meta['warnings']
    assert rows[0]['type'] == 'Credit Note'
    assert rows[0]['amount'] == -5000.00
    assert meta['signConflicts'] == 0


def test_a_negative_word_typed_bill_is_still_rejected():
    grid = [['Date', 'Type', 'Reference', 'Amount'],
            ['01/06/2026', 'Bill', 'INV-1', '-5000.00']]
    rows, meta = parse_grid(grid)
    assert len(rows) == 0
    assert meta['signConflicts'] == 1


# ── period mismatch is announced ─────────────────────────────────────────
def _row(ref, amt, date, typ='Invoice'):
    return {'ref': ref, 'refRaw': ref, 'amount': amt, 'date': date,
            'dateISO': date, 'type': typ}


def test_differing_periods_are_called_out():
    vendor = [_row('A-1', 100.0, '2024-02-29'),
              _row('A-2', 200.0, '2025-06-30'),
              _row('A-3', 300.0, '2026-05-01')]
    zoho = [_row('A-3', 300.0, '2026-05-01')]
    out = reconcile(vendor, zoho)
    s = out['summary']
    assert s['periodMismatch'] is True
    assert s['vendorPeriod'] == ['2024-02-29', '2026-05-01']
    assert any('different periods' in f for f in s['findings'])


def test_charges_after_our_statement_ends_are_called_out_separately():
    """History and unbooked charges both fall outside the window, but only one
    of them is a finding. The vendor's July-August invoices are the answer the
    reconciliation exists to produce; 2024 postings are not."""
    vendor = [_row('OLD-1', 5000.0, '2024-02-29'),
              _row('IN-9', 300.0, '2026-05-01'),
              _row('IN-10', 55491.55, '2026-08-25'),
              _row('IN-11', 26028.70, '2026-08-24')]
    zoho = [_row('IN-9', 300.0, '2026-05-01')]
    s = reconcile(vendor, zoho)['summary']
    assert s['vendorChargesAfterWindow'] == 2
    assert s['vendorChargesAfterWindowValue'] == 81520.25
    assert any('not recorded yet' in f for f in s['findings'])
    assert any('predate' in f for f in s['findings'])


def test_matching_periods_raise_nothing():
    vendor = [_row('A-1', 100.0, '2026-05-01')]
    zoho = [_row('A-1', 100.0, '2026-05-01')]
    out = reconcile(vendor, zoho)
    assert out['summary']['periodMismatch'] is False


# ── contra pairs are claimed once ────────────────────────────────────────
def test_one_row_cannot_offset_two_others():
    """400001361 was reported as offsetting both 400001389 and 400001391 —
    the same 342,363.00 stated as two separate findings."""
    vendor = [_row('X-1', 342363.0, '2026-03-01'),
              _row('X-2', -342363.0, '2026-03-02'),
              _row('X-3', -342363.0, '2026-03-03')]
    out = reconcile(vendor, [])
    contras = [f for f in out['summary']['findings'] if 'contra' in f]
    assert len(contras) == 1, contras


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
