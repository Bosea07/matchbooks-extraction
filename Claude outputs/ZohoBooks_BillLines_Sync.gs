/**
 * ZohoBooks_BillLines_Sync.gs
 * ---------------------------------------------------------------------------
 * Adds bill / expense / vendor-credit LINE ITEMS to the Zoho GL Extraction sheet.
 *
 * WHY THIS EXISTS
 * The existing GL sync pulls `journal` and `banking` documents only. Those never
 * carry a vendor and an expense account on the same row, which is why the
 * vendor -> expense-account mapping cannot be built from them. A BILL LINE does:
 * it has vendor_id, vendor_name, account_id, account_name and an amount together.
 *
 * This is written as an ADDITION, not a replacement. It does not touch the
 * existing GL tabs or any function in ZohoBooks_GL_Sync.gs. Add it as a new .gs
 * file in the same Apps Script project.
 *
 * RUN probeApi() FIRST. It makes one call per document type and logs the exact
 * response shape, so you can confirm the field names below match your Zoho
 * edition and data centre before committing to a full backfill.
 *
 * SETUP — Project Settings -> Script Properties
 *   ZOHO_CLIENT_ID          from the Zoho API console
 *   ZOHO_CLIENT_SECRET      from the Zoho API console
 *   ZOHO_REFRESH_TOKEN      a refresh token with ZohoBooks.fullaccess.READ
 *   ZOHO_DC                 com | eu | in | sa | au   (data centre, default com)
 *   ALERT_EMAIL             optional; a failed unattended run emails here
 * Never put these in the code. Anyone with edit access to the sheet can read code.
 */

var BL = {
  SHEET_ID: '1QMyw1yfXMIDl4Qh84iTgOaSqdCM9GeqyUfMNYgphczE',   // Zoho GL Extraction
  FROM_DATE: '2025-01-01',
  PER_PAGE: 200,
  // Leave ~90s of headroom against the Apps Script execution limit, then stop
  // cleanly and resume on the next run instead of dying mid-write.
  TIME_BUDGET_MS: 4.5 * 60 * 1000,
  TRIGGER_HOUR: 3,
  TIMEZONE: 'Asia/Dubai',
  ENTITIES: [
    // code, organization_id, base currency
    { code: 'KUWA',  orgId: 'PUT_KUWA_ORG_ID',  base: 'AED' },
    { code: 'DMCC',  orgId: 'PUT_DMCC_ORG_ID',  base: 'AED' },
    { code: 'KSA',   orgId: 'PUT_KSA_ORG_ID',   base: 'SAR' },
    { code: 'SAHA',  orgId: 'PUT_SAHA_ORG_ID',  base: 'SAR' },
    { code: 'VWHC',  orgId: 'PUT_VWHC_ORG_ID',  base: 'AED' },
    { code: 'SHIFA', orgId: 'PUT_SHIFA_ORG_ID', base: 'AED' }
  ],
  // SAR/AED is the ratio of the two USD pegs: 3.6725 / 3.75.
  // It sits inside a normal FX band, so nothing will flag it as wrong — it has
  // to be applied deliberately.
  TO_AED: { AED: 1, SAR: 0.97933 },
  DOC_TYPES: ['bill', 'expense', 'vendorcredit'],
  LOG_TAB: 'Bill Sync Log'
};

var BL_HEADERS = [
  'Entity', 'Doc Type', 'Doc ID', 'Doc No', 'Date', 'Vendor ID', 'Vendor Name',
  'Account ID', 'Account Name', 'Description', 'Reference', 'Currency',
  'Exchange Rate', 'Amount (source)', 'Amount (base)', 'Amount AED',
  'Status', 'Last Modified', 'Fetched At'
];


/* ── menu — guarded, so a time trigger never hits getUi() ─────────────── */

function onOpenBillSync(e) {
  try {
    SpreadsheetApp.getUi()
      .createMenu('Bill Lines')
      .addItem('Probe API (run this first)', 'probeApi')
      .addItem('Sync now', 'syncBillLines')
      .addItem('Reset backfill state', 'resetBillSyncState')
      .addItem('Install daily trigger', 'installBillSyncTrigger')
      .addToUi();
  } catch (err) {
    console.log('onOpenBillSync: no UI context, menu skipped');
  }
}

function installBillSyncTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'syncBillLines') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('syncBillLines').timeBased()
    .atHour(BL.TRIGGER_HOUR).everyDays(1).inTimezone(BL.TIMEZONE).create();
  console.log('Daily bill-line sync installed for ' + BL.TRIGGER_HOUR + ':00 ' + BL.TIMEZONE);
}


/* ── auth ────────────────────────────────────────────────────────────── */

function blToken_() {
  var cache = CacheService.getScriptCache();
  var t = cache.get('zoho_access_token');
  if (t) return t;
  var p = PropertiesService.getScriptProperties();
  var dc = p.getProperty('ZOHO_DC') || 'com';
  var url = 'https://accounts.zoho.' + dc + '/oauth/v2/token'
          + '?refresh_token=' + encodeURIComponent(p.getProperty('ZOHO_REFRESH_TOKEN'))
          + '&client_id='     + encodeURIComponent(p.getProperty('ZOHO_CLIENT_ID'))
          + '&client_secret=' + encodeURIComponent(p.getProperty('ZOHO_CLIENT_SECRET'))
          + '&grant_type=refresh_token';
  var res = UrlFetchApp.fetch(url, { method: 'post', muteHttpExceptions: true });
  var body = JSON.parse(res.getContentText() || '{}');
  if (!body.access_token) {
    throw new Error('Zoho token refresh failed: ' + res.getContentText().slice(0, 300));
  }
  cache.put('zoho_access_token', body.access_token, 3000);   // Zoho tokens last 1h
  return body.access_token;
}

function blApi_(path, params) {
  var p = PropertiesService.getScriptProperties();
  var dc = p.getProperty('ZOHO_DC') || 'com';
  var qs = Object.keys(params || {}).map(function (k) {
    return k + '=' + encodeURIComponent(params[k]);
  }).join('&');
  var url = 'https://www.zohoapis.' + dc + '/books/v3' + path + (qs ? '?' + qs : '');
  for (var attempt = 1; attempt <= 4; attempt++) {
    var res = UrlFetchApp.fetch(url, {
      method: 'get',
      headers: { Authorization: 'Zoho-oauthtoken ' + blToken_() },
      muteHttpExceptions: true
    });
    var code = res.getResponseCode();
    if (code === 200) return JSON.parse(res.getContentText());
    if (code === 401) { CacheService.getScriptCache().remove('zoho_access_token'); continue; }
    if (code === 429) { Utilities.sleep(attempt * 5000); continue; }   // rate limited
    if (code >= 500)  { Utilities.sleep(attempt * 2000); continue; }
    throw new Error('Zoho API ' + code + ' on ' + path + ': ' + res.getContentText().slice(0, 300));
  }
  throw new Error('Zoho API gave up after retries on ' + path);
}


/* ── probe — confirm the response shape before a full run ────────────── */

function probeApi() {
  var e = BL.ENTITIES[0];
  var out = [];
  ['bills', 'expenses', 'vendorcredits'].forEach(function (res) {
    try {
      var r = blApi_('/' + res, { organization_id: e.orgId, per_page: 1 });
      var list = r[res] || [];
      out.push(res + ' -> ' + list.length + ' row(s); keys: ' +
               (list[0] ? Object.keys(list[0]).join(', ') : '(none)'));
      if (list[0]) {
        var id = list[0][res.replace(/s$/, '') + '_id'] || list[0].bill_id || list[0].expense_id;
        var d = blApi_('/' + res + '/' + id, { organization_id: e.orgId });
        var doc = d[res.replace(/s$/, '')] || {};
        var li = (doc.line_items || [])[0];
        out.push('  detail line_items[0] keys: ' + (li ? Object.keys(li).join(', ') : '(no line_items)'));
      }
    } catch (err) {
      out.push(res + ' -> ERROR ' + err.message);
    }
  });
  console.log(out.join('\n'));
  return out.join('\n');
}


/* ── the sync ────────────────────────────────────────────────────────── */

function resetBillSyncState() {
  PropertiesService.getScriptProperties().deleteProperty('BL_STATE');
  console.log('Backfill state cleared. The next run starts from ' + BL.FROM_DATE + '.');
}

