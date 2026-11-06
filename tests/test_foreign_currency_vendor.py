"""v2.21 — a vendor that states INR against books kept in AED (Seven Turns /
KUWA, ledger 1 Apr – 23 Oct 2026).

Two failures, one behind the other.

Extraction: Tally prints "Particulars" across three data columns (Dr/Cr,
ledger, PO) but leaves one blank header cell, so Vch Type, Vch No., Debit and
Credit each sit one column LEFT of their data. Read literally, 3 of 8 rows
survived, typed by their PO numbers; the two column-total rows became invoices
of 1.15m and 1.39m.

Currency: nothing on the ledger says INR, and /reconcile had no way to be told.
With fewer than three reference matches the engine could not infer a rate and
subtracted INR from AED as though they were one currency.

These tests hold the real rows: movement 313.25 = closing 237,762.59 less
opening 237,449.34.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.normalize import canon_type
from lib.parser import parse_grid
from lib.reconcile import reconcile

N = None
LEDGER = [
    ['Seven Turns Pvt Ltd', N, N, N, N, N, N, N],
    ['A Wing 204, Naman Midtown, Senapati', N, N, N, N, N, N, N],
    ['Bapat Marg, Dalamal Tower, Nariman Poi', N, N, N, N, N, N, N],
    ['Mumbai, Mumbai, Maharashtra, 400013', N, N, N, N, N, N, N],
    ['CIN: U52609MH2021PTC406109', N, N, N, N, N, N, N],
    ['Kuwa Food Supplements Trading LLC', N, N, N, N, N, N, N],
    ['Ledger Account', N, N, N, N, N, N, N],
    ['215 Al Shafar Investment Building, Al ', N, N, N, N, N, N, N],
    ['UAE, 0552936300', N, N, N, N, N, N, N],
    [N] * 8,
    ['1-Apr-26 to 23-Oct-26', N, N, N, N, N, N, N],
    ['Date', 'Particulars', N, 'Vch Type', 'Vch No.', 'Debit', 'Credit', N],
    ['2026-04-01', 'Cr', 'Opening Balance', N, N, 237449.34, N, N],
    ['2026-04-09', 'Cr', 'Export Sales A/C', 'PO1188', 'MH-SALES EXPORT', 'STE/M/001/26-27', 493928.82, N],
    ['2026-04-22', 'Dr', 'IDFC Bank - 10112560994', N, 'MH-RECIEPT', 'MH/137/26-27', N, 319478],
    ['2026-05-13', 'Cr', 'Export Sales A/C', 'PO1493', 'MH-SALES EXPORT', 'STE/M/005/26-27', 419160.01, N],
    ['2026-05-18', 'Dr', 'Export Sales A/C', 'PO1188', 'MH-EXPORT-CN', 'MH/E/C/1/26-27', N, 6030.33],
    ['2026-06-29', 'Dr', 'IDFC Bank - 10112560994', N, 'MH-RECIEPT', 'MH/608/26-27', N, 493928],
    ['2026-07-08', 'Dr', 'Export Sales A/C', 'PO1493', 'MH-EXPORT-CN', 'MH/E/C/3/26-27', N, 5284.22],
    ['2026-07-14', 'Dr', 'IDFC Bank - 10112560994', N, 'MH-RECIEPT', 'MH/738/26-27', N, 325817.62],
    ['2026-09-03', 'Cr', 'Export Sales A/C', 'PO1944-1', 'MH-SALES EXPORT', 'STE/M/009/26-27', 237762.59, N],
    [1388300.76, N, N, N, N, N, 1150538.17, N],
    [N, 'Dr', 'Closing Balance', N, N, N, 237762.59, N],
    [1388300.76, N, N, N, N, N, 1388300.76, N],
]


def _parsed():
    return parse_grid([list(r) for r in LEDGER])


# ── extraction ──────────────────────────────────────────────────────────
def test_labels_left_of_their_data_are_shifted_back():
    records, meta = _parsed()
    assert [r['ref'] for r in records] == [
        'STE/M/001/26-27', 'MH/137/26-27', 'STE/M/005/26-27', 'MH/E/C/1/26-27',
        'MH/608/26-27', 'MH/E/C/3/26-27', 'MH/738/26-27', 'STE/M/009/26-27']
    assert any('left of their own data' in w for w in meta['warnings'])


def test_tally_voucher_types_with_a_branch_prefix_are_recognised():
    assert canon_type('MH-SALES EXPORT') == 'Invoice'
    assert canon_type('MH-RECIEPT') == 'Payment'
    assert canon_type('MH-EXPORT-CN') == 'Credit Note'
    assert canon_type('PO1188') == 'PO1188', 'a PO number is not a voucher type'
    records, _ = _parsed()
    types = [r['type'] for r in records]
    assert types.count('Invoice') == 3 and types.count('Payment') == 3 \
        and types.count('Credit Note') == 2, types


def test_column_total_rows_are_not_money():
    records, meta = _parsed()
    assert len(records) == 8 and meta['unreferencedRows'] == 0
    assert any('column-total' in w for w in meta['warnings'])


def test_the_ledger_ties_opening_to_closing():
    _, meta = _parsed()
    t = meta['totalsCheck']
    assert t['ok'], t
    assert t['openingBalance'] == 237449.34 and t['extractedSum'] == 313.25


def test_an_indian_issuer_is_read_as_inr_but_only_as_an_inference():
    records, meta = _parsed()
    assert meta['currency'] == 'INR' and meta['currencySource'] == 'inferred'
    assert all(r['currency'] == 'INR' and r['currencySource'] == 'inferred'
               for r in records)
    assert any('an inference, not a declaration' in w for w in meta['warnings'])


def test_indian_vendors_in_the_data_rows_are_not_the_issuer():
    """Our own AP report lists Indian suppliers line by line. Scanned past the
    header row, every such report was stamped INR."""
    grid = [['KUWA AP Report', N, N, N],
            ['Date', 'Vendor', 'Bill No', 'Amount'],
            ['2026-04-09', 'Seven Turns Pvt Ltd, Mumbai, Maharashtra', 'B-1001', 500],
            ['2026-04-10', 'Mankind Private Limited, India', 'B-1002', 700]]
    _, meta = parse_grid(grid)
    assert meta['currency'] is None


def test_a_uae_address_alone_infers_nothing():
    """Every statement sent to us carries our own UAE address."""
    grid = [['Some Trading LLC', N, N], ['Dubai, UAE', N, N],
            ['Date', 'Invoice No', 'Amount'], ['2026-04-09', 'INV-1001', 500]]
    _, meta = parse_grid(grid)
    assert meta['currency'] is None


# ── reconciliation across currencies ────────────────────────────────────
RATE = 23.9   # INR per AED, about the booked rate for Apr–Sep 2026


def _ours(refs=('STE/M/001/26-27', 'STE/M/005/26-27', 'STE/M/009/26-27'), ccy='AED'):
    vendor = {r['ref']: r for r in _parsed()[0]}
    out = []
    for ref in refs:
        v = vendor[ref]
        out.append({'ref': ref, 'refAliases': [ref], 'type': 'Bill',
                    'amount': round(v['amount'] / RATE, 2), 'dateISO': v['dateISO'],
                    'currency': ccy})
    return out


def test_a_supplied_rate_compares_inr_with_aed():
    vendor, _ = _parsed()
    r = reconcile(vendor, _ours(), vendor_currency='INR', zoho_currency='AED',
                  fx_rate=RATE)
    s = r['summary']
    assert s['fxRateSource'] == 'declared' and not s['needsFxRate']
    assert not s['netDifferenceMeaningful']
    bills = [x for x in r['results'] if x['vendorRef'].startswith('STE/')]
    assert len(bills) == 3 and all(x['status'] == 'MATCHED' for x in bills), bills


def test_one_matched_row_is_enough_once_the_currencies_are_known_to_differ():
    vendor, _ = _parsed()
    r = reconcile(vendor, _ours(refs=('STE/M/001/26-27',)),
                  vendor_currency='INR', zoho_currency='AED')
    s = r['summary']
    assert s['fxRateSource'] == 'inferred'
    assert abs(s['impliedRate'] - RATE) < 0.05, s['impliedRate']


def test_without_any_rate_amounts_are_not_compared_and_it_says_so():
    """Our bills under different numbers: nothing matches by reference, no rate
    is supplied. Subtracting INR from AED would produce a confident-looking
    difference on every row."""
    vendor, _ = _parsed()
    ours = [dict(b, ref=f'KWB-{50 + i}', refAliases=[f'KWB-{50 + i}'])
            for i, b in enumerate(_ours())]
    r = reconcile(vendor, ours, vendor_currency='INR', zoho_currency='AED')
    s = r['summary']
    assert s['needsFxRate'] and not s['netDifferenceMeaningful']
    assert not any(x['status'] in ('MATCHED', 'AMOUNT_DIFF') and x.get('diff')
                   for x in r['results'])
    assert any('EXCHANGE RATE NEEDED' in f for f in s['findings'])


def test_pegged_currencies_need_no_rate():
    vendor = [{'ref': 'INV-1', 'refAliases': ['INV-1'], 'type': 'Invoice',
               'amount': 1000.0, 'dateISO': '2026-05-01', 'currency': 'USD'}]
    ours = [{'ref': 'BILL-9', 'refAliases': ['BILL-9'], 'type': 'Bill',
             'amount': 3672.5, 'dateISO': '2026-05-01', 'currency': 'AED'}]
    r = reconcile(vendor, ours)
    s = r['summary']
    assert s['fxRateSource'] == 'pegged'
    assert abs(s['impliedRate'] - 1 / 3.6725) < 1e-4   # reported to 4 dp
    assert any(x['status'] == 'MATCHED' for x in r['results']), r['results']


def test_a_currency_printed_on_the_statement_beats_the_selection():
    vendor = [{'ref': 'INV-1', 'refAliases': ['INV-1'], 'type': 'Invoice',
               'amount': 500.0, 'dateISO': '2026-05-01', 'currency': 'AED'}]
    ours = [{'ref': 'INV-1', 'refAliases': ['INV-1'], 'type': 'Bill',
             'amount': 500.0, 'dateISO': '2026-05-01', 'currency': 'AED'}]
    r = reconcile(vendor, ours, vendor_currency='INR')
    s = r['summary']
    assert s['vendorCurrency'] == 'AED' and s['fxRateSource'] is None
    assert 'INR was selected' in str(s)


def test_a_selection_overrides_an_inferred_currency():
    vendor, _ = _parsed()
    r = reconcile(vendor, _ours(), vendor_currency='USD', zoho_currency='AED')
    assert r['summary']['vendorCurrency'] == 'USD'


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
