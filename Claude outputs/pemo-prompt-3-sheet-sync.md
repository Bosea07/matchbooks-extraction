# PROMPT 3 — scheduled Google Sheet sync

Send only after Prompt 1 is verified. This adds a second way in; it does not replace the
import screen.

Context: the sheet is refreshed by an automated Pemo extraction at **06:00 Gulf Standard Time**
every day. This sync runs after it and must be able to tell the difference between "the sheet
is up to date" and "the extraction failed and the sheet is yesterday's".

```
Add a scheduled Google Sheets sync for Pemo transactions. Do not change the schema, the
category resolution, or the dashboard.

CRITICAL: reuse the existing import code path. The sync must call the same parse-and-load
function the /expense-analysis/pemo/import screen calls — same header mapping, same four
transform rules, same category resolution, same upsert key. Do not write a second loader. If
the shared logic is currently inside the import page component, extract it into a shared module
first and have both callers use it.

AUTH — Google service account, not a public link. This data carries cardholder names, merchant
names and amounts and must not be readable by URL.
  - Store the service account JSON as a Lovable Cloud secret named GOOGLE_SHEETS_SA_KEY.
  - Store the sheet id as PEMO_SHEET_ID and the tab name as PEMO_SHEET_TAB (default "Pemo").
  - Read the sheet with the Sheets API using the service account, scope
    https://www.googleapis.com/auth/spreadsheets.readonly
  - Never put the key, the sheet id, or any Google credential in client code or in the repo.

EDGE FUNCTION pemo-sheet-sync
  1. Read the whole tab.
  2. Validate headers against the same exact-header list the import screen uses. If any header
     is missing, abort the entire sync, write a failed run record naming the missing header,
     and load nothing. Never partially load.
  3. FRESHNESS CHECK, before loading. An upstream extraction feeds this sheet at 06:00 GST
     daily. When that extraction breaks, the sheet still reads fine — it is simply yesterday's —
     and a sync that reports success on it makes a stale dashboard look current. That is the
     failure this check exists to catch.
     Compute max(Transaction Date) in the sheet and the row count. Compare both against the
     last successful run. Mark the run 'stale' — still loading the rows, since they are valid —
     when EITHER:
       a. max(Transaction Date) has not advanced and more than 26 hours have passed since the
          last run whose max date was higher, or
       b. rows read is below 95% of the previous successful run (a truncated extraction).
     A 'stale' run must surface as a visible warning, not a silent success.
  4. Run the shared loader: sign rule, category resolution, upsert on
     (reference, txn_type, txn_date, billing_amount_raw), nothing filtered at load.
  5. Stamp every row the sync touched with last_seen_at = now().

ADD TWO COLUMNS
  pemo_transactions.last_seen_at timestamptz
  pemo_transactions.source       text default 'upload'   -- 'upload' | 'sheet_sync'

RUN HISTORY TABLE pemo_sync_runs
  id uuid pk, started_at, finished_at timestamptz,
  status text ('success' | 'stale' | 'failed'),
  rows_read int, rows_inserted int, rows_updated int, rows_unmapped int,
  sheet_max_txn_date date,
  error text
Write one row per run, whatever the outcome.

SCHEDULE
  pg_cron, daily at 07:00 Gulf Standard Time (03:00 UTC), one hour after the 06:00 extraction.
  Also expose a "Sync now" button on /expense-analysis/pemo/import, restricted to users with
  the expense-analysis write permission, which invokes the same function and shows the run
  result inline.

SURFACE THE STATE
  On /expense-analysis/pemo/import and in a compact strip at the top of the dashboard, show:
  last successful sync time, the latest transaction date present, rows read on that run, and
  the rows_unmapped count linking to the mapping page.
  If the most recent run is 'stale' or 'failed', show it as a banner with the reason. A stale
  dashboard that looks current is worse than an empty one.
  A "Sync history" panel on the import page listing the last 30 runs from pemo_sync_runs.

STALE ROWS
  Do not delete anything. On the import page, show a count of rows whose last_seen_at is older
  than the last successful run — rows that have disappeared from the sheet — and let a user
  review them. Deletion stays a human decision.

Keep the manual upload path working exactly as it does now. It is the fallback when the sheet
or the extraction is broken, and the way a one-off historical file gets loaded.
```

## Verify

- [ ] "Sync now" on a fresh database loads **4,232** rows, spend **3,416,500.84**
- [ ] running it a second time changes nothing — row count still 4,232
- [ ] the second run is marked **stale**, not success, because the max transaction date did not
      advance — this is the freshness check working, and it is the one behaviour worth testing
      deliberately
- [ ] renaming a column in the sheet makes the run fail with that column named, and loads nothing
- [ ] deleting rows from a copy of the sheet so it falls below 95% marks the run stale
- [ ] `pemo_sync_runs` has a row for every attempt, including the failures above
- [ ] the service account key does not appear in any client bundle or in the repo
- [ ] revoking the service account's access produces a failed run, not a silent empty load
