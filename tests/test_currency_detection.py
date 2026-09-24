"""v2.15 — knowing what currency a statement is in.

The engine could infer an exchange RATE from matched rows but never learned
what either document was denominated in, so the platform labelled a USD
statement (Honasa: "Balance Due $ 127,883.28") as AED. A currency is a stated
fact, not something to infer from ratios.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.normalize import detect_currency
from lib.parser import parse_grid
from lib.reconcile import reconcile


# ── reading the declaration ──────────────────────────────────────────────
def test_iso_codes_are_recognised():
    assert detect_currency('USD') == 'USD'
    assert detect_currency('AED 1,234.00') == 'AED'
    assert detect_currency('Amount in SAR') == 'SAR'


def test_symbols_are_recognised():
    assert detect_currency('$ 127,883.28') == 'USD'
    assert detect_currency('£1,951.50') == 'GBP'
    assert detect_currency('₹7,920') == 'INR'


def test_words_are_recognised():
    assert detect_currency('Dhs. 500') == 'AED'
    assert detect_currency('Total in Dirhams') == 'AED'


def test_a_code_beats_a_symbol():
    """"USD $1,000" is USD, not a guess from the symbol."""
    assert detect_currency('USD $1,000') == 'USD'


def test_nothing_is_better_than_a_guess():
    assert detect_currency('1,234.00') is None
    assert detect_currency('') is None
    assert detect_currency(None) is None


# ── from a currency column ───────────────────────────────────────────────
def test_a_currency_column_is_read_per_row():
    grid = [['Doc No.', 'Doc Type', 'Doc Date', 'Document Currency', 'Amount'],
            [8066000965, 'RV', '25/08/2026', 'USD', 55491.55],
            [8066000960, 'RV', '24/08/2026', 'USD', 898.72]]
    rows, meta = parse_grid(grid)
    assert meta['currency'] == 'USD'
    assert meta['mixedCurrency'] is False
    assert all(r['currency'] == 'USD' for r in rows)


def test_a_mixed_currency_ledger_is_called_out():
    """Honasa's USD ledger carries two INR postings. Summing them is not a
    monetary figure, and the statement must say so."""
    grid = [['Doc No.', 'Doc Type', 'Doc Date', 'Document Currency', 'Amount'],
            [1, 'RV', '01/06/2026', 'USD', 1000.00],
            [2, 'RV', '02/06/2026', 'USD', 2000.00],
            [3, 'DZ', '23/06/2026', 'INR', -7920.00]]
    _, meta = parse_grid(grid)
    assert meta['currency'] == 'USD'
    assert meta['mixedCurrency'] is True
    assert meta['currencyCounts'].get('INR') == 1
    assert any('mixes currencies' in w for w in meta['warnings'])


# ── from the document body ───────────────────────────────────────────────
def test_currency_is_taken_from_a_summary_block():
    """A Zoho SOA prints the currency only in its account summary, above the
    transaction header."""
    grid = [['Account Summary'],
            ['Balance Due', '$ 127,883.28'],
            ['Date', 'Transactions', 'Details', 'Amount'],
            ['02 Jan 2026', 'Bill', '8066000809', 10050.02]]
    _, meta = parse_grid(grid)
    assert meta['currency'] == 'USD'


def test_a_statement_stating_nothing_says_so():
    grid = [['Date', 'Reference', 'Amount'],
            ['01/06/2026', 'INV-1', 100.00]]
    _, meta = parse_grid(grid)
    assert meta['currency'] is None
    assert any('No currency found' in w for w in meta['warnings'])


# ── reconciliation ───────────────────────────────────────────────────────
def _row(ref, amt, date, ccy, typ='Invoice'):
    return {'ref': ref, 'refRaw': ref, 'amount': amt, 'date': date,
            'dateISO': date, 'type': typ, 'currency': ccy}


def test_different_declared_currencies_are_reported():
    vendor = [_row('A-1', 100.0, '2026-05-01', 'USD')]
    zoho = [_row('A-1', 367.25, '2026-05-01', 'AED')]
    s = reconcile(vendor, zoho)['summary']
    assert s['vendorCurrency'] == 'USD'
    assert s['zohoCurrency'] == 'AED'
    assert s['currencyDeclaredMismatch'] is True
    assert any('different currencies' in f for f in s['findings'])


def test_same_currency_raises_nothing():
    vendor = [_row('A-1', 100.0, '2026-05-01', 'AED')]
    zoho = [_row('A-1', 100.0, '2026-05-01', 'AED')]
    s = reconcile(vendor, zoho)['summary']
    assert s['currencyDeclaredMismatch'] is False
    assert s['currencyContradiction'] is False


def test_a_rate_between_two_same_currency_books_is_a_contradiction():
    """Both sides say AED, but the matched rows sit at 3.67 to each other.
    That is not an exchange rate — something has been read wrongly, and the
    engine must refuse to let it pass as an FX difference."""
    vendor = [_row('A-1', 3672.50, '2026-05-01', 'AED'),
              _row('A-2', 7345.00, '2026-05-02', 'AED'),
              _row('A-3', 1836.25, '2026-05-03', 'AED')]
    zoho = [_row('A-1', 1000.0, '2026-05-01', 'AED'),
            _row('A-2', 2000.0, '2026-05-02', 'AED'),
            _row('A-3', 500.0, '2026-05-03', 'AED')]
    s = reconcile(vendor, zoho)['summary']
    assert s['currencyContradiction'] is True
    assert any('CONTRADICTION' in f for f in s['findings'])


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
        print(f'  ok  {fn.__name__}')
    print(f'\n{len(fns)} passed')
