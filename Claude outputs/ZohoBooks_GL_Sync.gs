/**
 * MatchBooks — Zoho Books GL dump → its own Google Sheet (standalone script)
 * Lives in a SEPARATE spreadsheet from the AP/transactions sync.
 *
 * COMPLETE FILE: the original GL sync plus the bill/expense/vendor-credit
 * line-item sync. Replace the whole contents of ZohoBooks_GL_Sync.gs with this.
 *
 * SETUP:
 * 1. Create a new Google Sheet (e.g. "MatchBooks GL"), Extensions → Apps Script,
 *    paste this file.
 * 2. Project Settings → Script Properties (same values as the AP script):
 *      ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN
 *      ZOHO_ACCOUNTS_BASE = https://accounts.zoho.com
 *      ZOHO_API_BASE      = https://www.zohoapis.com/books/v3
 *      SYNC_INGEST_URL / SYNC_INGEST_KEY   (optional, pushes to the website)
 * 3. Run debugGL() first and send the log output — the GL fetch below gets
 *    finalized against whichever report endpoint your Zoho edition exposes.
 * 4. syncCOA() works immediately (chart of accounts dump per entity).
 */

const ENTITIES = {
  KUWA:  '814624081',
  SAHA:  '906420266',
  DMCC:  '751813311',
  KSA:   '841313343',
  SHIFA: '851585320',
  VWHC:  '899870447',
};

// how far back the GL dump goes (edit as needed)
const GL_FROM_DATE = '2024-01-01';

// ─── auth (same pattern as the AP script; separate token cache) ──────────
function getAccessToken_() {
  const cache = CacheService.getScriptCache();
  const cached = cache.get('zoho_gl_token');
  if (cached) return cached;
  const P = PropertiesService.getScriptProperties();
  const url = P.getProperty('ZOHO_ACCOUNTS_BASE') + '/oauth/v2/token' +
    '?refresh_token=' + encodeURIComponent(P.getProperty('ZOHO_REFRESH_TOKEN')) +
    '&client_id='     + encodeURIComponent(P.getProperty('ZOHO_CLIENT_ID')) +
    '&client_secret=' + encodeURIComponent(P.getProperty('ZOHO_CLIENT_SECRET')) +
    '&grant_type=refresh_token';
  const res = JSON.parse(UrlFetchApp.fetch(url, { method: 'post', muteHttpExceptions: true }).getContentText());
  if (!res.access_token) throw new Error('Zoho token refresh failed: ' + JSON.stringify(res));
  cache.put('zoho_gl_token', res.access_token, 3300);
  return res.access_token;
}

function zget_(path, params) {
  const P = PropertiesService.getScriptProperties();
  const token = getAccessToken_();
  const url = P.getProperty('ZOHO_API_BASE') + '/' + path +
    (path.indexOf('?') < 0 ? '?' : '&') + params;
  const r = UrlFetchApp.fetch(url, {
    headers: { Authorization: 'Zoho-oauthtoken ' + token }, muteHttpExceptions: true });
  return r;
}

// ─── PROBE — run this FIRST and share the log ────────────────────────────
function debugGL() {
  ['reports/generalledger', 'reports/accounttransactions', 'reports/expensedetails',
   'reports/profitandloss', 'chartofaccounts', 'journals'].forEach(res => {
    const r = zget_(res, 'organization_id=' + ENTITIES.KUWA +
      '&from_date=' + GL_FROM_DATE + '&to_date=2026-08-31&per_page=5&page=1');
    Logger.log('── ' + res + ': HTTP ' + r.getResponseCode());
    Logger.log(r.getContentText().slice(0, 900));
  });
}

// ═══ DETAILED GL LINES — assembled from journals + banking transactions ═══
// Tab "{ENTITY} GL Lines": Source | Doc ID | Doc No | Date | Account | D/C |
// Amount | Currency | Exch Rate | Party | Description. Also pushed to the
// website table gl_lines (sources 'journal' and 'banking'; bill/expense/
// vendorcredit sources come from syncBillLines_ below).

