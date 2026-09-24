/*************************************************************************************************
 * PEMO → GOOGLE SHEETS  +  MONTH-END RECONCILIATION  (read-only against Zoho)
 * -----------------------------------------------------------------------------------------------
 * KUWA FOOD SUPPLEMENTS TRADING LLC
 *
 * COMPLETE FILE — replace the entire contents of Code.gs with this.
 *
 * Four functions are corrected against the previous version; everything else is unchanged:
 *   writeRows_              merges instead of clearing — a partial pull can no longer wipe history
 *   pullPemoTransactions    safe now that writeRows_ merges; log says it was a partial pull
 *   setSpreadsheetId        stores SPREADSHEET_ID (it stored a property named the sheet id before)
 *   postToWebsite_          targets the Pemo route with the header that route expects
 * Plus one new helper: debugPemoSyncProbe() — run it before trusting postToWebsite_.
 *
 * It NEVER writes anything to Zoho. Zoho data is read-only (from the "Zoho" tab).
 *
 * SECURITY: API keys live in Script Properties, never in this file.
 *************************************************************************************************/

/*==============================  CONFIG  ======================================================*/

const CONFIG = {
  PEMO_SHEET: 'Pemo',
  ZOHO_SHEET: 'Zoho',
  CONTROL_SHEET: 'Control',
  SUMMARY_SHEET: 'Recon Summary',
  EMAIL_TO: '',                  // e.g. 'ashutosh.bose@feelvaleo.com' — blank = no email
  ACK_MARK_EXPORTED: false,      // true tells Pemo to mark pulled txns "Exported" — changes state
  WEBHOOK_URL: '',
  WEBHOOK_METHOD: 'post',
  WEBHOOK_BATCH: 200,
  WEBHOOK_SEND: 'raw',
};

const ENTITIES = [
  { name: 'Kuwa',  keyProp: 'PEMO_API_KEY_Kuwa',  sheet: 'Pemo Kuwa' },
  { name: 'Shifa', keyProp: 'Pemo_API_Key_Shifa', sheet: 'Pemo Shifa' },
  // { name: 'ThirdEntity', keyProp: 'PEMO_API_KEY_Third', sheet: 'Pemo ThirdEntity' },
];

const PEMO = {
  BASE_URL: 'https://external-api.pemo.io',
  TX_PATH:  '/v1/transactions',
  AUTH_HEADER: 'apiKey',
  EXPORT_STATUS: 'all',
  PAGE_SIZE: 50,
  MAX_PAGES: 4000,
  PARAM_PAGE: 'page',
  PARAM_LIMIT: 'limit',
  AMOUNT_DIVISOR: 100,
};

const RESPONSE = { ARRAY_KEYS: ['transactions', 'data', 'items', 'results', 'records'] };

const PEMO_SIGN = -1;
const FIELD_MAP = [
  { header: 'Entity',                       fn: (tx) => tx.__entity || '' },
  { header: 'Transaction Date',            fn: txnDate_ },
  { header: 'Reference',                    paths: ['reference'] },
  { header: 'Transaction Type',            fn: txnType_ },
  { header: 'Merchant Name',               paths: ['merchant', 'merchantName'] },
  { header: 'Merchant City',               paths: ['merchantCity'] },
  { header: 'Merchant Country',            paths: ['merchantCountry'] },
  { header: 'MCC',                          paths: ['mcc'] },
  { header: 'Cardholder',                  paths: ['createdFor'] },
  { header: 'Cardholder Email',            paths: ['spender.email'] },
  { header: 'Billing Amount',              paths: ['billingAmount'], scale: true, signed: true },
  { header: 'Billing Currency',            paths: ['billingCurrency'] },
  { header: 'Actual Amount',               paths: ['actualAmount'], scale: true, signed: true },
  { header: 'Actual Currency',             paths: ['actualCurrency', 'transactionCurrency'] },
  { header: 'Wallet Debited',              paths: ['walletDebitedAmount'], scale: true, signed: true },
  { header: 'Fee Total',                   fn: feeTotal_ },
  { header: 'GL Account',                  fn: (tx) => acct_(tx, 'chartOfAccounts') },
  { header: 'Vendor',                      fn: (tx) => acct_(tx, 'vendor') },
  { header: 'VAT Code',                    fn: (tx) => acct_(tx, 'vatCode') },
  { header: 'Status',                       paths: ['status'] },
  { header: 'Export Status',               paths: ['exportStatus'] },
  { header: 'Receipt URL',                 fn: (tx) => (tx.receipts && tx.receipts[0]) || '' },
  { header: 'Expense ID / Receipts Name',  paths: ['reference'] },
];

