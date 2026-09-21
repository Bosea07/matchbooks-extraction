# Pemo expenses — Lovable prompts

Two single-purpose prompts. Send **Prompt 1**, verify it against its checklist, then send **Prompt 2**.
Do not merge them — the dashboard is only correct if the load rules in Prompt 1 landed first.

---

## PROMPT 1 — schema, seed data and import

```
Add Pemo card-expense ingestion. Backend and an import screen only — no dashboard yet.

CREATE THREE TABLES

pemo_mcc_map
  mcc          int primary key
  mcc_label    text not null
  category     text not null

pemo_merchant_rules
  id             uuid pk default gen_random_uuid()
  priority       int not null
  merchant_regex text not null
  category       text not null
  note           text

pemo_transactions
  id                 uuid pk default gen_random_uuid()
  txn_date           date not null
  reference          text
  txn_type           text not null
  status             text not null
  merchant_name      text
  merchant_city      text
  merchant_country   text
  mcc                int
  cardholder         text
  cardholder_email   text
  billing_amount_raw numeric not null
  amount_aed         numeric not null
  direction          text not null
  actual_amount      numeric
  actual_currency    text
  wallet_debited     numeric
  fee_total          numeric
  gl_account         text
  vendor             text
  vat_code           text
  export_status      text
  receipt_url        text
  expense_id         text
  category           text not null
  category_source    text not null
  created_at         timestamptz default now()
  UNIQUE (reference, txn_type, txn_date, billing_amount_raw)

RLS: same pattern as the existing expense tables — authenticated users of the org can read;
insert/update restricted to users with the expense-analysis write permission.

SEED pemo_merchant_rules (priority, regex, category) — case-insensitive match on merchant_name:
  1 | ADVERTISING|TIKTOK ADS|GOOGLE \*ADS|META |FACEBK|INSTAGRAM|SNAP\b|LINKEDIN ADS | Marketing & Advertising
  2 | DHL|ARAMEX|FEDEX|SHIPP | Logistics & Shipping
  3 | INSURANCE|ORIENT INS | Insurance
  4 | LINKEDIN JOB | Recruitment
  5 | SHOPIFY|PADDLE|LINKTREE|BEAUTIFUL\.AI|SCREAMING FROG|BLOOMBERG|EQUITYLIST|VASTA|OCTA|MAMO|COURSIV | Software & Subscriptions
  6 | FEDERAL TAX|SMARTDXB|DED\b|QATAR FINANCIAL|AAFAQ|GS1 | Government & Compliance

SEED pemo_mcc_map (mcc | label | category):
3175|Airline / hotel booking|Travel & Accommodation
4111|Local transport|Transport & Fuel
4121|Taxi & ride-hailing|Transport & Fuel
4214|Freight & courier|Transport & Fuel
4215|Courier services|Logistics & Shipping
4722|Travel agencies|Travel & Accommodation
4814|Telecom|Utilities & Telecom
4816|Information services|Marketplace Purchases
4899|Cable & streaming|Software & Subscriptions
4900|Utilities|Utilities & Telecom
5047|Medical & lab equipment|Procurement
5065|Electronic parts|Office & Facilities
5192|Books & publications|Software & Subscriptions
5199|Nondurable goods|Marketplace Purchases
5211|Building materials|Office & Facilities
5262|Online marketplaces|Marketplace Purchases
5311|Department stores|Marketplace Purchases
5399|General merchandise|Marketplace Purchases
5411|Grocery & supermarkets|Marketplace Purchases
5499|Convenience & specialty food|Procurement
5511|Automotive|Transport & Fuel
5541|Service stations (fuel)|Transport & Fuel
5651|Apparel|Marketplace Purchases
5691|Clothing stores|Marketplace Purchases
5712|Furniture|Office & Facilities
5713|Floor coverings|Office & Facilities
5732|Electronics stores|Office & Facilities
5734|Computer software|Software & Subscriptions
5811|Caterers|Meals & Entertainment
5812|Restaurants|Meals & Entertainment
5814|Fast food|Meals & Entertainment
5815|Digital media|Software & Subscriptions
5816|Digital goods — games|Marketing & Advertising
5817|Digital goods — apps|Marketing & Advertising
5818|Digital goods — other|Marketing & Advertising
5912|Pharmacies|Health & Pharmacy
5942|Book stores|Marketplace Purchases
5943|Stationery & office supplies|Office & Facilities
5968|Subscription services|Marketing & Advertising
5977|Cosmetics & personal care|Procurement
5998|General stores|Marketplace Purchases
5999|Specialty retail|Procurement
6300|Insurance|Insurance
6513|Real estate & rentals|Travel & Accommodation
7011|Hotels & lodging|Travel & Accommodation
7221|Photographic & print services|Office & Facilities
7278|Buying & shopping services|Marketplace Purchases
7311|Advertising services|Marketing & Advertising
7372|Computer programming services|Software & Subscriptions
7392|Consulting & PR|Professional Services
7399|Business services|Government & Compliance
7512|Car rental|Transport & Fuel
7523|Parking|Transport & Fuel
8099|Health services|Health & Pharmacy
8299|Educational services|Professional Services
8699|Membership organisations|Professional Services
8931|Accounting & audit services|Professional Services
9311|Tax payments|Government & Compliance
9399|Government services|Government & Compliance

IMPORT SCREEN at /expense-analysis/pemo/import
Accepts a .xlsx or .csv Pemo export. Read the sheet named "Pemo" if present, else the first sheet.
Map by exact header text:
  Transaction Date -> txn_date        Reference -> reference
  Transaction Type -> txn_type        Merchant Name/City/Country -> merchant_*
  MCC -> mcc                          Cardholder -> cardholder
  Cardholder Email -> cardholder_email
  Billing Amount -> billing_amount_raw
  Actual Amount -> actual_amount      Actual Currency -> actual_currency
  Wallet Debited -> wallet_debited    Fee Total -> fee_total
  GL Account -> gl_account            Vendor -> vendor
  VAT Code -> vat_code                Status -> status
  Export Status -> export_status      Receipt URL -> receipt_url
  Expense ID / Receipts Name -> expense_id
If any of these headers is missing, abort the whole import and name the missing header.
Never partially load.

FOUR TRANSFORM RULES — apply at load, they are not optional

1. SIGN. Every amount in the export is negative, including money coming in.
   amount_aed = abs(billing_amount_raw). Keep billing_amount_raw exactly as exported so the
   correction stays auditable.
   direction = 'inflow'   when txn_type in (refund, topUp, cashback)
             = 'transfer' when txn_type = 'walletTransfer'
             = 'outflow'  otherwise

2. CATEGORY, resolved in this order:
   a. first pemo_merchant_rules row, by ascending priority, whose regex matches merchant_name
      case-insensitively  -> category_source = 'merchant_rule'
   b. else pemo_mcc_map[mcc]                                 -> category_source = 'mcc'
   c. else category = 'Uncategorised'                        -> category_source = 'unmapped'
   Resolve on insert and store it. Do not compute it at read time.

3. DEDUPLICATION. reference is NOT unique — 3,279 distinct values across 4,232 rows, because
   every International Fee row reuses its parent purchase's reference. Upsert on
   (reference, txn_type, txn_date, billing_amount_raw), updating the row on conflict.
   Re-uploading the same export must not create duplicates.

4. NOTHING IS FILTERED AT LOAD. Load declined, reversed and pending rows and the
   International Fee rows too. Filtering is the dashboard's job. Loading is lossless.

After the import, show a receipt: rows read, rows inserted, rows updated, and a breakdown by
txn_type and status. Also show a count of rows with category_source = 'unmapped', linked to a
list of the distinct (merchant_name, mcc) pairs behind them.

MAPPING ADMIN at /expense-analysis/pemo/mapping
Two editable tables — pemo_mcc_map and pemo_merchant_rules — with add, edit and delete.
A "Re-resolve categories" button that re-runs rule 2 over every existing row and reports how many
changed. Restrict this screen to users with the expense-analysis write permission.

DO NOT build any charts or dashboard in this prompt.
```

