# PROMPT 15 — AR ageing alongside AP, one shared model, all seven buckets

Extends the AP snapshot work to AR. Same workbook, same weekly cadence, one table for both
sides rather than two parallel builds.

Source: `Finance Team Weekly KPI` (`1vHma4ZIPztkSYPinu2jkDP0qY926-6M-DPZovEJfkxs`).
**80 AP tabs** (Aug-24 → Sep-26) and **73 AR tabs**, already in the same file.

Two facts from the data that shape this:

- **AP `Current` is present on 79 of 80 tabs but empty on five of them** — 10-08, 17-08, 24-08,
  31-08 and 07-09-2026. It returns on 14-09 at AED 563,123. The export broke for five weeks and
  recovered. An empty bucket must therefore be shown as *absent*, not as zero.
- **AR `Current` is missing entirely from 54 of 73 tabs.** Where it exists it is about **76% of
  the balance** (17-08-2026: 10,008,954 of 13,247,887). An AR ageing without it understates the
  book by three quarters.

```
Add AR ageing to the platform and unify it with AP under one model. Do not build a separate AR
table or a separate parser — the buckets are identical and only the counterparty differs.

RESTRUCTURE INTO ONE MODEL
  ageing_snapshots
    id uuid pk
    side text not null,              -- 'AP' | 'AR'
    snapshot_date date not null
    source_tab text not null, source_file text not null
    row_count int, total_amount numeric
    has_current boolean not null,    -- was a Current column present in this tab at all
    ingested_at timestamptz, ingested_by uuid
    UNIQUE (side, snapshot_date)

  ageing_snapshot_lines
    id uuid pk
    snapshot_id uuid references ageing_snapshots on delete cascade
    entity text, party_name text not null, match_key text not null,
    party_id uuid,                   -- vendor_id when AP, customer_id when AR
    current numeric, d1_30 numeric, d31_60 numeric, d61_90 numeric,
    d91_120 numeric, d121_150 numeric, above_150 numeric,
    total numeric not null, unused_credits numeric
    UNIQUE (snapshot_id, entity, party_name)

Migrate any existing AP snapshot data into these tables with side='AP'. Do not lose it.

PARSING — one parser, tolerant of both sides and of header drift
The header is not on row 1: find the first row within the first 8 that contains any of
`Vendor Name`, `vendor_name`, `Customer Name`, `customer_name`, `customer`.
Map by name, accepting every spelling that appears across the history:
  Entity Name | Entity | vendor_id                         -> entity
  Vendor Name | vendor_name | Customer Name | customer_name | customer  -> party_name
  Current | current                                        -> current
  1 - 30 Days | Days_1-30 | days_1-30                      -> d1_30
  31 - 60 Days | Days_31-60 | days_31-60                   -> d31_60
  61 - 90 Days | Days_61-90 | days_61-90                   -> d61_90
  91 - 120 Days | Days_91-120 | days_91-120                -> d91_120
  121 - 150 Days | Days_121-150 | days_121-150             -> d121_150
  Above 150 Days | Days_above-150 | days_above-150         -> above_150
  Total Amount | Total | total                             -> total
  unused_credits_payable | Unused Credits                  -> unused_credits
Header matching is case-insensitive. An unmapped column aborts that tab and names it. Never
guess by position.

Tab names are inconsistent across the history — "AP 14-09-2026", "AP-09-02-2026",
"Ap 26-01-2026", "AP 2-03-2026", "AR 29-06-206", "AR 30-03-3036". Derive `side` from the AP/AR
prefix and parse the date as dd-mm-yyyy, accepting two-digit years. Anything unparseable goes
in front of a person rather than being guessed.

Skip the totals row above the header and blank spacer rows. A row counts only when it has a
party name AND a numeric total.

Validate before committing a tab: the seven bucket columns must sum to the row total within
1.00, on every row. Reject the whole tab if any row fails, and report which rows.

MISSING IS NOT ZERO — the rule that matters most here
  has_current = false      the tab had no Current column at all (54 of 73 AR tabs)
  has_current = true       the column existed, even if every value was zero
Where has_current is false, store `current` as NULL, never 0.
On screen, a NULL Current renders as "—" with a tooltip "not in the source export", and any
total that includes it is marked as incomplete. A zero renders as 0.
This distinction is the whole point: AP shows zero Current for five weeks in Aug-Sep 2026
because the export broke, and AR has no Current at all before Jun-2026. Presenting either as a
real zero misstates the ageing.

THE AR REPORT at /reports/ar — mirror the AP report
Same structure, same components, `side='AR'`:
  - as-at date picker over available AR snapshot dates
  - rows by customer, columns Current through Above 150, plus Total
  - entity filter, subtotals per entity
  - a coverage banner when the selected snapshot has has_current = false:
    "This export did not include the Current bucket. Totals below exclude current receivables."
  - trend: total AR over time, ageing mix as a stacked series, above-150 over time
  - drill from a customer to their lines
  - CSV export carrying the as-at date and the coverage state in its header

AP REPORT — two fixes
  1. Make sure Current is read and displayed. It exists on 79 of 80 tabs; 14-09-2026 carries
     AED 563,123. If the platform currently shows nothing there, the column is being dropped
     on ingest.
  2. Apply the same missing-vs-zero rule, so the five broken August weeks show "—" rather than
     a zero that looks like a real balance.

AP vs AR VIEW at /reports/working-capital
Now that both sides share a model, one page: AP total and AR total by snapshot date, net
position, and the ageing mix side by side. Only where both sides have a snapshot for the same
date. This is the payoff for one table instead of two.

SCOPE BREAKS — mark them
AP vendor counts step from ~103 to ~190 on 01-06-2026 and ~190 to ~326 on 17-08-2026 as
reporting scope widened. Mark both dates on every AP trend chart and note the series is not
like-for-like across them. Check whether AR has similar steps and mark those too.

MANAGE SCREEN at /admin/ageing-snapshots
List snapshots: side, date, source tab, rows, total, has_current, ingested by and when.
Upload a workbook and ingest all AP and AR tabs in one pass, with a preview before commit
showing per-tab row counts, totals, and which tabs lack a Current column.
Re-ingesting a date replaces that snapshot and records who did it.
```

## Verify

- [ ] 80 AP and 73 AR snapshots ingest from one upload
- [ ] AP 14-09-2026: **326 rows, AED 10,305,116.00, Current 563,123.00**
- [ ] AP 07-09-2026 shows Current as **0**, not NULL — the column was there, the values were not
- [ ] AR 17-08-2026: **57 rows, AED 13,247,887.30, Current 10,008,953.69**
- [ ] AR 24-08-2026 shows Current as **"—"** with the coverage banner — no column in that export
- [ ] exactly **19** AR snapshots have has_current = true
- [ ] every ingested row's seven buckets sum to its total within 1.00
- [ ] the working-capital page only shows dates where both an AP and an AR snapshot exist
- [ ] the two AP scope-break dates are marked on the trend chart
- [ ] re-uploading the same workbook leaves 80 AP and 73 AR snapshots, not 160 and 146

## One thing to fix outside the platform

**Re-export AR with the Current bucket enabled, and check it stays on.** 54 of 73 weeks lack it,
and it is roughly 76% of the receivable. The same intermittent problem hit AP for five weeks in
August. Whatever setting controls that bucket in the Zoho ageing report is being lost between
runs — worth pinning down before more history accumulates without it.