/*==============================  MENU  ========================================================*/

function onOpen() {
  try {
    SpreadsheetApp.getUi()
      .createMenu('Pemo Tools')
      .addItem('Pull ALL history (one-time)', 'pullAllHistory')
      .addItem('Sync now (daily update)', 'dailyUpdate')
      .addSeparator()
      .addItem('Pull this month only', 'pullPemoTransactions')
      .addItem('Run reconciliation', 'runReconciliation')
      .addItem('Pull + reconcile (month-end run)', 'monthEndRun')
      .addSeparator()
      .addItem('Install DAILY sync trigger', 'installDailyTrigger')
      .addItem('Install month-end trigger', 'installMonthEndTrigger')
      .addSeparator()
      .addItem('Debug: dump one Pemo transaction to Logs', 'debugDumpOnePemoTransaction')
      .addItem('Debug: probe the platform sync route', 'debugPemoSyncProbe')
      .addItem('Debug: test website POST', 'debugTestWebhook')
      .addToUi();
  } catch (e) {
    Logger.log('onOpen skipped (no UI context): ' + e.message);
  }
}

/*==============================  SECRETS  =====================================================*/

function getEntityKey_(ent) {
  const k = PropertiesService.getScriptProperties().getProperty(ent.keyProp);
  if (!k) throw new Error('No API key in Script Property "' + ent.keyProp + '" for entity ' + ent.name + '.');
  return k;
}

/**
 * FIXED. The old version stored a property NAMED the sheet id, with the value
 * 'PASTE_YOUR_SHEET_ID_HERE', while ss_() reads SPREADSHEET_ID. So headless runs
 * never worked — the daily trigger only succeeded when the sheet happened to be open.
 * Delete the malformed property (the one named 1bllOY...) from Script Properties,
 * then run this once.
 */
function setSpreadsheetId() {
  const ID = '1bllOYmLhIx-vwOQWlzDjNZCUVHQZanB0DNgTL6KOXLMngF0rfp5dyl_K';
  if (!ID || ID.indexOf('PASTE_') === 0) {
    throw new Error('Edit setSpreadsheetId(): paste the Sheet ID from its URL.');
  }
  PropertiesService.getScriptProperties().setProperty('SPREADSHEET_ID', ID.trim());
  const name = SpreadsheetApp.openById(ID.trim()).getName();   // prove it, don't assume
  Logger.log('SPREADSHEET_ID stored. Opened "' + name + '" — headless runs will now work.');
}

function setWebhookToken() {
  const T = 'PASTE_WEBHOOK_TOKEN_HERE';
  if (T.indexOf('PASTE_') === 0) throw new Error('Edit setWebhookToken(): paste your token (or skip if none).');
  PropertiesService.getScriptProperties().setProperty('WEBHOOK_TOKEN', T.trim());
  Logger.log('Webhook token stored. Now delete it from setWebhookToken() and save.');
}

function ss_() {
  const id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  if (id) return SpreadsheetApp.openById(id);
  const a = SpreadsheetApp.getActive();
  if (a) return a;
  throw new Error('No spreadsheet available. Run setSpreadsheetId() so it can run headless.');
}

function toast_(msg) {
  try { const a = SpreadsheetApp.getActive(); if (a) a.toast(msg); } catch (e) {}
  Logger.log(msg);
}

