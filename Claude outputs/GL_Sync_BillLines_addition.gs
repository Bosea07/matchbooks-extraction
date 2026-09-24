/**
 * ADD TO THE EXISTING GL SCRIPT — bill / expense / vendor-credit LINE ITEMS
 * ---------------------------------------------------------------------------
 * Paste this block at the end of the GL script. It reuses zget_, pagedList_,
 * writeTab_, log_ and ENTITIES, and follows the same JNCache caching pattern.
 * Nothing existing is renamed or removed.
 *
 * WHY: journals + banking never put a vendor and an expense account on the same
 * row, so vendor -> account cannot be derived from them. A bill line does:
 * vendor_name and account_name together, with the amount.
 *
 * Writes tab "{ENTITY} Bill Lines" and pushes to gl_lines with sources
 * 'bill' / 'expense' / 'vendorcredit', using the same ingest contract as
 * syncGLLines_ so the endpoint needs no change.
 *
 * ORDER OF WORK
 *   1. debugBillLines()        confirm the field names on your edition
 *   2. billLinesKUWA()         one entity, check the tab
 *   3. billLinesAllStaggered() the rest
 *   4. setupBillLineTriggers() daily, once the backfill has caught up
 */

// Detail calls per run, per document type. The backfill resumes on the next run;
// the log line reports how many are still pending.
const BILL_DETAIL_BUDGET = 300;

// Entity base currency -> AED. SAR/AED is the ratio of the two USD pegs
// (3.6725 / 3.75). It looks like an ordinary rate, so it has to be applied
// deliberately — nothing downstream will flag SAR added to AED untranslated.
const BASE_CCY = { KUWA: 'AED', DMCC: 'AED', SHIFA: 'AED', VWHC: 'AED',
                   KSA: 'SAR', SAHA: 'SAR' };
const TO_AED   = { AED: 1, SAR: 0.97933 };

// resource, response key, singular key, id field, number field
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


// ─── the sync ────────────────────────────────────────────────────────────
function syncBillLines_(name) {
  const orgId = ENTITIES[name];
  const base  = BASE_CCY[name] || 'AED';
  const cache = blCacheLoad_(name);
  const lines = [];
  let pending = 0, fetched = 0, noLines = 0;

  BILL_DOCS.forEach(d => {
    const [res, listKey, docKey, idField, noField] = d;

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
      if (!li.length) { noLines++; }
      cache[x[idField]] = {
        mod:  x.last_modified_time,
        typ:  docKey,
        date: doc.date || '',
        no:   doc[noField] || '',
        party: doc.vendor_name || doc.contact_name || '',
        ref:  doc.reference_number || '',
        cur:  doc.currency_code || base,
        rate: doc.exchange_rate != null ? doc.exchange_rate : 1,
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

  // ── push to the website, same contract as syncGLLines_ ─────────────────
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


/* ═══ ONE EDIT TO AN EXISTING FUNCTION ════════════════════════════════════
 * pagedList_ stops at page 60. At per_page=200 that is a silent 12,000-row
 * ceiling — it returns short and nothing says so, which is how the GL Lines
 * tabs ended up capped. Replace the existing pagedList_ with this version:
 * the limit is raised and hitting it now throws instead of truncating quietly.

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
 * ═══════════════════════════════════════════════════════════════════════ */


/* ─────────────────────────────────────────────────────────────────────────
 * VERIFY
 *   - debugBillLines() logs real field names; check them against what this
 *     reads (account_name, item_total, vendor_name, exchange_rate)
 *   - KUWA Bill Lines for Jan-2026 on "3PL - Commission and Charges" should
 *     tie to that account/month in the existing KUWA GL tab
 *   - a KSA or SAHA row has Amount AED about 2% below Amount (base); if the two
 *     are equal, the peg was not applied
 *   - re-running changes nothing: the cache means only modified docs refetch
 *   - the log line ends with "pending 0" once the backfill is complete
 *
 * EXPECT SEVERAL RUNS. The list endpoints return no line items, so every
 * document costs one detail call, budgeted at 300 per run per type. KUWA alone
 * has roughly 3,000 bills over 2025-26. Run billLinesKUWA() repeatedly until
 * the log says pending 0, then set the daily triggers.
 * ───────────────────────────────────────────────────────────────────────── */
