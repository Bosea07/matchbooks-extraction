# PROMPT 14 — pull Zoho directly into the platform, mirror to Sheets

Inverts the current pipeline. Today: Zoho → Apps Script → Google Sheet → push → platform.
After this: **Zoho → platform → Google Sheet**, with the database as the source of truth and
the sheet as a mirror finance can still work in.

Why not have the backend write to Sheets itself: that needs a Google service account key, and
key creation may be blocked by your org policy (`iam.disableServiceAccountKeyCreation`). A thin
Apps Script that reads from the platform's API avoids the problem entirely — it already has
access to the sheet it lives in.

```
Move the Zoho integration from Apps Script into the platform. The database becomes the source
of truth; the Google Sheet becomes a mirror. Do not remove the existing ingest endpoint yet —
run both until the new path is verified.

SECRETS — Lovable Cloud
  ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN
  ZOHO_ACCOUNTS_BASE   https://accounts.zoho.com
  ZOHO_API_BASE        https://www.zohoapis.com/books/v3
  SHEET_MIRROR_KEY     a shared secret the Apps Script sends to read data back

TABLE zoho_entities
  code text pk,                  -- KUWA, SAHA, DMCC, KSA, SHIFA, VWHC
  organization_id text not null,
  base_currency text not null,   -- AED | SAR
  rate_to_aed numeric,           -- 1 for AED, 0.97933 for SAR (the two USD pegs)
  active boolean default true
Seed: KUWA 814624081 AED · SAHA 906420266 SAR · DMCC 751813311 AED · KSA 841313343 SAR ·
      SHIFA 851585320 AED · VWHC 899870447 AED

TABLE sync_jobs — the part that makes this work at all
  id uuid pk
  job_type text not null,        -- gl_monthly | gl_lines | bill_lines | vendors | ap_ageing
  entity text not null
  status text not null,          -- queued | running | paused | done | failed
  cursor jsonb,                  -- {docType, page, lastModified} — where to resume
  rows_written int default 0, docs_fetched int default 0, pending_estimate int,
  started_at, finished_at timestamptz, last_error text,
  created_at timestamptz default now()

EDGE FUNCTIONS — one per job type, all built the same way
  zoho-sync-gl-monthly · zoho-sync-gl-lines · zoho-sync-bill-lines ·
  zoho-sync-vendors · zoho-sync-ap-ageing

Every one of them:
  - refreshes the Zoho access token server-side, caching it for its lifetime
  - claims a queued or paused sync_jobs row for its type, sets status 'running'
  - works until a TIME BUDGET well inside the edge function wall-clock limit, then writes its
    cursor back, sets status 'paused', and returns. It does NOT try to finish in one call.
  - on completion sets 'done'; on error sets 'failed' with last_error and does not retry blindly
  - is idempotent: re-running a job from its cursor must never duplicate rows. Upsert on the
    natural key of each table.

WHAT EACH ONE PULLS
  gl_monthly    reports/generalledger, one call per entity per month from 2024-01-01.
                Store entity, month, account, account_type, debit, credit, net.
  gl_lines      journals (detail per journal, cached by last_modified_time) + banktransactions.
  bill_lines    bills, expenses, vendorcredits. The list endpoints return no line items, so
                each document needs a detail call — this is the slow one and the reason the
                job queue exists. Store vendor_name, vendor_id, account_name, account_id,
                amount, currency, exchange_rate, and the AED equivalent.
  vendors       the vendor master per entity: id, display name, company name, currency, status.
  ap_ageing     the AP ageing summary per entity.

PAGINATION — no silent truncation
Page until page_context.has_more_page is false. If a hard page ceiling is ever reached, FAIL
the job with an explicit error. Returning a short list quietly is how the existing GL Lines
tabs ended up capped at 12,000 rows.

CURRENCY
Store three values on every amount: source, entity base, and AED (base x rate_to_aed).
SAR/AED is 0.97933 and looks like an ordinary rate — nothing downstream will flag SAR added to
AED untranslated, so it must be applied here.

SCHEDULING — pg_cron
  hourly    tick each job type; a paused job resumes, a done job that is older than its
            refresh interval is re-queued
  daily 03:00 Gulf   queue gl_lines, bill_lines, vendors, ap_ageing
  monthly on the 1st  queue gl_monthly
A tick that finds nothing to do exits immediately.

SYNC STATUS PAGE at /admin/sync
Per entity and job type: last successful run, rows written, pending estimate, current status,
and last error. A "Run now" button per job. A failed or long-paused job is shown in red at the
top, not buried in a list. Owner-only.
This page is what replaces reading the Apps Script execution log.

READ ENDPOINT FOR THE SHEET MIRROR
GET /api/public/sheet-mirror?table=<name>&entity=<code>&since=<iso>
  Header: X-Mirror-Key: <SHEET_MIRROR_KEY>
  Returns JSON { columns: [...], rows: [[...]] }, paginated with a cursor.
  Tables exposed: gl_monthly, gl_lines, bill_lines, vendors, ap_ageing.
  Reject any request without a matching key. Read-only: this endpoint can never write.

KEEP THE OLD PATH ALIVE
Do not delete the existing /ingest endpoint or change its contract. Run the Apps Script push
and the new pull in parallel until the row counts agree per entity and job type, then retire
the push. Show both counts on the sync status page while they coexist.
```