/*==============================  PEMO PULL  ===================================================*/

function syncEntity_(ent, fromDate, toDate) {
  const key = getEntityKey_(ent);
  let txns = fetchAllPemo_(key);
  txns.forEach(t => { t.__entity = ent.name; });
  if (fromDate && toDate) {
    const from = Utilities.formatDate(fromDate, 'UTC', 'yyyy-MM-dd');
    const to = Utilities.formatDate(toDate, 'UTC', 'yyyy-MM-dd');
    txns = txns.filter(tx => { const d = txnDate_(tx); return !d || (d >= from && d <= to); });
  }
  const rows = buildRowsFor_(txns);
  writeRows_(ent.sheet, FIELD_MAP.map(f => f.header), rows);
  const posted = postToWebsite_(txns, rows, ent.name);
  if (CONFIG.ACK_MARK_EXPORTED && txns.length) acknowledgeExported_(txns, key);
  return { entity: ent.name, txns: txns.length, rows: rows.length, posted: posted };
}

function syncAllEntities_(fromDate, toDate) {
  return ENTITIES.map(ent => syncEntity_(ent, fromDate, toDate));
}

function pullAllHistory() {
  const res = syncAllEntities_();
  toast_('History pulled — ' + res.map(r => r.entity + ': ' + r.txns +
         (r.posted != null ? '/' + r.posted + ' posted' : '')).join('  |  '));
  return res;
}

function dailyUpdate() {
  const res = syncAllEntities_();
  const summary = res.map(r => '• ' + r.entity + ': ' + r.txns + ' transactions, ' + r.rows + ' rows' +
                 (r.posted != null ? ', ' + r.posted + ' posted' : '')).join('\n');
  toast_('Daily sync done (' + res.length + ' entities).');
  if (CONFIG.EMAIL_TO) {
    MailApp.sendEmail(CONFIG.EMAIL_TO, 'Pemo daily sync (' + res.length + ' entities)',
      summary + '\n\n' + ss_().getUrl());
  }
}

/**
 * FIXED (in effect). This pulls ONE MONTH. Before, writeRows_ cleared the tab first,
 * so running this replaced the entire history with a single month — which is how the
 * Pemo tab went from 4,232 rows to about 130. writeRows_ now merges, so this is safe.
 * The log line says explicitly that it was a partial pull.
 */
function pullPemoTransactions() {
  const now = new Date();
  const from = new Date(now.getFullYear(), now.getMonth(), 1);
  const to = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  const res = syncAllEntities_(from, to);
  toast_('This-month pull (merged into existing history) — ' +
         res.map(r => r.entity + ': ' + r.txns).join('  |  '));
}

/**
 * FIXED. The platform READS the Google Sheet for Pemo (pemo-sync.server.ts); it does not
 * want raw Pemo JSON. The old version posted to /api/public/sync/ingest with
 * `Authorization: Bearer` — wrong route (that one is for GL/AP), wrong header (it wants
 * X-Sync-Key) and wrong body ({table, entity, mode, rows}).
 *
 * This now notifies the Pemo route after the sheet is written.
 * Set PEMO_SYNC_URL = https://www.matchbooksai.com/api/public/pemo/sync
 * and PEMO_SYNC_KEY to whatever that route checks.
 * RUN debugPemoSyncProbe() FIRST to confirm the header — the two marked lines may need
 * adjusting to match src/routes/api/public/pemo/sync.ts.
 */
function postToWebsite_(txns, rows, entity) {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('PEMO_SYNC_URL');
  if (!url) { Logger.log('PEMO_SYNC_URL not set — sheet written, platform not notified.'); return null; }
  const key = props.getProperty('PEMO_SYNC_KEY') || props.getProperty('SYNC_INGEST_KEY') || '';

  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'X-Sync-Key': key },                                           // <- confirm
    payload: JSON.stringify({ entity: entity || '', trigger: 'apps_script' }), // <- confirm
    muteHttpExceptions: true,
    followRedirects: false,
  });

  const code = res.getResponseCode();
  if (code >= 300) {
    throw new Error('Pemo sync trigger failed (' + code + ') for ' + entity + ': ' +
                    res.getContentText().slice(0, 300));
  }
  Logger.log('Platform notified for ' + entity + ' (' + code + '): ' + res.getContentText().slice(0, 200));
  return rows.length;
}

