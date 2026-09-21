# PROMPT 7 — YTD sourced from the weekly KPI sheet

Replaces Prompt 6. The difference: **Report (YTD) is not computed from Zoho.** It is the
finance team's own weekly AP tab, ingested as a dated snapshot. Actuals stays live Zoho.

Source: `Finance Team Weekly KPI` (Google Sheet `1vHma4ZIPztkSYPinu2jkDP0qY926-6M-DPZovEJfkxs`),
owner job@feelvaleo.com. **79 ingestible AP tabs, 19 Aug 2024 → 14 Sep 2026** — so this is not
one snapshot, it is a two-year weekly AP series you already have.

The 14-Sep tab totals **AED 10,305,116** across 326 vendor rows and **already excludes 90 of
the 96 reviewed vendors**. Six still leak through and the exclusion list handles those.

```
Add a dated snapshot source for the AP report and make the AP page a two-view page.
Do not change how live Zoho AP is ingested.

TWO VIEWS, chosen by a segmented control, held in the URL
  ?view=actuals        live Zoho, every vendor, nothing excluded
  ?view=ytd&as_at=YYYY-MM-DD    the finance team's weekly AP snapshot for that date
Default to ytd at the latest available snapshot. Name the view on screen, in every chart title
and in every export header.

NEW TABLES

ap_snapshots
  id uuid pk
  snapshot_date date not null unique
  source_tab text not null             -- e.g. "AP 14-09-2026"
  source_file text not null
  row_count int, total_aed numeric
  ingested_at timestamptz, ingested_by uuid

ap_snapshot_lines
  id uuid pk
  snapshot_id uuid references ap_snapshots on delete cascade
  entity text, vendor_name text not null, match_key text not null
  vendor_id uuid              -- resolved against the vendor master, nullable
  current numeric, d1_30 numeric, d31_60 numeric, d61_90 numeric,
  d91_120 numeric, d121_150 numeric, above_150 numeric,
  total numeric not null, unused_credits numeric
  UNIQUE (snapshot_id, entity, vendor_name)

INGESTION — read the tabs, tolerantly
Each AP tab is one weekly snapshot. The header is NOT on row 1: find the row containing
"Vendor Name" or "vendor_name" within the first 8 rows and treat that as the header.

Two header conventions exist across the history. Map both:
  Entity Name | Entity | vendor_id            -> entity
  Vendor Name | vendor_name                   -> vendor_name
  Current | current                           -> current
  1 - 30 Days | days_1-30                     -> d1_30
  31 - 60 Days | days_31-60                   -> d31_60
  61 - 90 Days | days_61-90                   -> d61_90
  91 - 120 Days | days_91-120                 -> d91_120
  121 - 150 Days | days_121-150               -> d121_150
  Above 150 Days | days_above-150             -> above_150
  Total Amount | total                        -> total
  unused_credits_payable                      -> unused_credits
An unmapped header aborts that tab and names the column. Never guess by position.

Tab names are inconsistent — "AP 14-09-2026", "AP-09-02-2026", "Ap 26-01-2026", "AP 2-03-2026",
"AP - 26-08-24", and one typo "AR 30-03-3036". Parse the date as dd-mm-yyyy, accept 2-digit
years, and put anything unparseable in front of a person rather than guessing.

Skip the row above the header that holds column totals, and skip blank spacer rows. A row
counts only when it has a vendor name AND a numeric total.

Validate each tab before committing it: the seven bucket columns must sum to the row total on
every row, within 1.00. If any row fails, reject the whole tab and report which rows failed.
On the 14-Sep tab all 326 rows pass, so this is a real check, not a formality.

Ignore the Lookup and Diff columns entirely. They are an XLOOKUP against the prior week's tab
matched on vendor name alone, ignoring entity, and are wrong wherever a vendor trades under
more than one entity — which is 18 vendor names. Do not import them and do not reproduce them.

EXCLUSIONS still apply on top of the snapshot
Keep ap_excluded_vendors from the previous prompt, seeded from ap_excluded_vendors_seed.csv
(96 rows). Resolve each to a vendor_id and exclude across all entities.
The 14-Sep snapshot already omits 90 of the 96. These six are still present and must be
excluded by the list:
  Heavenly Secrets Private Limited (367,727) · Tabby L.L.C (36,827) ·
  SLICK ORGANICS PRIVATE LIMITED 1 (21,687) · Inmart Commerce Private Limited (10,264) ·
  Cambridge Nutraceuticals (8,564) · MH Enterprises L.L.C (5,394)

LINKING VENDORS AND BILLS
Resolve ap_snapshot_lines.vendor_id against the vendor master using match_key: lowercase,
punctuation stripped, and the suffixes LLC / L.L.C / FZCO / FZE / DMCC / Pvt / Private /
Limited / Ltd / Inc / WLL / Co / Company removed.
Once resolved, every snapshot row drills through to:
  - the vendor record
  - that vendor's open bills read LIVE from Zoho: bill number, bill date, due date, amount,
    balance, ageing bucket
Label that drill-through panel "Live bills as at <today>" — the snapshot row is dated, the
bills underneath it are not, and the two will not agree. Say so rather than letting a reader
assume they match.
Unresolved rows appear in a queue on the manage screen with candidate matches. Show the
unresolved count on the AP page. Never silently drop a row that will not resolve.

THE TWO VIEWS WILL NOT RECONCILE TO ZERO — say so, don't hide it
YTD is a dated snapshot; Actuals is live. The difference is real movement plus the exclusions.
Under the AP heading, always:
  YTD view:     "Report (YTD) · snapshot <date> · <n> vendors · AED <total>.
                 <m> vendors excluded · AED <excluded>.  Actuals today: AED <live total>."
  Actuals view: "Actuals · live from Zoho · all <n> vendors · AED <total>. Nothing excluded."
Plus a "Snapshot vs today" panel breaking the gap into: excluded vendors, vendors in the
snapshot no longer in Zoho, vendors in Zoho not in the snapshot, and movement on common
vendors. A gap that is explained is fine; an unexplained gap must be shown as unexplained.

THE SERIES IS THE POINT — ingest the history, not just the latest tab
Ingest all 79 AP tabs. That gives a weekly AP trend from Aug 2024 to Sep 2026:
  - total AP over time, with the ageing mix as a stacked series
  - above-150 balance over time, the number that matters most
  - per-vendor history: click a vendor, see its balance week by week
Put an as-at date picker on the YTD view listing the available snapshot dates.

WARN ON THE SCOPE BREAKS. Vendor row counts step up sharply — around 103 rows to 190 on
01-06-2026, and 190 to 326 on 17-08-2026 — because reporting scope widened. Mark those two
dates on every trend chart and note that the series is not like-for-like across them. Without
this the chart reads as AP doubling.

MANAGE SCREEN at /reports/ap/snapshots
List snapshots with date, source tab, row count, total, ingested by and when.
Upload a new weekly tab (xlsx or csv), with a preview before commit.
Re-ingesting an existing date replaces that snapshot and records who did it.
Restricted to AP write permission.

Do not delete or modify any Zoho data. Snapshots are additive and independent of it.
```

## Verify

- [ ] 79 snapshots ingest; the series runs 2024-08-19 to 2026-09-14
- [ ] the 14-09-2026 snapshot holds **326 rows** totalling **AED 10,305,116.00**
- [ ] spot-check the series: 07-09-2026 = 8,768,793.56 · 31-08-2026 = 8,060,916.19 ·
      24-08-2026 = 11,874,888.06 · 29-06-2026 = 9,274,822.37 · 19-08-2024 = 4,935,611.53
      (full list in `ap_weekly_snapshot_totals.csv`)
- [ ] every ingested row's buckets sum to its total within 1.00
- [ ] the six leaking vendors are excluded from the YTD view; Heavenly Secrets in particular,
      which appears under two entities
- [ ] YTD total + excluded total = the raw snapshot total, exactly
- [ ] a vendor row drills through to live Zoho bills, panel labelled with today's date
- [ ] the two scope-break dates are marked on the trend chart
- [ ] re-ingesting the same tab twice leaves 79 snapshots, not 80
