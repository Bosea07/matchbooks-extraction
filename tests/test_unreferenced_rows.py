"""Regression tests for v2.7 — 'a row with money in it is never silently dropped'.

Run: python -m pytest tests/ -q      (or: python tests/test_unreferenced_rows.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.normalize import REF_HINT, looks_like_ref, unwrap_pdf_breaks
from lib.parser import parse_grid
from lib.reconcile import reconcile


# ── reference recognition ───────────────────────────────────────────────
def test_scientific_notation_is_a_reference():
    """Excel mangles long card/txn numbers into 5.00E+15. That is a
    reference, not an amount — Cambridge Nutraceuticals, 04 Jun 2026."""
    assert REF_HINT.search('5.00E+15')
    assert looks_like_ref('5.00E+15')


def test_known_reference_formats_still_match():
    """Guard against REF_HINT regressions from earlier vendors."""
    for ref in ('SO-0822', 'INV-2186', 'xxxxxxxxxxxx0330', '597872319',
                '016/26/00012', 'Invoice No.15282'):
        assert looks_like_ref(ref), ref
    # note: '806G000834' (single embedded letter) is NOT matched by REF_HINT.
    # Pre-existing gap, unchanged by v2.7 — such refs only survive when they
    # arrive in a real reference column, not via the free-text fallback.


def test_amounts_are_not_references():
    for amt in ('1,234.56', '3390.00', '664.50', '-1,284.42'):
        assert not looks_like_ref(amt), amt


def test_pdf_line_wrapped_refs_rejoin():
    assert 'SO-0800' in unwrap_pdf_breaks('£1,851.88 for payment of SO-\n0800')


# ── the parser keeps rows it cannot name ────────────────────────────────
def test_row_with_amount_but_no_ref_is_kept():
    grid = [
        ['Date', 'Details', 'Amount'],
        ['01-06-2026', 'INV-2186', '234.00'],
        ['04-06-2026', 'no reference at all here', '7716.00'],
    ]
    records, meta = parse_grid(grid)
    assert len(records) == 2, 'the unreferenced row must survive'
    assert meta['unreferencedRows'] == 1
    assert meta['invalidRows'] == 0
    orphan = [r for r in records if r['ref'].startswith('~')]
    assert len(orphan) == 1 and orphan[0]['amount'] == 7716.00
    assert any('without a readable reference' in w for w in meta['warnings'])


# ── reconcile keeps their value and matches them by amount ──────────────
def test_unreferenced_rows_reach_the_totals():
    """Before v2.7 these vanished and netDifference read 0.00 with
    invariantsOk true — a clean reconciliation that was wrong."""
    vendor = [{'ref': 'INV-001', 'amount': 1000, 'dateISO': '2026-01-01', 'type': 'Invoice'},
              {'ref': '', 'amount': 5000, 'dateISO': '2026-01-02', 'type': 'Invoice'},
              {'ref': None, 'amount': 250, 'dateISO': '2026-01-03', 'type': 'Invoice'}]
    zoho = [{'ref': 'INV-001', 'amount': 1000, 'dateISO': '2026-01-01', 'type': 'Invoice'}]
    s = reconcile(vendor, zoho)['summary']
    assert s['vendorNet'] == 6250.0
    assert s['netDifference'] == 5250.0
    assert s['unreferencedRows'] == 2
    assert any('no readable reference' in f for f in s['findings'])


def test_synthetic_refs_never_match_by_reference():
    """Two unrelated ref-less rows must not pair just because their
    invented keys share digits."""
    vendor = [{'ref': '', 'amount': 111, 'dateISO': '2026-01-01', 'type': 'Invoice'}]
    zoho = [{'ref': '', 'amount': 999, 'dateISO': '2026-09-01', 'type': 'Invoice'}]
    r = reconcile(vendor, zoho)
    assert r['summary']['matched'] == 0
    assert r['summary']['extraInVendor'] == 1
    assert r['summary']['missingInVendor'] == 1
    assert all(not str(x['ref']).startswith('~') for x in r['results']), \
        'synthetic keys must never be shown to the user'


def test_unreferenced_payment_matches_on_amount():
    """The Cambridge failure in miniature: vendor names the payment, our
    books do not. It must still match, in the payment lane, on amount."""
    vendor = [{'ref': 'PY-0402', 'amount': -7716.0, 'dateISO': '2026-06-04', 'type': 'Payment'}]
    zoho = [{'ref': '', 'amount': -7716.0, 'dateISO': '2026-06-04', 'type': 'Payment'}]
    r = reconcile(vendor, zoho)
    assert r['summary']['matched'] == 1
    assert r['summary']['netDifference'] == 0.0


def test_rows_with_unreadable_amounts_are_reported():
    vendor = [{'ref': 'INV-001', 'amount': 'n/a', 'dateISO': '2026-01-01', 'type': 'Invoice'}]
    s = reconcile(vendor, [])['summary']
    assert s['droppedRows'] == 1
    assert any('unreadable amount' in f for f in s['findings'])


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