function debugTestWebhook() {
  const ent = ENTITIES[0];
  const sample = fetchAllPemo_(getEntityKey_(ent)).slice(0, 2);
  sample.forEach(t => { t.__entity = ent.name; });
  const n = postToWebsite_(sample, buildRowsFor_(sample), ent.name);
  Logger.log(n == null ? 'No PEMO_SYNC_URL set — nothing sent.' : 'Test POST sent for ' + ent.name + '.');
}

/** NEW. Find the real contract before trusting postToWebsite_. Whichever line is not
 *  401/403 is the header the endpoint wants. */
function debugPemoSyncProbe() {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('PEMO_SYNC_URL') ||
              'https://www.matchbooksai.com/api/public/pemo/sync';
  const key = props.getProperty('PEMO_SYNC_KEY') || props.getProperty('SYNC_INGEST_KEY') || '';
  const body = JSON.stringify({ entity: 'probe', trigger: 'probe' });
  const variants = [
    ['X-Sync-Key',           { 'X-Sync-Key': key }],
    ['x-sync-key',           { 'x-sync-key': key }],
    ['Authorization Bearer', { Authorization: 'Bearer ' + key }],
    ['x-api-key',            { 'x-api-key': key }],
    ['no header',            {}],
  ];
  Logger.log('URL: ' + url);
  variants.forEach(v => {
    const res = UrlFetchApp.fetch(url, { method: 'post', contentType: 'application/json',
      headers: v[1], payload: body, muteHttpExceptions: true, followRedirects: false });
    Logger.log(res.getResponseCode() + '  ' + v[0] + '  -> ' + res.getContentText().slice(0, 160));
  });
}

function buildRowsFor_(txns) {
  const rows = [];
  txns.forEach(tx => {
    rows.push(FIELD_MAP.map(f => valueFor_(tx, f)));
    const fee = feeTotal_(tx);
    if (fee !== '' && Math.abs(fee) > 0) {
      rows.push(FIELD_MAP.map(f => {
        if (f.header === 'Transaction Type') return 'International Fee';
        if (f.header === 'Billing Amount') return fee;
        if (f.header === 'Actual Amount' || f.header === 'Wallet Debited' || f.header === 'Fee Total') return '';
        if (f.header === 'Expense ID / Receipts Name') return '';
        return valueFor_(tx, f);
      }));
    }
  });
  return rows;
}

function fetchAllPemo_(key) {
  if (!key) throw new Error('fetchAllPemo_ needs an API key (call via syncEntity_).');
  const out = [], seen = {};
  let page = 1;
  while (page <= PEMO.MAX_PAGES) {
    const url = PEMO.BASE_URL + PEMO.TX_PATH + '?exportStatus=' + encodeURIComponent(PEMO.EXPORT_STATUS) +
      '&' + PEMO.PARAM_LIMIT + '=' + PEMO.PAGE_SIZE + '&' + PEMO.PARAM_PAGE + '=' + page;
    const res = UrlFetchApp.fetch(url, {
      method: 'get', muteHttpExceptions: true,
      headers: { [PEMO.AUTH_HEADER]: key, 'Accept': 'application/json' },
    });
    const code = res.getResponseCode();
    if (code === 401 || code === 403) throw new Error('Pemo auth failed (' + code + '). Check the apiKey header / scope.');
    if (code >= 400) throw new Error('Pemo API error ' + code + ': ' + res.getContentText().slice(0, 500));
    const arr = extractArray_(JSON.parse(res.getContentText() || '{}'));
    if (!arr.length) break;
    let added = 0;
    arr.forEach(t => { const id = t.id || t.reference; if (id && !seen[id]) { seen[id] = 1; out.push(t); added++; } });
    if (added === 0) break;
    page++; Utilities.sleep(150);
  }
  return out;
}