function glLinesKUWA()  { syncGLLines_('KUWA');  }
function glLinesSAHA()  { syncGLLines_('SAHA');  }
function glLinesDMCC()  { syncGLLines_('DMCC');  }
function glLinesKSA()   { syncGLLines_('KSA');   }
function glLinesSHIFA() { syncGLLines_('SHIFA'); }
function glLinesVWHC()  { syncGLLines_('VWHC');  }
function glLinesAllStaggered() {
  Object.keys(ENTITIES).forEach((name, i) => {
    ScriptApp.newTrigger('glLines' + name).timeBased().after((i * 8 + 1) * 60 * 1000).create();
  });
}

function syncGLLines_(name) {
  const orgId = ENTITIES[name];
  const lines = [];

  // ── 1. journal lines (detail call per journal; cached by modified time) ──
  const jcache = jnCacheLoad_(name);
  const jlist = pagedList_('journals', 'journals', orgId);
  const jpend = jlist.filter(j => String(j.journal_date || '') >= GL_FROM_DATE &&
    (!jcache[j.journal_id] || jcache[j.journal_id].mod !== j.last_modified_time));
  jpend.slice(0, 300).forEach(j => {
    const r = zget_('journals/' + j.journal_id, 'organization_id=' + orgId);
    const d = JSON.parse(r.getContentText()).journal;
    if (d) {
      jcache[j.journal_id] = { mod: j.last_modified_time,
        lines: JSON.stringify((d.line_items || []).map(li => ({
          acc: li.account_name || '', dc: li.debit_or_credit || '',
          amt: li.amount != null ? li.amount : li.bcy_amount,
          ds: (li.description || '').slice(0, 120) }))),
        date: d.journal_date, no: d.entry_number,
        cur: d.currency_code, rate: d.exchange_rate };
    }
    Utilities.sleep(150);
  });
  jnCacheSave_(name, jcache);
  Object.keys(jcache).forEach(id => {
    const j = jcache[id];
    let arr = []; try { arr = JSON.parse(j.lines); } catch (e) {}
    arr.forEach(li => lines.push(['journal', id, j.no, j.date, li.acc, li.dc,
      li.amt, j.cur, j.rate, '', li.ds]));
  });

  // ── 2. banking transactions (already line-level, no detail calls) ────────
  let bank = [];
  try { bank = pagedList_('banktransactions', 'banktransactions', orgId,
    '&date_start=' + GL_FROM_DATE); }
  catch (e) { bank = pagedList_('banktransactions', 'banktransactions', orgId)
    .filter(t => String(t.date || '') >= GL_FROM_DATE); }
  bank.forEach(t => lines.push(['banking', t.transaction_id, t.transaction_type || '',
    t.date, t.account_name || '', t.debit_or_credit || '', t.amount,
    t.currency_code || '', t.exchange_rate != null ? t.exchange_rate : '',
    t.payee || t.offset_account_name || '', (t.description || '').slice(0, 120)]));

  writeTab_(name + ' GL Lines',
    ['Source','Doc ID','Doc No','Date','Account','D/C','Amount','Currency','Exch Rate','Party','Description'],
    lines);

  // ── 3. push to the website (gl_lines; replace this entity's journal+banking) ─
  const P = PropertiesService.getScriptProperties();
  if (P.getProperty('SYNC_INGEST_URL') && P.getProperty('SYNC_INGEST_KEY')) {
    const rows = lines.map(l => ({ entity: name, source: l[0], doc_id: String(l[1]),
      doc_no: String(l[2] || ''), date: l[3] || null, account: l[4] || null,
      dc: l[5] || null, amount: l[6], currency: l[7] || null,
      exchange_rate: l[8] === '' ? null : l[8], party: l[9] || null, description: l[10] || null }));
    for (let i = 0; i < rows.length; i += 1000) {
      const res = UrlFetchApp.fetch(P.getProperty('SYNC_INGEST_URL'), {
        method: 'post', contentType: 'application/json',
        headers: { 'X-Sync-Key': P.getProperty('SYNC_INGEST_KEY') },
        payload: JSON.stringify({ table: 'gl_lines', entity: name,
          mode: i === 0 ? 'replace_sources' : 'append',
          sources: ['journal', 'banking'], rows: rows.slice(i, i + 1000) }),
        muteHttpExceptions: true,
      });
      if (res.getResponseCode() >= 300) {
        log_('GL Lines ' + name, 'ingest FAILED: ' + res.getContentText().slice(0, 150));
        break;
      }
    }
  }
  log_('GL Lines ' + name, lines.length + ' lines | journals pending: ' +
    Math.max(jpend.length - 300, 0));
}