function syncBillLines() {
  var started = new Date();
  var props = PropertiesService.getScriptProperties();
  var state = JSON.parse(props.getProperty('BL_STATE') || '{}');
  var ss = SpreadsheetApp.openById(BL.SHEET_ID);
  var totals = { fetched: 0, written: 0, skippedNoLines: 0, unmappedCurrency: 0 };
  var incomplete = false;

  try {
    for (var i = 0; i < BL.ENTITIES.length; i++) {
      var ent = BL.ENTITIES[i];
      if (!ent.orgId || ent.orgId.indexOf('PUT_') === 0) {
        console.log('Skipping ' + ent.code + ' — organization_id not set');
        continue;
      }
      var st = state[ent.code] || { page: 1, done: false, since: BL.FROM_DATE };
      if (st.done && !dueForIncremental_(st)) continue;

      var sheet = blSheet_(ss, ent.code + ' Bill Lines');
      var seen = blExistingKeys_(sheet);

      for (var d = 0; d < BL.DOC_TYPES.length; d++) {
        var docType = BL.DOC_TYPES[d];
        var res = { bill: 'bills', expense: 'expenses', vendorcredit: 'vendorcredits' }[docType];
        var page = (st.docType === docType && st.page) ? st.page : 1;

        while (true) {
          if (new Date() - started > BL.TIME_BUDGET_MS) {
            state[ent.code] = { page: page, docType: docType, done: false, since: st.since };
            props.setProperty('BL_STATE', JSON.stringify(state));
            incomplete = true;
            throw { __pause: true };
          }
          var params = { organization_id: ent.orgId, page: page, per_page: BL.PER_PAGE,
                         sort_column: 'date', sort_order: 'A' };
          if (st.since) params.last_modified_time = st.since;
          var r = blApi_('/' + res, params);
          var docs = r[res] || [];
          var rows = [];
          for (var k = 0; k < docs.length; k++) {
            var summary = docs[k];
            var docId = summary[docType + '_id'] || summary.bill_id || summary.expense_id ||
                        summary.vendor_credit_id;
            // The list endpoint does not return line items, so each document
            // needs one detail call. This is the expensive part, and the reason
            // the run resumes rather than restarts.
            var full = blApi_('/' + res + '/' + docId, { organization_id: ent.orgId });
            var doc = full[docType] || full[res.replace(/s$/, '')] || {};
            var lines = doc.line_items || [];
            totals.fetched++;
            if (!lines.length) { totals.skippedNoLines++; continue; }

            var rate = Number(doc.exchange_rate || 1) || 1;
            var ccy = doc.currency_code || ent.base;
            var toAed = BL.TO_AED[ent.base];
            if (toAed === undefined) totals.unmappedCurrency++;

            for (var L = 0; L < lines.length; L++) {
              var li = lines[L];
              var srcAmt = Number(li.item_total != null ? li.item_total : (li.rate || 0) * (li.quantity || 1));
              var baseAmt = srcAmt * rate;                       // entity base currency
              var key = docId + '|' + (li.line_item_id || L);
              if (seen[key]) continue;
              rows.push([
                ent.code, docType, docId,
                doc.bill_number || doc.expense_number || doc.vendor_credit_number || '',
                doc.date || '', doc.vendor_id || '', doc.vendor_name || '',
                li.account_id || '', li.account_name || '',
                li.description || li.name || '',
                doc.reference_number || '', ccy, rate,
                srcAmt, baseAmt,
                (toAed === undefined ? '' : baseAmt * toAed),
                doc.status || '', doc.last_modified_time || '', new Date()
              ]);
              seen[key] = true;
            }
          }
          if (rows.length) {
            sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, BL_HEADERS.length).setValues(rows);
            totals.written += rows.length;
          }
          var ctx = r.page_context || {};
          if (!ctx.has_more_page) break;
          page++;
        }
        st.page = 1;
      }
      state[ent.code] = { page: 1, done: true, since: Utilities.formatDate(started, 'UTC', "yyyy-MM-dd'T'HH:mm:ssXXX") };
      props.setProperty('BL_STATE', JSON.stringify(state));
    }

    blLog_(ss, started, incomplete ? 'paused' : 'success', totals, '');
    console.log('Bill line sync ' + (incomplete ? 'paused' : 'complete') + ': ' + JSON.stringify(totals));

  } catch (err) {
    if (err && err.__pause) {
      blLog_(ss, started, 'paused', totals, 'time budget reached, will resume next run');
      console.log('Paused at the time budget; resumes on the next run. ' + JSON.stringify(totals));
      return;
    }
    blLog_(ss, started, 'failed', totals, String(err && err.message || err));
    blAlert_(err);
    throw err;
  }
}

