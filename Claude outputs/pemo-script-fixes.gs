/*************************************************************************************************
 * PEMO SCRIPT — CORRECTED FUNCTIONS
 * Replace each function below in your existing file. Nothing else changes.
 *
 * Fixes, in order of severity:
 *   1. writeRows_      never destroys history — merges by reference instead of clearing
 *   2. pullPemoTransactions  no longer overwrites the full tab with one month
 *   3. setSpreadsheetId  actually stores SPREADSHEET_ID, so triggers can run headless
 *   4. postToWebsite_  sends what the platform's endpoint expects, not Bearer + raw JSON
 *************************************************************************************************/


/* 1 ─────────────────────────────────────────────────────────────────────────
 * writeRows_ — MERGE, don't clear.
 *
 * The old version called clearContents() then wrote the current result. Any run
 * that returned fewer rows than the tab already held silently deleted the rest.
 * That is how the Pemo tab went from 4,232 rows (Aug-2024 onward) to 130 rows
 * covering three weeks.
 *
 * This version keys on Reference + Transaction Type and upserts: existing rows
 * are updated in place, new rows appended, nothing removed.
 */
function writeRows_(name, headers, rows) {
  const ss = ss_();
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }

  const refCol  = headers.indexOf('Reference');
  const typeCol = headers.indexOf('Transaction Type');
  const keyOf = r => String(r[refCol] || '') + '|' + String(r[typeCol] || '');

  const last = sh.getLastRow();
  const existing = last > 1 ? sh.getRange(2, 1, last - 1, headers.length).getValues() : [];
  const index = {};
  existing.forEach((r, i) => { index[keyOf(r)] = i; });

  const appends = [];
  let updated = 0;
  rows.forEach(r => {
    const k = keyOf(r);
    if (index[k] !== undefined) { existing[index[k]] = r; updated++; }
    else { appends.push(r); }
  });

  if (existing.length) sh.getRange(2, 1, existing.length, headers.length).setValues(existing);
  if (appends.length)  sh.getRange(existing.length + 2, 1, appends.length, headers.length).setValues(appends);

  Logger.log(name + ': ' + updated + ' updated, ' + appends.length + ' added, ' +
             (existing.length + appends.length) + ' total');
}


/* 2 ─────────────────────────────────────────────────────────────────────────
 * pullPemoTransactions — a partial pull must never look like a full one.
 * With writeRows_ merging, this is now safe, but the log line matters: it should
 * be obvious that this run covered one month, not everything.
 */
function pullPemoTransactions() {
  const now = new Date();
  const from = new Date(now.getFullYear(), now.getMonth(), 1);
  const to = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  const res = syncAllEntities_(from, to);
  toast_('This-month pull (merged into existing history) — ' +
         res.map(r => r.entity + ': ' + r.txns).join('  |  '));
}


/* 3 ─────────────────────────────────────────────────────────────────────────
 * setSpreadsheetId — the old version stored a property NAMED the sheet id, with
 * the value 'PASTE_YOUR_SHEET_ID_HERE', while ss_() reads SPREADSHEET_ID. So it
 * never worked, and every trigger run depended on the sheet being open.
 */
function setSpreadsheetId() {
  const ID = '1bllOYmLhIx-vwOQWlzDjNZCUVHQZanB0DNgTL6KOXLMngF0rfp5dyl_K';
  if (!ID || ID.indexOf('PASTE_') === 0) {
    throw new Error('Edit setSpreadsheetId(): paste the Sheet ID from its URL.');
  }
  PropertiesService.getScriptProperties().setProperty('SPREADSHEET_ID', ID.trim());
  // Prove it works rather than assuming.
  const name = SpreadsheetApp.openById(ID.trim()).getName();
  Logger.log('SPREADSHEET_ID stored. Opened "' + name + '" — headless runs will now work.');
}


/* 4 ─────────────────────────────────────────────────────────────────────────
 * postToWebsite_ — the platform READS the sheet; it does not want raw Pemo JSON.
 *
 * Per the architecture: Pemo has its own route /api/public/pemo/sync, and
 * pemo-sync.server.ts reads the Google Sheet. The old code posted Pemo objects
 * to /api/public/sync/ingest with Authorization: Bearer — wrong endpoint, wrong
 * header (that route wants X-Sync-Key) and wrong body shape (it wants
 * { table, entity, mode, rows }).
 *
 * So this now just TELLS the platform to re-read the sheet, after the sheet has
 * been written. Set PEMO_SYNC_URL to:
 *     https://www.matchbooksai.com/api/public/pemo/sync
 * and PEMO_SYNC_KEY to whatever that route checks.
 *
 * CONFIRM THE CONTRACT FIRST: open src/routes/api/public/pemo/sync.ts and check
 * which header it reads and whether it expects a body. Adjust the two marked
 * lines if it differs. Do not guess — run debugPemoSyncProbe() below.
 */