function jnCacheLoad_(name) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name + ' JNCache');
  const map = {};
  if (!sh || sh.getLastRow() < 2) return map;
  sh.getRange(2, 1, sh.getLastRow() - 1, 7).getValues().forEach(r => {
    if (r[0]) map[String(r[0])] = { mod: r[1], lines: r[2], date: r[3], no: r[4], cur: r[5], rate: r[6] };
  });
  return map;
}
function jnCacheSave_(name, map) {
  writeTab_(name + ' JNCache',
    ['Journal ID','Modified','Lines','Date','No','Currency','Rate'],
    Object.keys(map).map(id => { const j = map[id];
      return [id, j.mod, j.lines, j.date, j.no, j.cur, j.rate]; }));
}

/**
 * CHANGED: the page cap was 60, a silent 12,000-row ceiling at per_page=200.
 * It returned a short list with nothing to say so — which is how the GL Lines
 * tabs ended up truncated. The limit is raised, and hitting it now throws.
 */
function pagedList_(resource, key, orgId, extraQ) {
  const rows = [];
  const MAX_PAGES = 400;                       // 80,000 rows at per_page=200
  let page = 1, more = true;
  while (more && page <= MAX_PAGES) {
    const r = zget_(resource, 'organization_id=' + orgId + '&per_page=200&page=' + page + (extraQ || ''));
    if (r.getResponseCode() === 429) { Utilities.sleep(15000); continue; }
    const body = JSON.parse(r.getContentText());
    if (body.code !== 0) throw new Error(resource + ' (org ' + orgId + ', p' + page + '): ' +
      r.getContentText().slice(0, 200));
    (body[key] || []).forEach(x => rows.push(x));
    more = body.page_context && body.page_context.has_more_page;
    page++;
    Utilities.sleep(200);
  }
  if (more) throw new Error(resource + ' (org ' + orgId + '): hit the ' + MAX_PAGES +
    '-page cap with more pages left — raise MAX_PAGES or filter by date. ' +
    'Returning a short list silently is how the GL tabs got truncated.');
  return rows;
}

// ─── PROBE: can we get TRANSACTION-LEVEL GL per account? ─────────────────
function debugDetailedGL() {
  let acct = null;
  const r0 = zget_('chartofaccounts', 'organization_id=' + ENTITIES.KUWA +
    '&filter_by=AccountType.Expense&per_page=5&page=1');
  const coa = (JSON.parse(r0.getContentText()).chartofaccounts || []);
  acct = coa.find(a => a.is_active) || coa[0];
  if (!acct) { Logger.log('no expense account found'); return; }
  Logger.log('Testing with account: ' + acct.account_name + ' (' + acct.account_id + ')');

  const tests = [
    ['reports/accounttransactions', 'account_id=' + acct.account_id],
    ['reports/generalledger', 'account_id=' + acct.account_id],
    ['reports/generalledger', 'account_id=' + acct.account_id + '&show_details=true'],
    ['banktransactions', 'account_id=' + acct.account_id],
  ];
  tests.forEach(t => {
    const r = zget_(t[0], 'organization_id=' + ENTITIES.KUWA +
      '&from_date=2026-06-01&to_date=2026-06-30&per_page=5&page=1&' + t[1]);
    Logger.log('── ' + t[0] + ' +' + t[1].split('&').pop() + ': HTTP ' + r.getResponseCode());
    Logger.log(r.getContentText().slice(0, 700));
  });

  const rj = zget_('journals', 'organization_id=' + ENTITIES.KUWA + '&per_page=1&page=1');
  const j = (JSON.parse(rj.getContentText()).journals || [])[0];
  if (j) {
    const rd = zget_('journals/' + j.journal_id, 'organization_id=' + ENTITIES.KUWA);
    Logger.log('── journal detail keys: ' +
      Object.keys(JSON.parse(rd.getContentText()).journal || {}).join(', ').slice(0, 400));
    const lines = (JSON.parse(rd.getContentText()).journal || {}).line_items || [];
    Logger.log('── journal line sample: ' + JSON.stringify(lines[0] || {}).slice(0, 300));
  }
}

