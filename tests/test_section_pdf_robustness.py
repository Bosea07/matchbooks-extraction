"""v2.23 — the Valeo-Kuwa sectioned SOA as it arrives in a PDF, plus due dates.

PDF text extraction damages the layout v2.20 learned from the Excel copy:
  * header labels are cut or wrapped — "Credit Note Date" arrives as "Credit Note",
    so the credit-note block had no date column, was not seen as a block, and its
    13 credit notes were read as more payments (totals still tied, so nothing looked wrong);
  * a block title is glued onto the last cell of the row above it ("08/06/2026\\nCredit Notes").
Both must still give 39 invoices · 7 payments · 13 credit notes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.parser import parse_grid
from tests.test_sectioned_statements import INV_HEAD, INVOICES, PAY_HEAD, PAYMENTS, CREDITS

BLANK = [None] * 7


def _types(recs):
    out = {}
    for r in recs:
        out[r['type']] = out.get(r['type'], 0) + 1
    return out


def _pdf_grid(cn_head, glue_title=False, titles=True):
    g = []
    if titles:
        g.append(['Invoice Summary'] + [None] * 6)
    g.append(INV_HEAD)
    g += [list(r) for r in INVOICES]
    g.append(BLANK)
    if titles:
        g.append(['Payments'] + [None] * 6)
    g.append(PAY_HEAD + [None] * 3)
    pays = [list(r) + [None] * 3 for r in PAYMENTS]
    if glue_title:
        pays[-1][0] = f"{pays[-1][0]}\nCredit Notes"
    g += pays
    if titles and not glue_title:
        g.append(BLANK)
        g.append(['Credit Notes'] + [None] * 6)
    g.append(cn_head + [None] * (7 - len(cn_head)))
    g += [list(r) + [None] * (7 - len(r)) for r in CREDITS]
    return g


def test_credit_note_header_with_its_date_label_cut_off():
    recs, meta = parse_grid(_pdf_grid(['Credit Note', 'Credit Note Number', 'Invoice Number', 'Credit Note Amount', 'Currency']))
    assert _types(recs) == {'Invoice': 39, 'Payment': 7, 'Credit Note': 13}, _types(recs)
    cn = round(sum(r['amount'] for r in recs if r['type'] == 'Credit Note'), 2)
    assert cn == -481761.68, cn


def test_block_title_glued_onto_the_row_above():
    # No title rows at all, and a credit-note header that names no document type:
    # only the glued "Credit Notes" says what the block is.
    recs, meta = parse_grid(_pdf_grid(['Date', 'Number', 'Invoice Number', 'Amount', 'Currency'], glue_title=True, titles=False))
    t = _types(recs)
    assert t.get('Credit Note') == 13 and t.get('Payment') == 7, t
    last_payment = [r for r in recs if r['type'] == 'Payment'][-1]
    assert last_payment['dateISO'] == '2026-06-08', last_payment


def test_due_date_is_read_and_never_replaces_the_document_date():
    recs, _ = parse_grid(_pdf_grid(['Credit Note Date', 'Credit Note Number', 'Invoice Number', 'Credit Note Amount', 'Currency']))
    inv = next(r for r in recs if r['ref'] == 'MF/HR26-27/07844')
    assert inv['dateISO'] == '2026-08-24', inv
    assert inv.get('dueDateISO') == '2026-09-08', inv


def test_a_sheet_with_only_a_due_date_still_has_dates():
    grid = [['Due Date', 'Invoice No', 'Amount'], ['15/10/2026', 'INV-1', '100.00'], ['20/10/2026', 'INV-2', '250.00']]
    recs, _ = parse_grid(grid)
    assert [r['dateISO'] for r in recs] == ['2026-10-15', '2026-10-20'], recs


def test_the_excel_layout_is_unchanged():
    recs, meta = parse_grid(_pdf_grid(['Credit Note Date', 'Credit Note Number', 'Invoice Number', 'Credit Note Amount', 'Currency']))
    assert _types(recs) == {'Invoice': 39, 'Payment': 7, 'Credit Note': 13}
    assert round(sum(r['amount'] for r in recs if r['type'] == 'Invoice'), 2) == 4830591.33
    assert round(sum(r['amount'] for r in recs if r['type'] == 'Payment'), 2) == -2650656.72


if __name__ == '__main__':
    tests = [v for k, v in dict(globals()).items() if k.startswith('test_') and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f'  ok   {t.__name__}')
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f'  FAIL {t.__name__}: {e!r}')
    print(f'{len(tests) - failed} passed' if not failed else f'{failed} failed')
    sys.exit(1 if failed else 0)