## Verify

- [ ] a bill_lines job for KUWA runs, pauses at its time budget, and resumes on the next tick
      with no duplicate rows
- [ ] KUWA bill lines reconcile to the existing `KUWA GL` tab for a given account and month
- [ ] a SAR entity's AED amount is about 2% below its base amount; equal means the peg was skipped
- [ ] pagination past a page ceiling fails loudly rather than returning a short list
- [ ] /admin/sync shows a failed job in red with its error
- [ ] the mirror endpoint refuses a request with no key, and returns rows with one
- [ ] both pipelines report matching row counts before the Apps Script push is retired

---

## The replacement Apps Script

Once the above is live, the whole GL sync script is replaced by this. Keep the existing file
until the counts agree, then swap it.

```javascript
/**
 * MatchBooks — mirror platform data into this sheet.
 * The platform pulls from Zoho; this only reads and writes tabs.
 * Script Properties: MIRROR_URL, MIRROR_KEY
 */
const MIRROR_TABLES = ['gl_monthly', 'gl_lines', 'bill_lines', 'vendors', 'ap_ageing'];
const MIRROR_ENTITIES = ['KUWA', 'SAHA', 'DMCC', 'KSA', 'SHIFA', 'VWHC'];

function onOpen(e) {
  try {
    SpreadsheetApp.getUi().createMenu('MatchBooks')
      .addItem('Refresh all tabs', 'mirrorAll').addToUi();
  } catch (err) { console.log('no UI context'); }
}

function installMirrorTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'mirrorAll') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('mirrorAll').timeBased()
    .everyDays(1).atHour(6).inTimezone('Asia/Dubai').create();
}

function mirrorAll() {
  const P = PropertiesService.getScriptProperties();
  const base = P.getProperty('MIRROR_URL'), key = P.getProperty('MIRROR_KEY');
  if (!base || !key) throw new Error('MIRROR_URL / MIRROR_KEY not set');

  MIRROR_TABLES.forEach(table => {
    MIRROR_ENTITIES.forEach(ent => {
      let cursor = '', cols = null, rows = [];
      do {
        const url = base + '?table=' + table + '&entity=' + ent +
                    (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
        const r = UrlFetchApp.fetch(url, {
          headers: { 'X-Mirror-Key': key }, muteHttpExceptions: true });
        if (r.getResponseCode() !== 200) {
          log_(table + ' ' + ent, 'FAILED ' + r.getResponseCode() + ': ' +
               r.getContentText().slice(0, 150));
          return;
        }
        const body = JSON.parse(r.getContentText());
        cols = cols || body.columns;
        (body.rows || []).forEach(x => rows.push(x));
        cursor = body.next_cursor || '';
      } while (cursor);

      if (cols) writeTab_(ent + ' ' + table, cols, rows);
      log_(table + ' ' + ent, rows.length + ' rows mirrored');
    });
  });
}

function writeTab_(tabName, header, rows) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(tabName) || ss.insertSheet(tabName);
  sh.clear();
  sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
  sh.setFrozenRows(1);
  if (rows.length) sh.getRange(2, 1, rows.length, header.length).setValues(rows);
}

function log_(what, detail) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName('Mirror Log') || ss.insertSheet('Mirror Log');
  sh.appendRow([new Date(), what, detail]);
}
```

No Zoho credentials, no pagination logic, no caching, no OAuth. Roughly 60 lines against the
600 it replaces — because the hard parts moved to where they can be monitored and where a
failure is visible in the app rather than in an execution log nobody opens.