**Verify Prompt 1 before moving on.** Import the Pemo export, then check:

- [ ] `pemo_transactions` holds **4,232** rows
- [ ] by type: purchase 3,107 · International Fee 953 · refund 84 · topUp 75 · cashback 12 · walletTransfer 1
- [ ] by status: completed 3,421 · declined 695 · reversed 100 · pending 16
- [ ] `SUM(amount_aed) WHERE txn_type='purchase' AND status='completed'` = **3,416,501** (±1)
- [ ] every `amount_aed` is positive; `billing_amount_raw` still negative
- [ ] rows with `category_source='unmapped'` = **0**
- [ ] top category is Procurement at **1,205,205**
- [ ] importing the same file a second time leaves the row count at 4,232

---

## PROMPT 2 — the dashboard

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
- [ ] category bars match: COGS 1,205,205 · Marketplace 835,095 · Marketing 778,715
- [ ] decline rate **18.4%**, declined value **1,598,563**
- [ ] cross-border **AED 1,667,823**, fees **64,107**, rate **3.84%**
- [ ] missing receipts **422** worth **AED 555,885**
- [ ] wallet top-ups **AED 2,573,000** across **75**
- [ ] changing a filter changes the URL; pasting that URL into a new tab reproduces the view
- [ ] the current part-month is absent from the trend chart until the toggle is switched on