function acknowledgeExported_(txns, key) {
  const ids = txns.map(t => firstPath_(t, ['id', 'transactionId'])).filter(String);
  if (!ids.length) return;
  const res = UrlFetchApp.fetch(PEMO.BASE_URL + PEMO.TX_PATH, {
    method: 'patch', contentType: 'application/json', muteHttpExceptions: true,
    headers: { [PEMO.AUTH_HEADER]: key },
    payload: JSON.stringify({ operation: 'markAsExported', transactionIds: ids }),
  });
  Logger.log('markAsExported response: ' + res.getResponseCode() + ' for ' + ids.length + ' ids');
}

function debugDumpOnePemoTransaction() {
  const ent = ENTITIES[0];
  const arr = fetchAllPemo_(getEntityKey_(ent));
  if (!arr.length) { Logger.log('No transactions returned for ' + ent.name + '. Check exportStatus / plan / key.'); return; }
  Logger.log(ent.name + ' total returned: ' + arr.length);
  Logger.log('First transaction JSON:\n' + JSON.stringify(arr[0], null, 2));
}

/*==============================  RECONCILIATION (read-only)  ===================================*/

const ZOHO_COLS = { ref: 'reference_number', net: 'net_amount', det: 'transaction_details', date: 'date' };
const PEMO_COLS = { bill: 'Billing Amount', exp: 'Expense ID / Receipts Name',
                    type: 'Transaction Type', date: 'Transaction Date', merch: 'Merchant Name' };

function runReconciliation() {
  ensureControlSheet_();
  const pemo = readObjects_(CONFIG.PEMO_SHEET);
  const zoho = readObjects_(CONFIG.ZOHO_SHEET);
  if (!pemo.length) throw new Error('No rows in "' + CONFIG.PEMO_SHEET + '". Pull Pemo first.');
  if (!zoho.length) throw new Error('No rows in "' + CONFIG.ZOHO_SHEET + '". Paste your Zoho export there.');

  const P = pemo.map(r => ({ bill: num_(r[PEMO_COLS.bill]), exp: String(r[PEMO_COLS.exp] || '').trim(),
                             type: String(r[PEMO_COLS.type] || ''), date: String(r[PEMO_COLS.date] || ''),
                             merch: String(r[PEMO_COLS.merch] || ''), m: false, zr: -1 }))
                 .filter(x => x.bill !== null);
  const Z = zoho.map((r, i) => { const s = String(r[ZOHO_COLS.ref] || '').trim();
                                 const fee = /_fees$/i.test(s);
                                 return { i: i, ref: s, base: fee ? s.slice(0, -5) : s, fee: fee,
                                          net: num_(r[ZOHO_COLS.net]), det: String(r[ZOHO_COLS.det] || ''),
                                          date: String(r[ZOHO_COLS.date] || ''), used: false }; })
                 .filter(x => x.net !== null);

  const byRef = {};
  Z.forEach(z => { if (z.base && !z.fee) (byRef[z.base] = byRef[z.base] || []).push(z); });
  P.forEach(p => { if (!p.exp) return;
    const c = (byRef[p.exp] || []).filter(z => !z.used);
    if (c.length) { c[0].used = true; p.m = true; p.zr = c[0].i; } });

  const pool = {};
  Z.forEach(z => { if (!z.used) (pool[z.net.toFixed(2)] = pool[z.net.toFixed(2)] || []).push(z); });
  P.forEach(p => { if (p.m) return;
    const bucket = pool[p.bill.toFixed(2)];
    if (bucket && bucket.length) { const z = bucket.pop(); z.used = true; p.m = true; p.zr = z.i; } });

  const matched = P.filter(p => p.m);
  const umP = P.filter(p => !p.m);
  const umZ = Z.filter(z => !z.used);
  const sum = a => Math.round(a.reduce((s, x) => s + x, 0) * 100) / 100;

  const zNet = sum(Z.map(z => z.net));
  const pNet = sum(P.map(p => p.bill));
  const fx = sum(matched.map(p => p.bill - Z.find(z => z.i === p.zr).net));
  const sUmP = sum(umP.map(p => p.bill));
  const sUmZ = sum(umZ.map(z => z.net));

  const ctrl = readControl_();
  const zClose = Math.round((ctrl.zoho_open + zNet) * 100) / 100;
  const pClose = Math.round((ctrl.pemo_open + pNet) * 100) / 100;
  const calcPClose = Math.round((zClose + (ctrl.pemo_open - ctrl.zoho_open) + fx + sUmP - sUmZ) * 100) / 100;
  const unexplained = Math.round((calcPClose - pClose) * 100) / 100;

  writeSummary_({ matched, umP, umZ, Z, zNet, pNet, fx, sUmP, sUmZ, zClose, pClose,
                  calcPClose, unexplained, ctrl });
  const msg = 'Recon done. Matched ' + matched.length + ', Pemo-only ' + umP.length +
              ', Zoho-only ' + umZ.length + ', Unexplained ' + unexplained.toFixed(2) + '.';
  toast_(msg);
  return { unexplained: unexplained, msg: msg };
}

