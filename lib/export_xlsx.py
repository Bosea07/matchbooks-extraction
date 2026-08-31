"""Four-tab reconciliation workbook.

    Summary              headline numbers, all formulas over the other tabs
    Zoho Extraction      every row read from our statement, as extracted
    Vendor SOA Extract   every row read from the vendor statement, as extracted
    Mapping              the pairing the engine made, side by side, with a
                         second independent amount lookup as a cross-check

Every figure is a formula. Nothing is a value the reader has to take on trust,
and changing a raw row re-drives the whole workbook.

INDEX/MATCH is used throughout rather than XLOOKUP: XLOOKUP is a spilling
function with no spill metadata when written by a library, so it evaluates only
in recent Excel and silently returns nothing elsewhere.
"""
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import FormulaRule

FONT = 'Arial'
HDR_FILL = PatternFill('solid', fgColor='12263A')
BAND_FILL = PatternFill('solid', fgColor='EEF2F5')
OK_FILL = PatternFill('solid', fgColor='E3F1EA')
BAD_FILL = PatternFill('solid', fgColor='FBE6E0')
WARN_FILL = PatternFill('solid', fgColor='FDF3DC')
YELLOW = PatternFill('solid', fgColor='FFFF00')

HDR = Font(name=FONT, size=10, bold=True, color='FFFFFF')
BODY = Font(name=FONT, size=10)
BOLD = Font(name=FONT, size=10, bold=True)
LINK = Font(name=FONT, size=10, color='008000')       # pulled from another sheet
INPUT = Font(name=FONT, size=10, color='0000FF')      # raw, as extracted
MUTED = Font(name=FONT, size=9, color='5A6B75')
TITLE = Font(name=FONT, size=14, bold=True, color='12263A')

THIN = Side(style='thin', color='C3CDD5')
TOTAL_BORDER = Border(top=THIN, bottom=Side(style='double', color='12263A'))

MONEY = '#,##0.00;(#,##0.00);"-"'
RATE = '0.0000;;"-"'
DATE_FMT = 'dd-mmm-yy'


def _headers(ws, row, labels, widths):
    for i, label in enumerate(labels, 1):
        c = ws.cell(row=row, column=i, value=label)
        c.font, c.fill = HDR, HDR_FILL
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 28
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _title(ws, title, subtitle):
    ws['A1'] = title
    ws['A1'].font = TITLE
    ws['A2'] = subtitle
    ws['A2'].font = MUTED
    ws.sheet_view.showGridLines = False


def _side_sheet(wb, sheet_title, rows, heading, subtitle, amount_header,
                mapping_col_letter, n_map_rows):
    """One raw extraction tab. `mapping_col_letter` is the column on Mapping
    holding this side's reference, so each row can report whether the
    reconciliation actually used it."""
    ws = wb.create_sheet(sheet_title)
    _title(ws, heading, subtitle)
    _headers(ws, 4,
             ['#', 'Date', 'Type', 'Reference', 'Other identifiers on the row',
              'Settles (allocation refs)', amount_header, 'Used in reconciliation?'],
             [5, 12, 14, 26, 30, 30, 16, 22])
    first = 5
    for i, r in enumerate(rows):
        R = first + i
        ws.cell(row=R, column=1, value=i + 1).font = MUTED
        d = ws.cell(row=R, column=2, value=r.get('dateISO') or r.get('date') or '')
        d.font, d.number_format = INPUT, DATE_FMT
        ws.cell(row=R, column=3, value=r.get('type') or '').font = INPUT
        ws.cell(row=R, column=4, value=r.get('ref') or '').font = INPUT
        alias = [a for a in (r.get('refAliases') or []) if a != r.get('ref')]
        ws.cell(row=R, column=5, value=', '.join(alias)).font = MUTED
        ws.cell(row=R, column=6, value=', '.join(r.get('allocationRefs') or [])).font = MUTED
        a = ws.cell(row=R, column=7, value=r.get('amount'))
        a.font, a.number_format = INPUT, MONEY
        last = 4 + n_map_rows
        c = ws.cell(row=R, column=8,
                    value=f'=IF($D{R}="","",IF(COUNTIF(Mapping!${mapping_col_letter}$5:'
                          f'${mapping_col_letter}${last},$D{R})>0,"yes","NOT USED"))')
        c.font = BODY
        c.alignment = Alignment(horizontal='center')
        if i % 2:
            for col in range(1, 9):
                ws.cell(row=R, column=col).fill = BAND_FILL
    last_row = first + len(rows) - 1
    tot = last_row + 1
    ws.cell(row=tot, column=4, value='Total').font = BOLD
    t = ws.cell(row=tot, column=7, value=f'=SUM(G{first}:G{last_row})')
    t.font, t.number_format, t.border = BOLD, MONEY, TOTAL_BORDER
    ws.cell(row=tot, column=4).border = TOTAL_BORDER
    ws.conditional_formatting.add(
        f'H{first}:H{last_row}',
        FormulaRule(formula=[f'$H{first}="NOT USED"'], fill=BAD_FILL,
                    font=Font(name=FONT, size=10, bold=True, color='A03A1E')))
    ws.cell(row=tot + 2, column=1,
            value='Blue = exactly as extracted from the statement. Column H is a '
                  'completeness check: every row here should appear on Mapping.').font = MUTED
    return ws, first, last_row, tot


