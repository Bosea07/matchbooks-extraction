"""Regression tests for v2.8 — the Seven Turns Pvt Ltd failures.

Four independent defects, each pinned here:
  1. tier-2 relaxed matching paired credit notes with bills
  2. REF_HINT truncated leading reference segments
  3. a "Transactions" type column was never read, so types came from narration
  4. an INR ledger was subtracted from AED books and reported as AED

Run: python -m pytest tests/ -q      (or: python tests/test_currency_and_types.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.normalize import REF_HINT, canon_type
from lib.parser import parse_grid
from lib.reconcile import reconcile


# ── 1. relaxed reference matches must agree in direction ────────────────
def test_tier2_will_not_pair_a_credit_note_with_a_bill():
    """MH/EXC/2/25-26 and M/002/25-26 both reduce to digits 22526. Before the
    sign guard they paired, and a −65,238 credit note was reported as a
    confident amount difference against a +4,600 bill."""
    vendor = [{'ref': 'MH/EXC/2/25-26', 'type': 'Credit Note', 'amount': -65238.36},
              {'ref': 'MH/EXC/4/25-26', 'type': 'Credit Note', 'amount': -39996.50}]
    zoho = [{'ref': 'M/002/25-26', 'type': 'Bill', 'amount': 4600.0},
            {'ref': 'M/004/25-26', 'type': 'Bill', 'amount': 19300.0}]
    r = reconcile(vendor, zoho)
    opposite = [x for x in r['results']
                if x['vendorAmt'] is not None and x['zohoAmt'] is not None
                and (x['vendorAmt'] >= 0) != (x['zohoAmt'] >= 0)]
    assert not opposite, 'a relaxed ref match must never cross a sign boundary'
    assert r['summary']['extraInVendor'] == 2
    assert r['summary']['missingInVendor'] == 2


# ── 2. references keep their leading segments ───────────────────────────
def test_multi_segment_references_are_not_truncated():
    for text, want in (
            ('STE/M/001/25-26 - due on 18 Apr 2025', 'STE/M/001/25-26'),
            ('MH/EXC/2/25-26\nAED2,785.00 for payment of\nSTE/M/003/25-26', 'MH/EXC/2/25-26'),
            ('MH/E/C/1/26-27', 'MH/E/C/1/26-27'),
            ('STE/M001/26-27 - due on 09 Apr 2026', 'STE/M001/26-27')):
        m = REF_HINT.search(text)
        assert m and m.group(0) == want, f'{text!r} -> {m and m.group(0)!r}, want {want!r}'


def test_single_segment_references_still_match():
    for ref in ('SO-0822', 'INV-2186', 'xxxxxxxxxxxx0330', '597872319',
                '016/26/00012', 'Invoice No.15282', '5.00E+15', 'PHUB1234'):
        m = REF_HINT.search(ref)
        assert m and m.group(0) == ref, ref


# ── 3. the statement's own type column wins over its narration ──────────
def test_transactions_column_is_read_as_the_type_column():
    """A Credits row whose narration says 'for payment of' must not become a
    Payment. The answer is sitting in the Transactions column."""
    grid = [
        ['Date', 'Transactions', 'Details', 'Amount', 'Payments', 'Balance'],
        ['25 Jun 2025', 'Credits', 'MH/EXC/2/25-26\nAED2,785.00 for payment of\nSTE/M/003/25-26',
         None, '2,785.00', '37,732.70'],
        ['31 May 2025', 'Bill', 'STE/M/004/25-26 - due on 31 May 2025', '19,300.00', None, '40,517.70'],
        ['14 Oct 2025', 'Payment Made', '552309294\nKWA-VP-2016', None, '38,506.22', '14,154.48'],
    ]
    records, meta = parse_grid(grid)
    assert meta['typeCol'] == 1
    by_ref = {r['ref']: r for r in records}
    assert by_ref['MH/EXC/2/25-26']['type'] == 'Credit Note'
    assert by_ref['STE/M/004/25-26']['type'] == 'Bill'
    assert by_ref['552309294']['type'] == 'Payment'


def test_canon_type_maps_statement_wording():
    assert canon_type('Payment Made') == 'Payment'
    assert canon_type('Credits') == 'Credit Note'
    assert canon_type('Bill') == 'Bill'
    assert canon_type('Invoices') == 'Invoice'
    assert canon_type('Something Else') == 'Something Else'


def test_bills_keep_their_sign_when_narration_mentions_payment():
    """Cambridge: 'SO-0750 - due on 21 Jan 2026 / £417.90 from payment
    KWA-VP-1225' is a Bill of +417.90. Typing it from the narration made it a
    Payment and flipped it to −417.90, which is what put Cambridge's net
    difference out by 7,665.90."""
    grid = [
        ['Date', 'Transactions', 'Details', 'Amount', 'Payments', 'Balance'],
        ['21 Jan 2026', 'Bill',
         '#SO-0750 - due on 21 Jan 2026\n£417.90 from payment KWA-\nVP-1225',
         '417.90', None, '-2,513.92'],
    ]
    records, _ = parse_grid(grid)
    assert len(records) == 1
    assert records[0]['type'] == 'Bill'
    assert records[0]['amount'] == 417.90


# ── 4. two currencies are never subtracted ──────────────────────────────
def _two_currency_books(rate=23.5):
    """Same eight transactions, vendor side in INR, ours in AED."""
    aed = [('STE/M/001/25-26', 'Bill', 37200.0), ('STE/M/002/25-26', 'Bill', 4600.0),
           ('STE/M/003/25-26', 'Bill', 24650.0), ('STE/M/004/25-26', 'Bill', 19300.0),
           ('MH/EXC/2/25-26', 'Credit Note', -2785.0), ('MH/EXC/4/25-26', 'Credit Note', -1702.0),
           ('MH/EXC/6/25-26', 'Credit Note', -6927.0), ('MH/EXC/7/25-26', 'Credit Note', -368.0)]
    zoho = [{'ref': r, 'type': t, 'amount': a} for r, t, a in aed]
    vendor = [{'ref': r, 'type': t, 'amount': round(a * rate, 2)} for r, t, a in aed]
    return vendor, zoho


def test_currency_mismatch_is_detected_and_flagged():
    vendor, zoho = _two_currency_books()
    s = reconcile(vendor, zoho)['summary']
    assert s['currencyMismatch'] is True
    assert s['netDifferenceMeaningful'] is False
    assert 23.0 < s['impliedRate'] < 24.0
    assert any('different currencies' in f for f in s['findings'])
    assert s['findings'][0].startswith('The two statements are in different currencies')


def test_rows_match_across_currencies_and_carry_their_rate():
    vendor, zoho = _two_currency_books()
    r = reconcile(vendor, zoho)
    assert r['summary']['matched'] == 8
    assert r['summary']['amountDiff'] == 0
    for x in r['results']:
        assert x['impliedRate'] is not None
        assert x['diff'] is None, 'a cross-currency subtraction is not a difference'


def test_a_row_at_the_wrong_rate_is_flagged_not_swallowed():
    """FX drift is normal; a row 40% off the statement's own rate is not."""
    vendor, zoho = _two_currency_books()
    vendor[0]['amount'] = round(vendor[0]['amount'] * 1.4, 2)
    r = reconcile(vendor, zoho)
    assert r['summary']['amountDiff'] == 1
    bad = [x for x in r['results'] if x['status'] == 'AMOUNT_DIFF']
    assert len(bad) == 1 and 'STE/M/001/25-26' in bad[0]['ref']
    assert 'off the statement rate' in bad[0]['note']


def test_same_currency_books_are_untouched_by_fx_logic():
    _, zoho = _two_currency_books()
    vendor = [dict(z) for z in zoho]
    s = reconcile(vendor, zoho)['summary']
    assert s['currencyMismatch'] is False
    assert s['netDifferenceMeaningful'] is True
    assert s['impliedRate'] is None
    assert s['matched'] == 8 and s['netDifference'] == 0.0


def test_combination_matching_is_off_across_currencies():
    """Summing lines from two currencies would be nonsense."""
    vendor, zoho = _two_currency_books()
    vendor.append({'ref': 'ODD/1', 'type': 'Bill', 'amount': 999999.0})
    r = reconcile(vendor, zoho, enable_combos=True)
    assert r['summary']['combosMatched'] == 0


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
