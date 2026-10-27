"""v2.18 — categorise every row by what has to happen to it, and by whom.

A reviewer reading "41 missing in Zoho" cannot tell an invoice we must book
from a journal posting internal to the vendor's own ledger. Status says where a
row sits; category says what to do about it, which is the only question a
reconciliation is really asked.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.reconcile import reconcile


def _row(ref, amt, date, typ='Invoice'):
    return {'ref': ref, 'refRaw': ref, 'amount': amt, 'date': date,
            'dateISO': date, 'type': typ}


def _cats(out):
    return {r['ref']: r['category'] for r in out['results']}


# ── who acts ─────────────────────────────────────────────────────────────
def test_an_invoice_only_they_have_is_ours_to_book():
    out = reconcile([_row('8066000948', 47221.84, '2026-07-28')], [])
    assert _cats(out)['8066000948'] == 'TO_BOOK'


def test_a_journal_only_they_have_is_theirs_to_explain():
    """400001391 at -342,363 is the largest item in the Honasa reconciliation
    and it is a posting internal to their ledger. It is not something we
    failed to record."""
    out = reconcile([_row('400001391', -342363.00, '2025-07-23', 'Journal')], [])
    assert _cats(out)['400001391'] == 'VENDOR_EXPLAIN'


def test_an_unidentifiable_payment_is_theirs_to_explain():
    out = reconcile([_row('1400019887', -88318.36, '2026-02-26', 'Payment')], [])
    assert _cats(out)['1400019887'] == 'VENDOR_EXPLAIN'


def test_a_payment_we_made_that_they_have_not_credited():
    out = reconcile([], [_row('128263003647', -10933.47, '2026-03-30', 'Payment')])
    assert _cats(out)['128263003647'] == 'WE_SENT'


# ── bank charge vs dispute ───────────────────────────────────────────────
def test_a_small_payment_difference_is_a_bank_charge():
    """57.50 on a 28,840 wire is a fee deducted in transit, not a disagreement
    about the debt."""
    out = reconcile([_row('P-1', -28782.64, '2026-02-13', 'Payment')],
                    [_row('P-1', -28840.14, '2026-02-13', 'Payment')])
    assert _cats(out)['P-1'] == 'BANK_CHARGE'


def test_a_large_invoice_difference_is_a_dispute():
    """8066000720: they billed 9,090.22, we booked 3,636.09. That is an
    argument about what was supplied, not a fee."""
    out = reconcile([_row('8066000720', 9090.22, '2025-09-17')],
                    [_row('8066000720', 3636.09, '2025-09-17')])
    assert _cats(out)['8066000720'] == 'TO_QUERY'


def test_a_large_payment_difference_is_still_a_dispute():
    """Past the fee threshold a payment gap stops being explainable as one."""
    out = reconcile([_row('P-9', -50000.00, '2026-05-01', 'Payment')],
                    [_row('P-9', -42000.00, '2026-05-01', 'Payment')])
    assert _cats(out)['P-9'] == 'TO_QUERY'


# ── noise is named as noise ──────────────────────────────────────────────
def test_contras_and_history_are_not_open_items():
    vendor = [_row('CLR-A', 1000.0, '2026-04-15'),
              _row('CLR-B', -1000.0, '2026-04-15'),
              _row('OLD-1', 500.0, '2024-01-01'),
              _row('REAL', 250.0, '2026-05-01')]
    zoho = [_row('REAL', 250.0, '2026-05-01'), _row('Z-1', 10.0, '2025-06-01')]
    out = reconcile(vendor, zoho)
    c = _cats(out)
    assert c['CLR-A'] == c['CLR-B'] == 'NOISE'
    assert c['OLD-1'] == 'NOISE'
    assert c['REAL'] == 'MATCHED'


def test_every_row_carries_a_category_and_the_totals_add_up():
    vendor = [_row('A', 100.0, '2026-01-01'), _row('B', 200.0, '2026-02-01')]
    zoho = [_row('A', 100.0, '2026-01-01')]
    out = reconcile(vendor, zoho)
    assert all(r.get('category') for r in out['results'])
    assert sum(b['rows'] for b in out['summary']['byCategory'].values()) \
        == len(out['results'])


# ── no control total ─────────────────────────────────────────────────────
def test_a_statement_with_no_closing_balance_is_flagged():
    """Honasa's file is a transaction dump: no opening, no closing, nothing to
    prove it was read whole."""
    out = reconcile([_row('A', 100.0, '2026-01-01')], [_row('A', 100.0, '2026-01-01')],
                    vendor_states_total=False, zoho_states_total=True)
    s = out['summary']
    assert s['controlTotalMissing'] is True
    assert any('no control total' in f for f in s['findings'])


def test_nothing_is_said_when_both_sides_state_a_total():
    out = reconcile([_row('A', 100.0, '2026-01-01')], [_row('A', 100.0, '2026-01-01')],
                    vendor_states_total=True, zoho_states_total=True)
    s = out['summary']
    assert s['controlTotalMissing'] is False
    assert not any('control total' in f for f in s['findings'])


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