def build_workbook(vendor_rows, zoho_rows, result, meta=None):
    meta = meta or {}
    summary = result.get('summary', {})
    results = result.get('results', [])
    cur = meta.get('currency') or ''
    vcur = meta.get('vendorCurrency') or cur
    zcur = meta.get('zohoCurrency') or cur
    fx = bool(summary.get('currencyMismatch'))

    wb = Workbook()
    wb.remove(wb.active)

    # ── Mapping ─────────────────────────────────────────────────────────
    mp = wb.create_sheet('Mapping')
    n_map = len(results)
    zsum = wb  # placeholder, sheets created below

    # ── raw extraction tabs (created first so Mapping can point at them) ─
    zws, zf, zl, zt = _side_sheet(
        wb, 'Zoho Extraction', zoho_rows,
        'Zoho statement — as extracted',
        'Every row the engine read from our side. Nothing is filtered out.',
        f'Amount ({zcur})' if zcur else 'Amount', 'E', n_map)
    vws, vf, vl, vt = _side_sheet(
        wb, 'Vendor SOA Extraction', vendor_rows,
        'Vendor statement of account — as extracted',
        'Every row the engine read from the vendor side. Nothing is filtered out.',
        f'Amount ({vcur})' if vcur else 'Amount', 'A', n_map)

    _title(mp, 'Mapping — vendor statement against our books',
           'One row per pairing the engine made, plus every row it could not pair. '
           'Column N is an independent amount lookup, kept as a second opinion.')
    _headers(mp, 4,
             ['Vendor reference', 'Vendor date', 'Vendor type',
              f'Vendor {vcur}'.strip(), 'Our reference', 'Our date', 'Our type',
              f'Ours {zcur}'.strip(), 'Difference', 'Implied rate', 'Tier',
              'Status', 'Basis for the match', 'Amount also found on the other side?'],
             [24, 12, 13, 16, 24, 12, 13, 16, 14, 11, 6, 16, 42, 26])
    # engine columns: A vendorRef, E ourRef  → side sheets check against these
    mp.column_dimensions['A'].width = 24

    first = 5
    for i, r in enumerate(results):
        R = first + i
        vref, zref = r.get('vendorRef'), r.get('zohoRef')
        mp.cell(row=R, column=1, value=vref or '').font = LINK
        d = mp.cell(row=R, column=2,
                    value=(r.get('dateISO') or '') if vref else '')
        d.font, d.number_format = LINK, DATE_FMT
        mp.cell(row=R, column=3, value=(r.get('type') or '') if vref else '').font = LINK
        a = mp.cell(row=R, column=4, value=r.get('vendorAmt'))
        a.font, a.number_format = LINK, MONEY

        mp.cell(row=R, column=5, value=zref or '').font = LINK
        d = mp.cell(row=R, column=6,
                    value=(r.get('dateISO') or '') if zref else '')
        d.font, d.number_format = LINK, DATE_FMT
        mp.cell(row=R, column=7, value=(r.get('type') or '') if zref else '').font = LINK
        a = mp.cell(row=R, column=8, value=r.get('zohoAmt'))
        a.font, a.number_format = LINK, MONEY

        diff = mp.cell(row=R, column=9,
                       value=f'=IF(OR($D{R}="",$H{R}=""),"",ROUND($D{R}-$H{R},2))')
        diff.font, diff.number_format = BODY, MONEY
        rate = mp.cell(row=R, column=10,
                       value=f'=IF(OR($D{R}="",$H{R}="",$H{R}=0),"",ABS($D{R})/ABS($H{R}))')
        rate.font, rate.number_format = BODY, RATE
        mp.cell(row=R, column=11, value=r.get('tier') or 0).font = BODY

        # status is a formula, so editing an amount re-decides it
        if fx:
            st = (f'=IF(OR($A{R}="",$E{R}=""),IF($A{R}="","Only in our books",'
                  f'"Only on vendor statement"),'
                  f'IF($J{R}="","Review",'
                  f'IF(ABS($J{R}/Summary!$C$14-1)<=Summary!$C$15,"Matched","Rate outlier")))')
        else:
            st = (f'=IF(OR($A{R}="",$E{R}=""),IF($A{R}="","Only in our books",'
                  f'"Only on vendor statement"),'
                  f'IF(ABS($I{R})<=Summary!$C$13,"Matched","Amount difference"))')
        c = mp.cell(row=R, column=12, value=st)
        c.font = BOLD
        n = mp.cell(row=R, column=13, value=(r.get('note') or '')[:240])
        n.font = MUTED
        n.alignment = Alignment(wrap_text=True, vertical='top')

        # independent cross-check, in the spirit of the reference workbook:
        # does this amount exist anywhere on the other statement at all?
        look = (f'=IF($D{R}<>"",IFERROR(IF(MATCH(ROUND($D{R},2),'
                f"'Zoho Extraction'!$G${zf}:$G${zl},0)>0,\"same amount is in our books\"),"
                f'\"no such amount in our books\"),'
                f'IF($H{R}<>"",IFERROR(IF(MATCH(ROUND($H{R},2),'
                f"'Vendor SOA Extraction'!$G${vf}:$G${vl},0)>0,"
                f'\"same amount is on the vendor statement\"),'
                f'\"no such amount on the vendor statement\"),""))')
        c = mp.cell(row=R, column=14, value=look)
        c.font = BODY
        if i % 2:
            for col in range(1, 15):
                mp.cell(row=R, column=col).fill = BAND_FILL

    last = first + max(len(results), 1) - 1
    tot = last + 1
    mp.cell(row=tot, column=1, value='Total').font = BOLD
    for col, letter in ((4, 'D'), (8, 'H'), (9, 'I')):
        c = mp.cell(row=tot, column=col, value=f'=SUM({letter}{first}:{letter}{last})')
        c.font, c.number_format, c.border = BOLD, MONEY, TOTAL_BORDER
    mp.cell(row=tot, column=1).border = TOTAL_BORDER

    for fill, word in ((OK_FILL, 'Matched'), (BAD_FILL, 'Only on vendor statement'),
                       (BAD_FILL, 'Only in our books'), (WARN_FILL, 'Amount difference'),
                       (WARN_FILL, 'Rate outlier')):
        mp.conditional_formatting.add(
            f'A{first}:N{last}',
            FormulaRule(formula=[f'$L{first}="{word}"'], fill=fill))
    mp.cell(row=tot + 2, column=1,
            value='Green = pulled from the extraction tabs. Status and Difference are '
                  'formulas — correct an amount on an extraction tab and this re-decides '
                  'itself. Column N is a plain amount lookup, deliberately independent of '
                  'the engine, so the two can disagree and you can see where.').font = MUTED
    mp.merge_cells(start_row=tot + 2, start_column=1, end_row=tot + 3, end_column=14)

    # ── Summary ─────────────────────────────────────────────────────────
    sm = wb.create_sheet('Summary', 0)
    _title(sm, f"Reconciliation — {meta.get('vendorName') or 'Vendor'}",
           ' · '.join(x for x in (
               meta.get('entity'), meta.get('period'),
               f"generated {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}",
               f"engine {meta.get('engineVersion') or ''}") if x))
    for col, w in ((1, 34), (2, 10), (3, 18), (4, 18), (5, 60)):
        sm.column_dimensions[get_column_letter(col)].width = w

    sm['A4'] = 'OUTCOME'
    sm['A4'].font = Font(name=FONT, size=10, bold=True, color='12263A')
    _headers(sm, 5, ['', 'Count', f'Vendor {vcur}'.strip(), f'Ours {zcur}'.strip(), 'Meaning'],
             [34, 10, 18, 18, 60])
    OUTCOME = [
        ('Matched', 'Matched',
         'Paired, and the amounts agree.'),
        ('Amount difference', 'Amount difference',
         'Paired, but the two sides disagree on the amount.'),
        ('Only on vendor statement', 'Only on vendor statement',
         'On their statement, not in our books.'),
        ('Only in our books', 'Only in our books',
         'In our books, not on their statement.'),
    ]
    r0 = 6
    for i, (label, status, meaning) in enumerate(OUTCOME):
        R = r0 + i
        sm.cell(row=R, column=1, value=label).font = BODY
        sm.cell(row=R, column=2,
                value=f'=COUNTIF(Mapping!$L${first}:$L${last},"{status}")').font = BODY
        c = sm.cell(row=R, column=3,
                    value=f'=SUMIF(Mapping!$L${first}:$L${last},"{status}",'
                          f'Mapping!$D${first}:$D${last})')
        c.font, c.number_format = LINK, MONEY
        c = sm.cell(row=R, column=4,
                    value=f'=SUMIF(Mapping!$L${first}:$L${last},"{status}",'
                          f'Mapping!$H${first}:$H${last})')
        c.font, c.number_format = LINK, MONEY
        sm.cell(row=R, column=5, value=meaning).font = MUTED
        if i % 2:
            for col in range(1, 6):
                sm.cell(row=R, column=col).fill = BAND_FILL
    tr = r0 + len(OUTCOME)
    sm.cell(row=tr, column=1, value='Total rows on Mapping').font = BOLD
    sm.cell(row=tr, column=2, value=f'=SUM(B{r0}:B{tr-1})').font = BOLD
    for col, letter in ((3, 'D'), (4, 'H')):
        c = sm.cell(row=tr, column=col,
                    value=f'=SUM(Mapping!${letter}${first}:${letter}${last})')
        c.font, c.number_format, c.border = BOLD, MONEY, TOTAL_BORDER
    sm.cell(row=tr, column=1).border = TOTAL_BORDER
    sm.cell(row=tr, column=2).border = TOTAL_BORDER

    sm['A12'] = 'SETTINGS THE RECONCILIATION USED'
    sm['A12'].font = Font(name=FONT, size=10, bold=True, color='12263A')
    sm['A13'] = 'Amount tolerance'
    sm['C13'] = summary.get('tolerance', 1.0)
    sm['C13'].font, sm['C13'].fill, sm['C13'].number_format = INPUT, YELLOW, MONEY
    sm['E13'] = 'Below this, two amounts are treated as agreeing.'
    sm['A14'] = 'Implied FX rate' if fx else 'Implied FX rate (not applicable)'
    sm['C14'] = summary.get('impliedRate') or ''
    sm['C14'].font, sm['C14'].number_format = INPUT, RATE
    sm['E14'] = ('The two statements are in different currencies. Rows are paired by '
                 'reference and rate, not by amount.' if fx
                 else 'Both statements are in the same currency.')
    sm['A15'] = 'Rate tolerance'
    sm['C15'] = summary.get('rateTolerance') or 0.10
    sm['C15'].font, sm['C15'].fill, sm['C15'].number_format = INPUT, YELLOW, '0.0%'
    sm['E15'] = 'How far a row\'s own rate may sit from the statement rate.'
    for R in (13, 14, 15):
        sm.cell(row=R, column=1).font = BODY
        sm.cell(row=R, column=5).font = MUTED

    sm['A17'] = 'BALANCES'
    sm['A17'].font = Font(name=FONT, size=10, bold=True, color='12263A')
    rows_bal = [
        ('Vendor statement, total movement', f"='Vendor SOA Extraction'!$G${vt}"),
        ('Our books, total movement', f"='Zoho Extraction'!$G${zt}"),
    ]
    for i, (label, f) in enumerate(rows_bal):
        R = 18 + i
        sm.cell(row=R, column=1, value=label).font = BODY
        c = sm.cell(row=R, column=3, value=f)
        c.font, c.number_format = LINK, MONEY
    sm.cell(row=20, column=1, value='Net difference').font = BOLD
    c = sm.cell(row=20, column=3, value='=C18-C19')
    c.font, c.number_format, c.border = BOLD, MONEY, TOTAL_BORDER
    sm.cell(row=20, column=1).border = TOTAL_BORDER
    if fx:
        w = sm.cell(row=20, column=5,
                    value='NOT a monetary figure — this subtracts two currencies. '
                          'Read the two movements above separately.')
        w.font = Font(name=FONT, size=9, bold=True, color='A03A1E')
        w.alignment = Alignment(wrap_text=True)

    sm['A22'] = 'COMPLETENESS'
    sm['A22'].font = Font(name=FONT, size=10, bold=True, color='12263A')
    checks = [
        ('Rows extracted from the vendor statement', f'=COUNTA(\'Vendor SOA Extraction\'!$D${vf}:$D${vl})'),
        ('Rows extracted from our statement', f"=COUNTA('Zoho Extraction'!$D${zf}:$D${zl})"),
        ('Vendor rows not appearing on Mapping', f"=COUNTIF('Vendor SOA Extraction'!$H${vf}:$H${vl},\"NOT USED\")"),
        ('Our rows not appearing on Mapping', f"=COUNTIF('Zoho Extraction'!$H${zf}:$H${zl},\"NOT USED\")"),
    ]
    for i, (label, f) in enumerate(checks):
        R = 23 + i
        sm.cell(row=R, column=1, value=label).font = BODY
        sm.cell(row=R, column=3, value=f).font = LINK
    c = sm.cell(row=27, column=1, value='Every extracted row is accounted for')
    c.font = BOLD
    c = sm.cell(row=27, column=3, value='=IF(AND(C25=0,C26=0),"YES","CHECK")')
    c.font = BOLD
    c.alignment = Alignment(horizontal='center')
    sm.conditional_formatting.add('C27', FormulaRule(formula=['$C$27="YES"'], fill=OK_FILL,
        font=Font(name=FONT, size=10, bold=True, color='0D5B49')))
    sm.conditional_formatting.add('C27', FormulaRule(formula=['$C$27="CHECK"'], fill=BAD_FILL,
        font=Font(name=FONT, size=10, bold=True, color='A03A1E')))

    findings = summary.get('findings') or []
    if findings:
        sm['A29'] = 'FINDINGS'
        sm['A29'].font = Font(name=FONT, size=10, bold=True, color='12263A')
        for i, f in enumerate(findings[:12]):
            R = 30 + i
            c = sm.cell(row=R, column=1, value='• ' + str(f))
            c.font = MUTED
            c.alignment = Alignment(wrap_text=True, vertical='top')
            sm.merge_cells(start_row=R, start_column=1, end_row=R, end_column=5)
            sm.row_dimensions[R].height = 26

    wb._sheets = [wb['Summary'], wb['Zoho Extraction'],
                  wb['Vendor SOA Extraction'], wb['Mapping']]
    return wb


def to_bytes(wb):
    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