// ─── Chart of Accounts (works on all editions) ───────────────────────────
function syncCOA() {
  Object.keys(ENTITIES).forEach(name => {
    const rows = [];
    let page = 1, more = true;
    while (more && page <= 20) {
      const r = zget_('chartofaccounts', 'organization_id=' + ENTITIES[name] +
        '&per_page=200&page=' + page);
      const body = JSON.parse(r.getContentText());
      if (body.code !== 0) throw new Error('COA ' + name + ' p' + page + ': ' +
        r.getContentText().slice(0, 200));
      (body.chartofaccounts || []).forEach(a => rows.push([
        name, a.account_id, a.account_name, a.account_type, a.account_code || '',
        a.is_active, a.parent_account_name || '',
      ]));
      more = body.page_context && body.page_context.has_more_page;
      page++;
      Utilities.sleep(200);
    }
    writeTab_(name + ' COA',
      ['Entity','Account ID','Account Name','Type','Code','Active','Parent'], rows);
  });
  log_('COA', 'all entities dumped');
}

// ─── GL dump: reports/generalledger, one call per month per entity ───────
function syncGL_KUWA()  { syncGL_('KUWA');  }
function syncGL_SAHA()  { syncGL_('SAHA');  }
function syncGL_DMCC()  { syncGL_('DMCC');  }
function syncGL_KSA()   { syncGL_('KSA');   }
function syncGL_SHIFA() { syncGL_('SHIFA'); }
function syncGL_VWHC()  { syncGL_('VWHC');  }

/** One-off staggered full run for all entities (8 min apart). */
function syncGLAllStaggered() {
  Object.keys(ENTITIES).forEach((name, i) => {
    ScriptApp.newTrigger('syncGL_' + name).timeBased()
      .after((i * 8 + 1) * 60 * 1000).create();
  });
}

/** Monthly triggers: each entity refreshes on the 1st, staggered. Run ONCE. */
function setupGLTriggers() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (/^syncGL_/.test(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  });
  Object.keys(ENTITIES).forEach((name, i) => {
    ScriptApp.newTrigger('syncGL_' + name).timeBased()
      .onMonthDay(1).atHour(3 + Math.floor(i / 2)).create();
  });
}

function syncGL_(name) {
  const orgId = ENTITIES[name];
  const types = accountTypes_(orgId);           // account_id → account_type
  const months = monthsSince_(GL_FROM_DATE);
  const rows = [];
  months.forEach(m => {
    let page = 1, more = true;
    while (more && page <= 10) {
      const r = zget_('reports/generalledger', 'organization_id=' + orgId +
        '&from_date=' + m.from + '&to_date=' + m.to + '&per_page=500&page=' + page);
      if (r.getResponseCode() === 429) { Utilities.sleep(20000); continue; }
      const body = JSON.parse(r.getContentText());
      if (body.code !== 0) throw new Error('GL ' + name + ' ' + m.label + ': ' +
        r.getContentText().slice(0, 200));
      (body.generalledger || []).forEach(a => {
        const dr = Number(a.debit_total) || 0, cr = Number(a.credit_total) || 0;
        if (!dr && !cr) return;
        const bal = Number(a.balance) || 0;
        rows.push([name, m.label, a.name, types[a.account_id] || '',
          dr, cr, a.is_debit ? bal : -bal]);
      });
      more = body.page_context && body.page_context.has_more_page;
      page++;
      Utilities.sleep(300);
    }
  });
  writeTab_(name + ' GL',
    ['Entity','Month','Account','Type','Debit','Credit','Net'], rows);

  const P = PropertiesService.getScriptProperties();
  if (P.getProperty('SYNC_INGEST_URL') && P.getProperty('SYNC_INGEST_KEY')) {
    try {
      const body = rows.map(r => ({ entity: r[0], month: r[1], account: r[2],
        account_type: r[3], debit: r[4], credit: r[5], net: r[6] }));
      for (let i = 0; i < body.length; i += 1000) {
        const res = UrlFetchApp.fetch(P.getProperty('SYNC_INGEST_URL'), {
          method: 'post', contentType: 'application/json',
          headers: { 'X-Sync-Key': P.getProperty('SYNC_INGEST_KEY') },
          payload: JSON.stringify({ table: 'gl_monthly', entity: name,
            mode: i === 0 ? 'replace' : 'append', rows: body.slice(i, i + 1000) }),
          muteHttpExceptions: true,
        });
        if (res.getResponseCode() >= 300) throw new Error(res.getContentText().slice(0, 200));
      }
      log_('GL ' + name, rows.length + ' rows / ' + months.length + ' months | ingest: pushed');
      return;
    } catch (e) {
      log_('GL ' + name, rows.length + ' rows | ingest FAILED: ' + String(e).slice(0, 150));
      return;
    }
  }
  log_('GL ' + name, rows.length + ' rows / ' + months.length + ' months');
}