function writeSummary_(d) {
  const ss = ss_();
  let sh = ss.getSheetByName(CONFIG.SUMMARY_SHEET);
  if (sh) sh.clear(); else sh = ss.insertSheet(CONFIG.SUMMARY_SHEET);
  const rows = [];
  rows.push(['PEMO – Kuwa Reconciliation (auto)', '', '']);
  rows.push(['Generated', new Date(), '']);
  rows.push(['', '', '']);
  rows.push(['1. BALANCE COMPARISON', 'Zoho', 'PEMO']);
  rows.push(['Opening balance', d.ctrl.zoho_open, d.ctrl.pemo_open]);
  rows.push(['Net movement', d.zNet, d.pNet]);
  rows.push(['Closing balance', d.zClose, d.pClose]);
  rows.push(['', '', '']);
  rows.push(['2. MATCHING', 'Count', '']);
  rows.push(['Matched', d.matched.length, '']);
  rows.push(['Pemo-only (timing/missing)', d.umP.length, '']);
  rows.push(['Zoho-only (timing)', d.umZ.length, '']);
  rows.push(['', '', '']);
  rows.push(['3. BRIDGE (should tie to nil)', 'Amount', '']);
  rows.push(['Zoho closing balance', d.zClose, '']);
  rows.push(['Opening diff (PEMO − Zoho)', Math.round((d.ctrl.pemo_open - d.ctrl.zoho_open) * 100) / 100, '']);
  rows.push(['Amount differences on matched (FX)', d.fx, '']);
  rows.push(['Pemo-only items', d.sUmP, '']);
  rows.push(['Less: Zoho-only items', -d.sUmZ, '']);
  rows.push(['PEMO closing (calculated)', d.calcPClose, '']);
  rows.push(['PEMO closing (actual)', d.pClose, '']);
  rows.push(['UNEXPLAINED', d.unexplained, '']);
  rows.push(['', '', '']);
  rows.push(['4. PEMO-ONLY ITEMS', 'Amount', 'Expense ID']);
  d.umP.forEach(p => rows.push([p.date + '  ' + p.type + '  ' + p.merch, p.bill, p.exp]));
  rows.push(['', '', '']);
  rows.push(['5. ZOHO-ONLY ITEMS', 'Amount', 'Reference']);
  d.umZ.forEach(z => rows.push([z.date + '  ' + z.det.split('\n')[0], z.net, z.ref]));
  sh.getRange(1, 1, rows.length, 3).setValues(rows);
  sh.getRange('A1:C1').merge().setFontWeight('bold').setFontSize(13);
  sh.setColumnWidth(1, 420); sh.setColumnWidth(2, 130); sh.setColumnWidth(3, 220);
  ['B5:C7', 'B14:B22'].forEach(r => sh.getRange(r).setNumberFormat('#,##0.00;(#,##0.00)'));
}

