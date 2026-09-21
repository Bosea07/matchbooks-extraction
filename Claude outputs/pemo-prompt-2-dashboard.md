# PROMPT 2 — the Pemo dashboard

Send this now. Prompt 1 is verified and the loader is in place, so the dashboard has correct
data to read. Prompt 3 (the scheduled sheet sync) can follow in either order — it touches
ingestion only.

---

```
Build the Pemo dashboard at /expense-analysis/pemo, as a leaf under Expense Analysis in the
sidebar, reading pemo_transactions. Do not change the schema or the import.

THE SPEND DEFINITION — every figure below uses it unless a section says otherwise
  spend rows = txn_type 'purchase' AND status 'completed'
Declined, reversed and pending rows are excluded from every spend and category figure. They
appear only in the Declines section. International Fee rows are excluded from spend entirely —
FX cost comes from the fee_total column on the purchase rows, never from the fee rows, or it
doubles.

GLOBAL FILTERS, in a bar at the top, all encoded in the URL query string so a filtered view is
shareable and survives a refresh:
  month range (default: last 12 complete months) · category · cardholder · merchant_country ·
  actual_currency
The current part-month is excluded from every trend series and every MoM comparison by default,
with a toggle to include it. Without this the newest month reads as a collapse in spend.

METRIC DEFINITIONS — use these exactly
  spend            = SUM(amount_aed)
  transactions     = COUNT(*)
  average ticket   = spend / transactions
  median ticket    = p50 of amount_aed
  MoM              = (this month - prior complete month) / prior complete month
  decline rate     = declined count / (declined + completed) count, purchases only
  FX fee rate      = SUM(fee_total) / spend where actual_currency <> 'AED'
  receipt rate     = COUNT(receipt_url is not null) / transactions

Show MEDIAN next to AVERAGE on every card that shows an average. The median ticket is AED 123
against an average of AED 1,382 — a long tail of small purchases with a few very large ones. An
average alone makes a cardholder with one big invoice look like a heavy spender.

EIGHT SECTIONS

1 OVERVIEW
  Cards: total spend, transactions, average ticket, median ticket, MoM change, decline rate,
  FX fee rate, receipt compliance %.
  Monthly spend column chart with a 3-month trailing average line.
  Stacked bar of spend by category over the same months.

2 BY CATEGORY
  Horizontal ranked bars, spend descending, with share-of-total labels.
  A month x category grid, values and a colour scale.
  A category x cardholder heat map.
  A "Needs mapping" panel: rows where category_source = 'unmapped', grouped by
  (merchant_name, mcc), newest first, each linking to /expense-analysis/pemo/mapping.

3 BY CARDHOLDER
  Table, one row per cardholder: spend, transactions, average, median, decline rate,
  missing receipts (count and value), top 3 categories. Sortable on every column.
  Decline rate ranges from 13% to 54% across the 15 cardholders — colour that column so the
  outliers are visible without sorting.

4 BY MERCHANT
  Top 20 by spend as bars. A concentration line: "top 20 merchants = X% of spend".
  A tail count of merchants used exactly once. A list of merchants first seen in the
  selected period.

5 CROSS-BORDER AND FX
  Cross-border = actual_currency <> 'AED'.
  Spend split AED vs cross-border, by count and by value — these differ sharply (20% of
  transactions, 49% of value), so show both.
  Spend by merchant_country and by actual_currency.
  Total fees paid and the effective fee rate, broken down by currency and by month.

6 DECLINES
  Purchases with status 'declined'. Decline rate over time, by cardholder, by merchant.
  Total value attempted. A table of the largest declined attempts.
  Label the section as operational — this is a workflow signal, not spend.

7 CLOSE READINESS
  Four cards, each showing a COUNT and the VALUE behind it, each drilling through to the
  filtered transaction list:
    missing receipt_url · gl_account is null · vat_code is null · export_status <> 'exported'

8 WALLET
  txn_type in (topUp, refund, cashback, walletTransfer).
  Top-ups against spend by month, a running balance line, and totals for refunds and cashback.
  Keep this visually separate from the spend sections — these are funding movements, not expense.

TRANSACTION LIST
  A drill-through table below the sections, respecting all active filters, with columns:
  date, merchant, category, cardholder, amount_aed, actual_amount + currency, status,
  receipt (icon linking to receipt_url), export_status. Server-side paginated. CSV export of
  the current filtered view.

Match the existing Expense Analysis pages for layout, card style, chart colours and typography.
Use TanStack Query with the filter state as the query key. Every chart and table must show a
proper empty state when a filter combination returns nothing.
```

**Verify Prompt 2:**

- [ ] no filters, all months: total spend **AED 3,416,501** over **2,473** transactions
- [ ] average **1,382**, median **123**
- [ ] category bars match: Procurement 1,205,205 · Marketplace 835,095 · Marketing 778,715
- [ ] decline rate **18.4%**, declined value **1,598,563**
- [ ] cross-border **AED 1,667,823**, fees **64,107**, rate **3.84%**
- [ ] missing receipts **422** worth **AED 555,885**
- [ ] wallet top-ups **AED 2,573,000** across **75**
- [ ] changing a filter changes the URL; pasting that URL into a new tab reproduces the view
- [ ] the current part-month is absent from the trend chart until the toggle is switched on