function accountTypes_(orgId) {
  const map = {};
  let page = 1, more = true;
  while (more && page <= 20) {
    const r = zget_('chartofaccounts', 'organization_id=' + orgId + '&per_page=200&page=' + page);
    const body = JSON.parse(r.getContentText());
    if (body.code !== 0) break;
    (body.chartofaccounts || []).forEach(a => map[a.account_id] = a.account_type);
    more = body.page_context && body.page_context.has_more_page;
    page++;
    Utilities.sleep(200);
  }
  return map;
}

function monthsSince_(fromISO) {
  const out = [];
  const start = new Date(fromISO + 'T00:00:00');
  const now = new Date();
  const d = new Date(start.getFullYear(), start.getMonth(), 1);
  while (d <= now) {
    const y = d.getFullYear(), mo = d.getMonth();
    const last = new Date(y, mo + 1, 0);
    const p = n => (n < 10 ? '0' : '') + n;
    out.push({ from: y + '-' + p(mo + 1) + '-01',
               to: y + '-' + p(mo + 1) + '-' + p(last.getDate()),
               label: y + '-' + p(mo + 1) });
    d.setMonth(mo + 1);
  }
  return out;
}

// ─── helpers ──────────────────────────────────────────────────────────────
function writeTab_(tabName, header, rows) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(tabName);
  if (!sh) sh = ss.insertSheet(tabName);
  sh.clear();
  sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
  sh.setFrozenRows(1);
  if (rows.length) sh.getRange(2, 1, rows.length, header.length).setValues(rows);
}

function log_(what, detail) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName('Sync Log') || ss.insertSheet('Sync Log');
  sh.appendRow([new Date(), what, detail]);
}


// ═══════════════════════════════════════════════════════════════════════════
// BILL / EXPENSE / VENDOR-CREDIT LINE ITEMS
//
// journals + banking never put a vendor and an expense account on the same row,
// so vendor -> account cannot be derived from them. A bill line does:
// vendor_name and account_name together, with the amount.
//
// Writes "{ENTITY} Bill Lines" and pushes to gl_lines with sources
// 'bill' / 'expense' / 'vendorcredit' — same ingest contract as syncGLLines_.
//
// ORDER: debugBillLines() → billLinesKUWA() → billLinesAllStaggered()
//        → setupBillLineTriggers() once the backfill reads "pending 0"
// ═══════════════════════════════════════════════════════════════════════════

// Detail calls per run, per document type. The backfill resumes next run.
const BILL_DETAIL_BUDGET = 300;

// Entity base currency → AED. SAR/AED is the ratio of the two USD pegs
// (3.6725 / 3.75). It looks like an ordinary rate, so it has to be applied
// deliberately — nothing downstream flags SAR added to AED untranslated.
const BASE_CCY = { KUWA: 'AED', DMCC: 'AED', SHIFA: 'AED', VWHC: 'AED',
                   KSA: 'SAR', SAHA: 'SAR' };
const TO_AED   = { AED: 1, SAR: 0.97933 };

// resource, list key, singular key, id field, number field
const BILL_DOCS = [
  ['bills',         'bills',         'bill',          'bill_id',          'bill_number'],
  ['expenses',      'expenses',      'expense',       'expense_id',       'expense_number'],
  ['vendorcredits', 'vendorcredits', 'vendor_credit', 'vendor_credit_id', 'vendor_credit_number'],
];