/*==============================  MONTH-END AUTOMATION  ========================================*/

function monthEndRun() {
  const now = new Date();
  const from = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const to = new Date(now.getFullYear(), now.getMonth(), 0);
  const res = syncAllEntities_(from, to);
  const summary = res.map(r => '• ' + r.entity + ': ' + r.txns + ' transactions' +
                 (r.posted != null ? ', ' + r.posted + ' posted' : '')).join('\n');
  if (CONFIG.EMAIL_TO) {
    MailApp.sendEmail(CONFIG.EMAIL_TO,
      'Pemo month-end pull — ' + Utilities.formatDate(to, 'GMT', 'MMM yyyy'),
      summary + '\n\n' + ss_().getUrl());
  }
}

function installMonthEndTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'monthEndRun') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('monthEndRun').timeBased().onMonthDay(1).atHour(7).create();
  toast_('Month-end trigger installed (1st of each month, 07:00).');
}

function installDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'dailyUpdate') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('dailyUpdate').timeBased().everyDays(1).atHour(6).create();
  toast_('Daily sync trigger installed (~6 AM every day).');
}

/*==============================  HELPERS  =====================================================*/

function ensureControlSheet_() {
  const ss = ss_();
  let sh = ss.getSheetByName(CONFIG.CONTROL_SHEET);
  if (!sh) {
    sh = ss.insertSheet(CONFIG.CONTROL_SHEET);
    sh.getRange('A1:B4').setValues([
      ['Control — opening balances for the reconciliation period', ''],
      ['zoho_open', 0],
      ['pemo_open', 0],
      ['note', 'Set these to the prior month closing balances before running recon.'],
    ]);
    sh.getRange('A1:B1').merge().setFontWeight('bold');
  }
  return sh;
}

function readControl_() {
  const sh = ensureControlSheet_();
  const v = sh.getRange('A2:B3').getValues();
  const map = {}; v.forEach(r => map[String(r[0]).trim()] = num_(r[1]) || 0);
  return { zoho_open: map.zoho_open || 0, pemo_open: map.pemo_open || 0 };
}

function readObjects_(name) {
  const sh = ss_().getSheetByName(name);
  if (!sh) throw new Error('Missing tab: "' + name + '".');
  const values = sh.getDataRange().getValues();
  if (values.length < 2) return [];
  let h = 0;
  for (let i = 0; i < Math.min(values.length, 5); i++) {
    const joined = values[i].map(String).join('|').toLowerCase();
    if (joined.indexOf('reference') >= 0 || joined.indexOf('transaction') >= 0 ||
        joined.indexOf('net_amount') >= 0 || joined.indexOf('billing') >= 0) { h = i; break; }
  }
  const headers = values[h].map(x => String(x).trim());
  const out = [];
  for (let i = h + 1; i < values.length; i++) {
    const row = values[i]; if (row.every(c => c === '' || c === null)) continue;
    const o = {}; headers.forEach((k, c) => o[k] = row[c]); out.push(o);
  }
  return out;
}

