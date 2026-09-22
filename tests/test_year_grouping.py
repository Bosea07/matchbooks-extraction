"""v2.14 — grouping a multi-year statement by calendar year.

Honasa's ledger runs Feb 2024 to Aug 2026. A 2024 exception still open is a
different conversation from one raised last month, so the results are grouped
by year — but only AFTER matching. Reconciling year by year would turn every
cross-year settlement into a pair of phantom exceptions.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.reconcile import reconcile


def _row(ref, amt, date, typ='Invoice'):
    return {'ref': ref, 'refRaw': ref, 'amount': amt, 'date': date,
            'dateISO': date, 'type': typ}


# ── grouping ─────────────────────────────────────────────────────────────
def test_results_are_grouped_by_calendar_year():
    vendor = [_row('A-1', 100.0, '2024-03-01'),
              _row('A-2', 200.0, '2025-06-01'),
              _row('A-3', 300.0, '2026-08-01')]
    zoho = [_row('A-1', 100.0, '2024-03-01'),
            _row('A-3', 300.0, '2026-08-01')]
    s = reconcile(vendor, zoho)['summary']
    assert s['years'] == ['2024', '2025', '2026']
    assert s['multiYear'] is True
    assert s['byYear']['2024']['matched'] == 1
    assert s['byYear']['2025']['extraInVendor'] == 1
    assert s['byYear']['2026']['matched'] == 1


def test_each_year_carries_its_own_totals():
    vendor = [_row('A-1', 100.0, '2025-06-01'), _row('A-2', 250.0, '2026-06-01')]
    zoho = [_row('A-1', 100.0, '2025-06-01')]
    by = reconcile(vendor, zoho)['summary']['byYear']
    assert by['2025']['vendorTotal'] == 100.0
    assert by['2025']['zohoTotal'] == 100.0
    assert by['2025']['netDifference'] == 0.0
    assert by['2026']['vendorTotal'] == 250.0
    assert by['2026']['netDifference'] == 250.0


def test_year_totals_add_up_to_the_whole():
    vendor = [_row('A-1', 100.0, '2024-03-01'),
              _row('A-2', 200.0, '2025-06-01'),
              _row('A-3', 300.0, '2026-08-01')]
    zoho = [_row('A-1', 90.0, '2024-03-01')]
    out = reconcile(vendor, zoho)
    s = out['summary']
    assert round(sum(b['vendorTotal'] for b in s['byYear'].values()), 2) == 600.0
    assert round(sum(b['zohoTotal'] for b in s['byYear'].values()), 2) == 90.0
    assert sum(b['rows'] for b in s['byYear'].values()) == len(out['results'])


def test_every_result_row_is_labelled_with_its_year():
    out = reconcile([_row('A-1', 100.0, '2026-05-05')], [])
    assert out['results'][0]['year'] == '2026'


def test_a_single_year_statement_is_not_flagged_as_multi_year():
    vendor = [_row('A-1', 100.0, '2026-01-05'), _row('A-2', 200.0, '2026-09-05')]
    s = reconcile(vendor, [])['summary']
    assert s['years'] == ['2026']
    assert s['multiYear'] is False


def test_undated_rows_get_their_own_bucket_not_a_wrong_year():
    vendor = [_row('A-1', 100.0, '2026-05-05'),
              {'ref': 'A-2', 'refRaw': 'A-2', 'amount': 50.0,
               'date': '', 'dateISO': None, 'type': 'Invoice'}]
    s = reconcile(vendor, [])['summary']
    assert 'undated' in s['byYear']
    assert s['byYear']['undated']['rows'] == 1
    assert s['years'] == ['2026']


# ── matching still spans years ───────────────────────────────────────────
def test_a_charge_settled_in_a_later_year_still_matches():
    """The whole reason grouping happens after matching."""
    vendor = [_row('INV-500', 5000.0, '2024-11-30')]
    zoho = [_row('INV-500', 5000.0, '2024-11-30')]
    s = reconcile(vendor, zoho)['summary']
    assert s['matched'] == 1
    assert s['extraInVendor'] == 0 and s['missingInVendor'] == 0


def test_a_cross_year_pair_is_matched_and_flagged():
    vendor = [_row('INV-600', 7000.0, '2025-12-28')]
    zoho = [_row('INV-600', 7000.0, '2026-01-04')]
    out = reconcile(vendor, zoho)
    s = out['summary']
    assert s['matched'] == 1
    assert s['crossYearPairs'] == 1
    assert out['results'][0]['crossYear'] is True
    assert any('span two calendar years' in f for f in s['findings'])


def test_same_year_pairs_are_not_flagged():
    vendor = [_row('INV-700', 100.0, '2026-02-01')]
    zoho = [_row('INV-700', 100.0, '2026-03-01')]
    s = reconcile(vendor, zoho)['summary']
    assert s['crossYearPairs'] == 0


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