function billLinesKUWA()  { syncBillLines_('KUWA');  }
function billLinesSAHA()  { syncBillLines_('SAHA');  }
function billLinesDMCC()  { syncBillLines_('DMCC');  }
function billLinesKSA()   { syncBillLines_('KSA');   }
function billLinesSHIFA() { syncBillLines_('SHIFA'); }
function billLinesVWHC()  { syncBillLines_('VWHC');  }

function billLinesAllStaggered() {
  Object.keys(ENTITIES).forEach((name, i) => {
    ScriptApp.newTrigger('billLines' + name).timeBased()
      .after((i * 8 + 1) * 60 * 1000).create();
  });
}

/** Daily refresh, staggered. Run ONCE, after the backfill has caught up. */
function setupBillLineTriggers() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (/^billLines/.test(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  });
  Object.keys(ENTITIES).forEach((name, i) => {
    ScriptApp.newTrigger('billLines' + name).timeBased()
      .everyDays(1).atHour(2 + Math.floor(i / 2)).create();
  });
}

// ─── PROBE — run first, share the log ────────────────────────────────────
function debugBillLines() {
  BILL_DOCS.forEach(d => {
    const r = zget_(d[0], 'organization_id=' + ENTITIES.KUWA + '&per_page=1&page=1');
    Logger.log('── ' + d[0] + ': HTTP ' + r.getResponseCode());
    const list = (JSON.parse(r.getContentText())[d[1]] || []);
    Logger.log('   list keys: ' + Object.keys(list[0] || {}).join(', ').slice(0, 400));
    if (!list[0]) return;
    const rd = zget_(d[0] + '/' + list[0][d[3]], 'organization_id=' + ENTITIES.KUWA);
    const doc = JSON.parse(rd.getContentText())[d[2]] || {};
    Logger.log('   detail keys: ' + Object.keys(doc).join(', ').slice(0, 400));
    Logger.log('   line_items[0]: ' + JSON.stringify((doc.line_items || [])[0] || {}).slice(0, 400));
  });
}