function postToWebsite_(txns, rows, entity) {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('PEMO_SYNC_URL');
  if (!url) { Logger.log('PEMO_SYNC_URL not set — sheet written, platform not notified.'); return null; }
  const key = props.getProperty('PEMO_SYNC_KEY') || props.getProperty('SYNC_INGEST_KEY') || '';

  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'X-Sync-Key': key },                    // <- confirm against sync.ts
    payload: JSON.stringify({ entity: entity || '', trigger: 'apps_script' }),  // <- confirm
    muteHttpExceptions: true,
    followRedirects: false,
  });

  const code = res.getResponseCode();
  if (code >= 300) {
    throw new Error('Pemo sync trigger failed (' + code + ') for ' + entity + ': ' +
                    res.getContentText().slice(0, 300));
  }
  Logger.log('Platform notified for ' + entity + ' (' + code + '): ' +
             res.getContentText().slice(0, 200));
  return rows.length;
}


/* ───────────────────────────────────────────────────────────────────────────
 * Run this once to find the real contract before trusting postToWebsite_.
 * It tries each plausible header against the Pemo route and prints the status.
 * Whichever line is not 401/403 is the one the endpoint wants.
 */
function debugPemoSyncProbe() {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('PEMO_SYNC_URL') ||
              'https://www.matchbooksai.com/api/public/pemo/sync';
  const key = props.getProperty('PEMO_SYNC_KEY') || props.getProperty('SYNC_INGEST_KEY') || '';
  const body = JSON.stringify({ entity: 'probe', trigger: 'probe' });
  const variants = [
    ['X-Sync-Key',            { 'X-Sync-Key': key }],
    ['x-sync-key',            { 'x-sync-key': key }],
    ['Authorization Bearer',  { Authorization: 'Bearer ' + key }],
    ['x-api-key',             { 'x-api-key': key }],
    ['no header',             {}],
  ];
  Logger.log('URL: ' + url);
  variants.forEach(v => {
    const res = UrlFetchApp.fetch(url, { method: 'post', contentType: 'application/json',
      headers: v[1], payload: body, muteHttpExceptions: true, followRedirects: false });
    Logger.log(res.getResponseCode() + '  ' + v[0] + '  -> ' +
               res.getContentText().slice(0, 160));
  });
}


/* ───────────────────────────────────────────────────────────────────────────
 * OPTIONAL — stop re-pulling all history on every run.
 *
 * fetchAllPemo_ currently pages through everything and filters in memory, so a
 * daily run costs the same as a full backfill and gets slower forever. If the
 * Pemo API accepts date parameters, pass them. Confirm the parameter names from
 * debugDumpOnePemoTransaction() / the Pemo docs before enabling this.
 *
 * function fetchAllPemo_(key, fromISO, toISO) {
 *   ...
 *   const url = PEMO.BASE_URL + PEMO.TX_PATH +
 *     '?exportStatus=' + encodeURIComponent(PEMO.EXPORT_STATUS) +
 *     '&' + PEMO.PARAM_LIMIT + '=' + PEMO.PAGE_SIZE +
 *     '&' + PEMO.PARAM_PAGE + '=' + page +
 *     (fromISO ? '&from=' + fromISO : '') +          // <- confirm parameter name
 *     (toISO   ? '&to='   + toISO   : '');           // <- confirm parameter name
 *   ...
 * }
 *
 * Until then, leave it as is. A slow correct pull beats a fast wrong one.
 */


/* ───────────────────────────────────────────────────────────────────────────
 * RECOVERING THE LOST HISTORY
 *
 * The tab currently holds roughly 130 rows; it held 4,232 on 9 September. With
 * writeRows_ merging instead of clearing, run pullAllHistory() once and the full
 * set is rebuilt from the Pemo API — provided the API still returns it. Check
 * the row count afterwards before assuming it worked.
 *
 * If the API no longer returns the older transactions, the Google Sheet version
 * history (File -> Version history) will still have the 4,232-row state from
 * before the overwrite. Restore from there.
 */