/**
 * FIXED — MERGE, never clear.
 *
 * The old version called clearContents() then wrote the current result, so any run
 * returning fewer rows than the tab already held silently deleted the rest. That is
 * how the Pemo tab went from 4,232 rows (Aug-2024 onward) to 130 rows covering three
 * weeks: "Pull this month only" replaced the entire history with one month.
 *
 * This upserts on Reference + Transaction Type. Existing rows are updated in place,
 * new rows appended, nothing removed.
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

function firstPath_(obj, paths) {
  for (const p of paths) { const v = getPath_(obj, p); if (v !== undefined && v !== null && v !== '') return v; }
  return '';
}

function getPath_(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function extractArray_(body) {
  if (Array.isArray(body)) return body;
  for (const k of RESPONSE.ARRAY_KEYS) if (Array.isArray(body[k])) return body[k];
  for (const k of Object.keys(body)) if (body[k] && typeof body[k] === 'object')
    for (const j of RESPONSE.ARRAY_KEYS) if (Array.isArray(body[k][j])) return body[k][j];
  return [];
}

function valueFor_(tx, f) {
  if (f.fn) return f.fn(tx);
  let v = firstPath_(tx, f.paths);
  if (f.scale && typeof v === 'number' && Number.isInteger(v)) v = v / PEMO.AMOUNT_DIVISOR;
  if (f.signed && typeof v === 'number') v = v * PEMO_SIGN;
  return v;
}

function txnDate_(tx) {
  const cand = firstPath_(tx, ['transactionDate', 'transaction_date', 'date', 'postedAt', 'createdAt', 'completedAt']);
  if (cand) return String(cand).slice(0, 10);
  const ref = String(firstPath_(tx, ['reference', 'id']) || '');
  const m = ref.match(/(\d{2})(\d{2})(\d{2})/);
  return m ? ('20' + m[1] + '-' + m[2] + '-' + m[3]) : '';
}

function txnType_(tx) {
  return firstPath_(tx, ['transactionType', 'type']) || 'purchase';
}

function acct_(tx, type) {
  const a = tx.accounting || [];
  for (const e of a) if (e && e.type === type) return e.value || '';
  return '';
}

function feeTotal_(tx) {
  const f = tx.feeDetails || {};
  let s = (+f.crossBorder || 0) + (+f.localWithdrawal || 0) + (+f.internationalWithdrawal || 0) + (+f.fx || 0);
  if (!s) return '';
  if (Number.isInteger(s)) s = s / PEMO.AMOUNT_DIVISOR;
  return Math.round(s * PEMO_SIGN * 100) / 100;
}

function num_(x) {
  if (x === '' || x === null || x === undefined) return null;
  if (typeof x === 'number') return Math.round(x * 100) / 100;
  const n = parseFloat(String(x).replace(/,/g, '').trim());
  return isNaN(n) ? null : Math.round(n * 100) / 100;
}

function debugAuthProbe() {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('SYNC_INGEST_URL') || CONFIG.WEBHOOK_URL;
  const token = props.getProperty('SYNC_INGEST_KEY') || '';
  const body = JSON.stringify({ source: 'pemo', entity: 'probe', count: 0, transactions: [] });
  const variants = [
    ['Authorization: Bearer', { Authorization: 'Bearer ' + token }],
    ['apikey',                { apikey: token }],
    ['x-api-key',             { 'x-api-key': token }],
    ['x-ingest-key',          { 'x-ingest-key': token }],
    ['X-Sync-Key',            { 'X-Sync-Key': token }],
    ['Authorization raw',     { Authorization: token }],
  ];
  variants.forEach(v => {
    const res = UrlFetchApp.fetch(url, { method: 'post', contentType: 'application/json',
                                         muteHttpExceptions: true, headers: v[1], payload: body });
    Logger.log(res.getResponseCode() + '   ' + v[0] + '   -> ' + res.getContentText().slice(0, 120));
  });
  Logger.log('\nWhichever line is NOT 401 is the header your endpoint wants.');
}

function debugShowIngestConfig() {
  const p = PropertiesService.getScriptProperties();
  Logger.log('SPREADSHEET_ID  : ' + (p.getProperty('SPREADSHEET_ID') || '(NOT SET — headless runs will fail)'));
  Logger.log('PEMO_SYNC_URL   : ' + (p.getProperty('PEMO_SYNC_URL') || '(none)'));
  const key = p.getProperty('PEMO_SYNC_KEY') || p.getProperty('SYNC_INGEST_KEY') || '';
  Logger.log('KEY length      : ' + key.length + '   last4: ' + key.slice(-4) + '   hasWhitespace: ' + /\s/.test(key));
}
