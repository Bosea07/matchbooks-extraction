"""Regression tests for v2.9 — the Arata / Slick Organics failures.

The through-line of every failure so far: the engine decided a row's identity
from ONE guessed reference, then demanded that single guess equal the other
side's single guess. These tests pin the four fixes.

Run: python -m pytest tests/ -q   (or: python tests/test_reference_identity.py)
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.normalize import clean_cell, ref_candidates, split_allocation
from lib.parser import parse_grid
from lib.readers import read_any, sniff
from lib.reconcile import reconcile


# ── 1. the bytes decide which reader runs, not the file name ────────────
def _xlsx_bytes():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Date', 'Vch Type', 'Vch No.', 'Debit', 'Credit'])
    ws.append(['2025-04-12', 'Sale Invoice', 'INS/25-26/0151', 150697.25, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_named_xls_is_still_read():
    """Accounting systems export XLSX under a .xls name. Routing on the
    extension sent it to xlrd, which threw, and the parser returned nothing."""
    data = _xlsx_bytes()
    assert sniff(data) == 'xlsx'
    grid, meta = read_any('Kuwa ledger from April2025 to July2026.xls', data)
    assert meta['reader'] == 'xlsx'
    assert meta.get('extensionMismatch')
    assert len(grid) >= 2


def test_html_masquerading_as_a_spreadsheet_is_read_and_named():
    html = (b'<html><body><table>'
            b'<tr><th>Date</th><th>Ref</th><th>Amount</th></tr>'
            b'<tr><td>01/07/2026</td><td>SO-0822</td><td>3,293.00</td></tr>'
            b'</table></body></html>')
    grid, meta = read_any('statement.xls', html)
    assert meta['reader'] == 'html-table'
    assert grid[1][1] == 'SO-0822'
    assert 'extensionMismatch' in meta, 'the format lie must still be reported'


def test_html_with_no_table_is_refused():
    try:
        read_any('statement.xls', b'<html><body><p>Session expired</p></body></html>')
    except ValueError as e:
        assert 'HTML' in str(e)
    else:
        raise AssertionError('an HTML page with no table must be reported, not accepted')


def test_vch_no_is_a_reference_column_and_vch_type_is_not():
    grid = [
        ['Date', 'Particulars', 'Vch Type', 'Vch No.', 'Debit', 'Credit'],
        ['2025-06-11', 'Hdfc Bank Ltd (50200023535069)', 'Receipt', '2024255103', None, '231696.85'],
    ]
    _records, meta = parse_grid(grid)
    assert meta['refCol'] == 3, 'Vch No. is the reference column'
    assert meta['typeCol'] == 2, 'Vch Type must not be mistaken for it'


# ── 2. allocation narration must not steal a row's identity ─────────────
def test_for_payment_of_does_not_become_the_reference():
    """A credit note that settles INS/25-26/1578 is not called INS/25-26/1578.
    Taking that reference merged the credit note into the bill and turned a
    clean match into a false AMOUNT_DIFF of 91,247.75 against 90,698.85."""
    cell = 'Shortages<div>shortages</div>\nRs.548.90 for payment of INS/25-26/1578'
    own, alloc = ref_candidates(cell)
    assert 'INS/25-26/1578' not in own
    assert 'INS/25-26/1578' in alloc


def test_own_reference_survives_alongside_allocation():
    cell = 'PO0194<div>ISR/25/260508</div>\nRs.3,832.40 for payment of INS/25-26/0151'
    own, alloc = ref_candidates(cell)
    assert own == ['PO0194', 'ISR/25/260508']
    assert alloc == ['INS/25-26/0151']


def test_allocation_reference_never_matches():
    """The credit note and the bill it settles must stay two separate rows."""
    vendor = [{'ref': 'INS/25-26/1578', 'refAliases': ['INS/25-26/1578'],
               'type': 'Invoice', 'amount': 91247.75, 'dateISO': '2025-07-11'}]
    ours = [{'ref': 'INS/25-26/1578', 'refAliases': ['INS/25-26/1578'],
             'type': 'Bill', 'amount': 91247.75, 'dateISO': '2025-08-16'},
            {'ref': 'SHORTAGES', 'refAliases': ['SHORTAGES'],
             'allocationRefs': ['INS/25-26/1578'],
             'type': 'Credit Note', 'amount': -548.90, 'dateISO': '2026-01-01'}]
    r = reconcile(vendor, ours)
    bill = [x for x in r['results'] if x['status'] == 'MATCHED']
    assert len(bill) == 1 and bill[0]['vendorAmt'] == 91247.75, \
        'the bill must match at its full value, not net of the credit note'
    assert r['summary']['missingInVendor'] == 1


def test_export_artefacts_are_stripped():
    assert '_x000D_' not in clean_cell('ISR/25/260029_x000D_\n')
    assert ref_candidates('ISR/25/260029_x000D_\n')[0] == ['ISR/25/260029']
    assert '<div>' not in clean_cell('PO0194<div>ISR/25/260508</div>')


# ── 3. a row has a set of identifiers, not one ──────────────────────────
def test_rows_pair_on_any_shared_identifier():
    """We call it PO0194, they call it ISR/25/260508, and the same cell holds
    both. One guess each meant no match; keeping all of them matches."""
    vendor = [{'ref': 'ISR/25/260508', 'refAliases': ['ISR/25/260508'],
               'type': 'Credit Note', 'amount': -3832.40, 'dateISO': '2025-06-03'}]
    ours = [{'ref': 'PO0194', 'refAliases': ['PO0194', 'ISR/25/260508'],
             'type': 'Credit Note', 'amount': -3832.40, 'dateISO': '2025-06-03'}]
    r = reconcile(vendor, ours)
    assert r['summary']['matched'] == 1
    assert 'shared identifier' in r['results'][0]['note']


def test_a_shared_identifier_still_needs_the_signs_to_agree():
    vendor = [{'ref': 'ISR/1', 'refAliases': ['ISR/1'], 'type': 'Credit Note',
               'amount': -500.0, 'dateISO': '2025-06-03'}]
    ours = [{'ref': 'PO1', 'refAliases': ['PO1', 'ISR/1'], 'type': 'Bill',
             'amount': 500.0, 'dateISO': '2025-06-03'}]
    r = reconcile(vendor, ours)
    assert r['summary']['matched'] == 0


# ── 4. pairing and agreement are different questions ────────────────────
def _big_payment_pair():
    vendor = [{'ref': '2024258777', 'refAliases': ['2024258777'], 'type': 'Payment',
               'amount': -1338018.00, 'dateISO': '2026-07-14'}]
    ours = [{'ref': 'XXXXXXXXXXXX0000', 'refAliases': ['XXXXXXXXXXXX0000'],
             'type': 'Payment', 'amount': -1338027.05, 'dateISO': '2026-07-14'}]
    return vendor, ours


def test_a_bank_charge_is_one_difference_not_two_exceptions():
    """9.05 on a 1.34m payment failed the 1.00 tolerance, so the report showed
    2.7m of phantom exceptions instead of a 9.05 discrepancy."""
    r = reconcile(*_big_payment_pair())
    s = r['summary']
    assert s['extraInVendor'] == 0 and s['missingInVendor'] == 0
    assert s['amountDiff'] == 1
    assert abs(r['results'][0]['diff'] - 9.05) < 0.01


def test_pairing_generously_does_not_mean_agreeing():
    """Paired is not matched. The gap must still be reported."""
    r = reconcile(*_big_payment_pair())
    assert r['summary']['matched'] == 0, 'a 9.05 gap is not agreement'
    assert r['results'][0]['status'] == 'AMOUNT_DIFF'


def test_a_genuinely_different_amount_is_left_alone():
    """4.2% apart is not a bank charge. The engine must not invent a pair."""
    vendor = [{'ref': 'RCPT/1', 'refAliases': ['RCPT/1'], 'type': 'Payment',
               'amount': -1034799.44, 'dateISO': '2026-05-03'}]
    ours = [{'ref': '128263004681', 'refAliases': ['128263004681'], 'type': 'Payment',
             'amount': -991289.03, 'dateISO': '2026-04-30'}]
    s = reconcile(vendor, ours)['summary']
    assert s['matched'] == 0 and s['amountDiff'] == 0
    assert s['extraInVendor'] == 1 and s['missingInVendor'] == 1


def test_an_exact_match_still_beats_a_near_one():
    vendor = [{'ref': 'V1', 'refAliases': ['V1'], 'type': 'Bill',
               'amount': 100000.00, 'dateISO': '2025-06-01'}]
    ours = [{'ref': 'Z-NEAR', 'refAliases': ['Z-NEAR'], 'type': 'Bill',
             'amount': 100500.00, 'dateISO': '2025-06-01'},
            {'ref': 'Z-EXACT', 'refAliases': ['Z-EXACT'], 'type': 'Bill',
             'amount': 100000.00, 'dateISO': '2025-06-01'}]
    r = reconcile(vendor, ours)
    hit = [x for x in r['results'] if x['status'] == 'MATCHED']
    assert len(hit) == 1 and 'Z-EXACT' in hit[0]['ref']


# ── 5. an unmatched row says what it is closest to ──────────────────────
def test_unmatched_rows_name_their_nearest_counterpart():
    vendor = [{'ref': 'RCPT/1', 'refAliases': ['RCPT/1'], 'type': 'Payment',
               'amount': -1034799.44, 'dateISO': '2026-05-03'}]
    ours = [{'ref': '128263004681', 'refAliases': ['128263004681'], 'type': 'Payment',
             'amount': -991289.03, 'dateISO': '2026-04-30'}]
    r = reconcile(vendor, ours)
    for x in r['results']:
        assert x.get('nearest'), 'each side should point at the other'
        assert 'apart' in x['note']


def test_no_hint_is_offered_when_nothing_is_close():
    vendor = [{'ref': 'V1', 'refAliases': ['V1'], 'type': 'Bill', 'amount': 900000.0}]
    ours = [{'ref': 'Z1', 'refAliases': ['Z1'], 'type': 'Bill', 'amount': 384.45}]
    r = reconcile(vendor, ours)
    assert not any(x.get('nearest') for x in r['results'])


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