function syncBillLines_(name) {
  const orgId = ENTITIES[name];
  const base  = BASE_CCY[name] || 'AED';
  const cache = blCacheLoad_(name);
  const lines = [];
  let pending = 0, fetched = 0, noLines = 0;

  BILL_DOCS.forEach(d => {
    const res = d[0], listKey = d[1], docKey = d[2], idField = d[3], noField = d[4];

    let list = [];
    try {
      list = pagedList_(res, listKey, orgId);
    } catch (e) {
      log_('Bill Lines ' + name, res + ' list FAILED: ' + String(e).slice(0, 150));
      return;
    }

    // Only fetch detail for documents that are new or changed since last run.
    const stale = list.filter(x => {
      const dt = String(x.date || x.bill_date || '');
      if (dt && dt < GL_FROM_DATE) return false;
      const c = cache[x[idField]];
      return !c || c.mod !== x.last_modified_time;
    });
    pending += Math.max(stale.length - BILL_DETAIL_BUDGET, 0);

    stale.slice(0, BILL_DETAIL_BUDGET).forEach(x => {
      const r = zget_(res + '/' + x[idField], 'organization_id=' + orgId);
      if (r.getResponseCode() === 429) { Utilities.sleep(15000); return; }
      const doc = JSON.parse(r.getContentText())[docKey];
      fetched++;
      if (!doc) return;
      const li = doc.line_items || [];
      if (!li.length) noLines++;
      cache[x[idField]] = {
        mod:   x.last_modified_time,
        typ:   docKey,
        date:  doc.date || '',
        no:    doc[noField] || '',
        party: doc.vendor_name || doc.contact_name || '',
        ref:   doc.reference_number || '',
        cur:   doc.currency_code || base,
        rate:  doc.exchange_rate != null ? doc.exchange_rate : 1,
        lines: JSON.stringify(li.map(l => ({
          acc: l.account_name || '',
          amt: l.item_total != null ? l.item_total
                : (Number(l.rate || 0) * Number(l.quantity != null ? l.quantity : 1)),
          ds: (l.description || l.name || '').slice(0, 120),
        }))),
      };
      Utilities.sleep(150);
    });
  });

  blCacheSave_(name, cache);

  const toAed = TO_AED[base];
  Object.keys(cache).forEach(id => {
    const c = cache[id];
    let arr = [];
    try { arr = JSON.parse(c.lines); } catch (e) {}
    arr.forEach(l => {
      const src = Number(l.amt) || 0;
      const baseAmt = src * (Number(c.rate) || 1);
      lines.push([c.typ === 'vendor_credit' ? 'vendorcredit' : c.typ,
        id, c.no, c.date, l.acc, 'debit', src, c.cur, c.rate, c.party, l.ds,
        baseAmt, toAed === undefined ? '' : baseAmt * toAed, c.ref]);
    });
  });

  writeTab_(name + ' Bill Lines',
    ['Source','Doc ID','Doc No','Date','Account','D/C','Amount','Currency','Exch Rate',
     'Party','Description','Amount (base)','Amount AED','Reference'],
    lines);

  const P = PropertiesService.getScriptProperties();
  if (P.getProperty('SYNC_INGEST_URL') && P.getProperty('SYNC_INGEST_KEY')) {
    const rows = lines.map(l => ({ entity: name, source: l[0], doc_id: String(l[1]),
      doc_no: String(l[2] || ''), date: l[3] || null, account: l[4] || null,
      dc: l[5], amount: l[6], currency: l[7] || null,
      exchange_rate: l[8] === '' ? null : l[8], party: l[9] || null,
      description: l[10] || null }));
    for (let i = 0; i < rows.length; i += 1000) {
      const r = UrlFetchApp.fetch(P.getProperty('SYNC_INGEST_URL'), {
        method: 'post', contentType: 'application/json',
        headers: { 'X-Sync-Key': P.getProperty('SYNC_INGEST_KEY') },
        payload: JSON.stringify({ table: 'gl_lines', entity: name,
          mode: i === 0 ? 'replace_sources' : 'append',
          sources: ['bill', 'expense', 'vendorcredit'], rows: rows.slice(i, i + 1000) }),
        muteHttpExceptions: true,
      });
      if (r.getResponseCode() >= 300) {
        log_('Bill Lines ' + name, 'ingest FAILED: ' + r.getContentText().slice(0, 150));
        break;
      }
    }
  }

  log_('Bill Lines ' + name, lines.length + ' lines from ' + Object.keys(cache).length +
    ' docs | fetched ' + fetched + ' | no line items ' + noLines +
    ' | pending ' + pending + (pending ? '  ← RUN AGAIN' : ''));
}

function blCacheLoad_(name) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name + ' BillCache');
  const map = {};
  if (!sh || sh.getLastRow() < 2) return map;
  sh.getRange(2, 1, sh.getLastRow() - 1, 10).getValues().forEach(r => {
    if (r[0]) map[String(r[0])] = { mod: r[1], typ: r[2], date: r[3], no: r[4],
      party: r[5], ref: r[6], cur: r[7], rate: r[8], lines: r[9] };
  });
  return map;
}

function blCacheSave_(name, map) {
  writeTab_(name + ' BillCache',
    ['Doc ID','Modified','Type','Date','No','Party','Reference','Currency','Rate','Lines'],
    Object.keys(map).map(id => { const c = map[id];
      return [id, c.mod, c.typ, c.date, c.no, c.party, c.ref, c.cur, c.rate, c.lines]; }));
}

/* VERIFY
 *  - debugBillLines() logs real field names; check account_name, item_total,
 *    vendor_name, exchange_rate against what syncBillLines_ reads
 *  - KUWA Bill Lines for Jan-2026 on "3PL - Commission and Charges" should tie
 *    to that account and month in the existing KUWA GL tab
 *  - a KSA or SAHA row has Amount AED ~2% below Amount (base); equal means the
 *    peg was not applied
 *  - re-running changes nothing — only modified docs refetch
 *  - the log line ends with "pending 0" when the backfill is complete
 *
 * EXPECT SEVERAL RUNS. The list endpoints return no line items, so every
 * document costs one detail call, budgeted at 300 per run per type. KUWA alone
 * has roughly 3,000 bills over 2025-26.
 */