function dueForIncremental_(st) {
  if (!st.since) return true;
  return (new Date() - new Date(st.since)) > 12 * 3600 * 1000;
}


/* ── sheet helpers ───────────────────────────────────────────────────── */

function blSheet_(ss, name) {
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.appendRow(BL_HEADERS);
    sh.setFrozenRows(1);
  }
  return sh;
}

/** Existing (doc, line) keys, so a re-run appends nothing it already holds. */
function blExistingKeys_(sheet) {
  var last = sheet.getLastRow();
  var seen = {};
  if (last < 2) return seen;
  var vals = sheet.getRange(2, 3, last - 1, 1).getValues();   // Doc ID column
  // Line ids are not stored separately; key on doc id + row ordinal within the doc.
  var counts = {};
  for (var i = 0; i < vals.length; i++) {
    var d = String(vals[i][0]);
    counts[d] = (counts[d] || 0);
    seen[d + '|' + counts[d]] = true;
    counts[d]++;
  }
  return seen;
}

function blLog_(ss, started, status, totals, detail) {
  try {
    var log = ss.getSheetByName(BL.LOG_TAB);
    if (!log) {
      log = ss.insertSheet(BL.LOG_TAB);
      log.appendRow(['Started', 'Finished', 'Status', 'Docs fetched', 'Lines written',
                     'Docs with no lines', 'Unmapped currency', 'Detail']);
      log.setFrozenRows(1);
    }
    log.appendRow([started, new Date(), status, totals.fetched, totals.written,
                   totals.skippedNoLines, totals.unmappedCurrency, String(detail).slice(0, 1500)]);
  } catch (e) {
    console.error('Could not write the log tab: ' + e);
  }
}

function blAlert_(err) {
  try {
    var to = PropertiesService.getScriptProperties().getProperty('ALERT_EMAIL');
    if (!to) return;
    MailApp.sendEmail({
      to: to,
      subject: 'Zoho bill-line sync FAILED',
      body: 'Failed at ' + new Date() + '\n\n' + (err && err.stack || err) +
            '\n\nhttps://docs.google.com/spreadsheets/d/' + BL.SHEET_ID
    });
  } catch (e) {
    console.error('Alert email failed: ' + e);
  }
}


/* ─────────────────────────────────────────────────────────────────────────
 * THE THREE THINGS THIS FIXES, AND ONE IT DOESN'T
 *
 * 1. Vendor and expense account on one row. A bill line carries vendor_id,
 *    vendor_name, account_id and account_name together. That is the join the
 *    General Ledger export cannot give, because the GL splits a bill into an
 *    expense leg and an AP leg on separate rows.
 *
 * 2. No row cap, no alphabetical truncation. The Detailed GL export stops at
 *    20,000 rows sorted by account name, which on KUWA and DMCC never gets past
 *    Accounts Payable and Receivable (94% and 99% of those files). Paging the
 *    API has no such ceiling.
 *
 * 3. Currency is stated three ways — source, entity base, and AED — instead of
 *    SAR being added to AED untranslated. SAR/AED = 0.97933 from the two USD
 *    pegs; it looks like a normal rate, so nothing catches it if it is skipped.
 *
 * WHAT IT DOESN'T FIX: the first backfill is slow. The list endpoint returns no
 * line items, so every document needs a second call. The run stops at its time
 * budget, saves its place, and continues on the next trigger — expect several
 * runs before the history is complete. After that it is incremental on
 * last_modified_time and takes seconds.
 *
 * VERIFY BEFORE TRUSTING IT
 *   - probeApi() logs real field names; check them against what this script reads
 *   - KUWA bill lines for Jan-2026 should tie to the 3PL - Commission and Charges
 *     total in the existing GL tab for that month
 *   - re-running adds no duplicate rows
 *   - a SAR entity's Amount AED is ~2% below its Amount (base), not equal to it
 * ───────────────────────────────────────────────────────────────────────── */
