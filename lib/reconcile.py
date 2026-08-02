"""MatchBooks reconciliation engine — deterministic, tiered, auditable.

Tiers (every match records which tier produced it):
  1 exact    : normalized reference equality
  2 relaxed  : digits-only / zero-stripped / suffix reference equality,
               single-character ref typos (806G000834 ~ 8066000834)
  3 value    : unmatched refs paired by amount within tolerance
               (unique amounts, or same-sign within a date window)
  4 combo    : one side's item equals the sum of 2-3 leftover items
               on the other side (offset payments, split bills)

Lines are first aggregated by reference per side (many-to-one: several
lines under one CN/RVT). Categories: MATCHED, AMOUNT_DIFF,
EXTRA_IN_VENDOR (missing in our books), MISSING_IN_VENDOR (only in ours).
Response is contract-compatible with the original matchbooks-api."""
import itertools
import re
from collections import defaultdict

TOL_DEFAULT = 1.0
DATE_WINDOW_DEFAULT = 7  # days, for tier-3 same-sign pairing


# ── reference canon helpers ─────────────────────────────────────────────
def _digits(ref):
    return re.sub(r'\D', '', ref or '')

def _alnum(ref):
    return re.sub(r'[^A-Z0-9]', '', (ref or '').upper())

def _zstrip(ref):
    d = _digits(ref)
    return d.lstrip('0') or d

def _one_typo(a, b):
    """Same length, exactly one differing character (806G000834 vs 8066000834)."""
    if len(a) != len(b) or len(a) < 6 or a == b:
        return False
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def _day(dateiso):
    try:
        y, m, d = str(dateiso)[:10].split('-')
        return int(y) * 372 + int(m) * 31 + int(d)
    except Exception:
        return None


# ── aggregation ─────────────────────────────────────────────────────────
def _aggregate(rows):
    agg = {}
    for r in rows:
        ref = str(r.get('ref') or '').strip()
        if not ref or ref.upper() in ('TOTALS', 'TOTAL'):
            continue
        try:
            amt = round(float(r.get('amount')), 2)
        except (TypeError, ValueError):
            continue
        e = agg.setdefault(ref, {
            'ref': ref, 'refRaw': r.get('refRaw') or ref, 'amount': 0.0,
            'lines': 0, 'date': r.get('dateISO') or None,
            'dateRaw': r.get('date') or '', 'types': []})
        e['amount'] = round(e['amount'] + amt, 2)
        e['lines'] += 1
        t = r.get('type') or 'Invoice'
        if t not in e['types']:
            e['types'].append(t)
        if not e['date'] and r.get('dateISO'):
            e['date'] = r.get('dateISO')
    return agg


# ── main entry ──────────────────────────────────────────────────────────
def _is_payment(r):
    return str(r.get('type', '')).strip().lower() in ('payment', 'payment made', 'receipt')


def _match_payment_lane(v_pay, z_pay, tolerance, date_window):
    """Line-level payment matching: ref equality first, then amount(+date)."""
    used_z, pairs = set(), []
    for i, v in enumerate(v_pay):
        for j, z in enumerate(z_pay):
            if j in used_z:
                continue
            if v['ref'] and v['ref'] == z['ref']:
                pairs.append((i, j, 1, 'payment ref match'))
                used_z.add(j)
                break
    for i, v in enumerate(v_pay):
        if any(p[0] == i for p in pairs):
            continue
        cands = [j for j, z in enumerate(z_pay) if j not in used_z
                 and abs(abs(z['amount']) - abs(v['amount'])) <= tolerance]
        if len(cands) > 1:
            vd = _day(v.get('dateISO'))
            dated = [j for j in cands if vd and _day(z_pay[j].get('dateISO'))
                     and abs(_day(z_pay[j].get('dateISO')) - vd) <= date_window]
            cands = dated or cands
        if len(cands) == 1:
            pairs.append((i, cands[0], 3, 'payment amount match'))
            used_z.add(cands[0])
    return pairs


def reconcile(vendor_rows, zoho_rows, tolerance=TOL_DEFAULT,
              date_window=DATE_WINDOW_DEFAULT, enable_combos=True):
    v_pay = [dict(r, amount=round(float(r['amount']), 2)) for r in vendor_rows
             if _is_payment(r) and r.get('amount') is not None]
    z_pay = [dict(r, amount=round(float(r['amount']), 2)) for r in zoho_rows
             if _is_payment(r) and r.get('amount') is not None]
    vendor_rows = [r for r in vendor_rows if not _is_payment(r)]
    zoho_rows = [r for r in zoho_rows if not _is_payment(r)]
    pay_pairs = _match_payment_lane(v_pay, z_pay, tolerance, date_window)
    vm, zm = _aggregate(vendor_rows), _aggregate(zoho_rows)
    results, pairs = [], []          # pairs: (vref, zref, tier, note)
    v_open = set(vm.keys())
    z_open = set(zm.keys())

    def pair(vref, zref, tier, note=''):
        pairs.append((vref, zref, tier, note))
        v_open.discard(vref)
        z_open.discard(zref)

    # tier 1 — exact normalized ref
    for vref in sorted(v_open):
        if vref in z_open:
            pair(vref, vref, 1)

    # tier 2 — relaxed reference forms
    def _index(refs, fn):
        idx = defaultdict(list)
        for r in refs:
            k = fn(r)
            if k:
                idx[k].append(r)
        return idx
    for fn, label in ((_alnum, 'alnum'), (_zstrip, 'zero-stripped'), (_digits, 'digits-only')):
        if not v_open or not z_open:
            break
        zidx = _index(z_open, fn)
        for vref in sorted(list(v_open)):
            cands = zidx.get(fn(vref), [])
            cands = [c for c in cands if c in z_open]
            if len(cands) == 1:
                pair(vref, cands[0], 2, f'ref match ({label})')
    # single-typo refs, only when amounts also agree within tolerance
    for vref in sorted(list(v_open)):
        va = _alnum(vref)
        hits = [z for z in z_open if _one_typo(va, _alnum(z))
                and abs(vm[vref]['amount'] - zm[z]['amount']) <= tolerance]
        if len(hits) == 1:
            pair(vref, hits[0], 2, 'ref match (1-char difference)')

    # tier 3 — value matching for the leftovers
    def _amount_key(x):
        return round(abs(x['amount']), 2)
    z_by_amt = defaultdict(list)
    for z in z_open:
        z_by_amt[_amount_key(zm[z])].append(z)
    for vref in sorted(list(v_open), key=lambda r: -abs(vm[r]['amount'])):
        v = vm[vref]
        cands = [z for z in z_by_amt.get(_amount_key(v), []) if z in z_open]
        if not cands:
            cands = [z for z in z_open
                     if abs(abs(zm[z]['amount']) - abs(v['amount'])) <= tolerance]
        if not cands:
            continue
        same_sign = [z for z in cands if (zm[z]['amount'] >= 0) == (v['amount'] >= 0)]
        cands = same_sign or cands
        if len(cands) == 1:
            pair(vref, cands[0], 3, 'amount match (unique value)')
            continue
        vd = _day(v['date'])
        dated = [z for z in cands if vd and _day(zm[z]['date'])
                 and abs(_day(zm[z]['date']) - vd) <= date_window]
        if len(dated) == 1:
            pair(vref, dated[0], 3, f'amount+date match (±{date_window}d)')

    # tier 4 — combination sums (2-3 leftover lines equal one line opposite)
    if enable_combos and v_open and z_open and len(v_open) + len(z_open) <= 80:
        def _try_combos(single_side, single_map, multi_side, multi_map, direction):
            for sref in sorted(list(single_side), key=lambda r: -abs(single_map[r]['amount'])):
                target = single_map[sref]['amount']
                pool = sorted(multi_side, key=lambda r: -abs(multi_map[r]['amount']))[:25]
                found = None
                for n in (2, 3):
                    for combo in itertools.combinations(pool, n):
                        s = round(sum(multi_map[c]['amount'] for c in combo), 2)
                        if abs(s - target) <= tolerance:
                            found = combo
                            break
                    if found:
                        break
                if found:
                    note = f"{direction}: {sref} = " + ' + '.join(found)
                    resid = round(target - sum(multi_map[c]['amount'] for c in found), 2)
                    for c in found:
                        multi_side.discard(c)
                    single_side.discard(sref)
                    combo_findings.append({'ref': sref, 'parts': list(found),
                                           'direction': direction, 'note': note,
                                           'resid': resid})
        combo_findings = []
        _try_combos(v_open, vm, z_open, zm, 'vendor line equals sum of our lines')
        _try_combos(z_open, zm, v_open, vm, 'our line equals sum of vendor lines')
    else:
        combo_findings = []

    # ── build results ───────────────────────────────────────────────────
    matched = amount_diff = 0
    matched_resid = 0.0
    for vref, zref, tier, note in pairs:
        v, z = vm[vref], zm[zref]
        diff = round(v['amount'] - z['amount'], 2)
        ok = abs(diff) <= tolerance
        if ok:
            matched += 1
            matched_resid += diff
        else:
            amount_diff += 1
        results.append({
            'ref': vref if vref == zref else f'{vref} = {zref}',
            'refRaw': v['refRaw'], 'date': v['dateRaw'] or z['dateRaw'],
            'dateISO': v['date'] or z['date'],
            'type': (v['types'] or z['types'] or ['Invoice'])[0],
            'vendorAmt': v['amount'], 'zohoAmt': z['amount'],
            'diff': diff if not ok else 0.0,
            'status': 'MATCHED' if ok else 'AMOUNT_DIFF',
            'tier': tier, 'note': note,
        })
    for f in combo_findings:
        results.append({'ref': f['ref'], 'refRaw': f['ref'], 'date': '', 'dateISO': None,
                        'type': 'Combination', 'vendorAmt': None, 'zohoAmt': None,
                        'diff': 0.0, 'status': 'MATCHED', 'tier': 4, 'note': f['note']})
        matched += 1
        matched_resid += f['resid'] if f['direction'].startswith('vendor line') else -f['resid']
    for vref in sorted(v_open):
        v = vm[vref]
        results.append({'ref': vref, 'refRaw': v['refRaw'], 'date': v['dateRaw'],
                        'dateISO': v['date'], 'type': (v['types'] or ['Invoice'])[0],
                        'vendorAmt': v['amount'], 'zohoAmt': None, 'diff': None,
                        'status': 'EXTRA_IN_VENDOR', 'tier': 0, 'note': 'missing in our books'})
    for zref in sorted(z_open):
        z = zm[zref]
        results.append({'ref': zref, 'refRaw': z['refRaw'], 'date': z['dateRaw'],
                        'dateISO': z['date'], 'type': (z['types'] or ['Invoice'])[0],
                        'vendorAmt': None, 'zohoAmt': z['amount'], 'diff': None,
                        'status': 'MISSING_IN_VENDOR', 'tier': 0, 'note': 'only in our books'})

    # payment lane results
    v_used = {p[0] for p in pay_pairs}
    z_used = {p[1] for p in pay_pairs}
    for i, j, tier, note in pay_pairs:
        v, z = v_pay[i], z_pay[j]
        d = round(v['amount'] - z['amount'], 2)
        ok = abs(d) <= tolerance
        if ok:
            matched += 1
            matched_resid += d
        else:
            amount_diff += 1
        ref = v['ref'] if v['ref'] == z['ref'] else f"{v['ref']} = {z['ref']}"
        results.append({'ref': ref, 'refRaw': v.get('refRaw') or v['ref'],
                        'date': v.get('date') or z.get('date') or '',
                        'dateISO': v.get('dateISO') or z.get('dateISO'),
                        'type': 'Payment', 'vendorAmt': v['amount'], 'zohoAmt': z['amount'],
                        'diff': d if not ok else 0.0,
                        'status': 'MATCHED' if ok else 'AMOUNT_DIFF',
                        'tier': tier, 'note': note})
    for i, v in enumerate(v_pay):
        if i not in v_used:
            results.append({'ref': v['ref'], 'refRaw': v.get('refRaw') or v['ref'],
                            'date': v.get('date') or '', 'dateISO': v.get('dateISO'),
                            'type': 'Payment', 'vendorAmt': v['amount'], 'zohoAmt': None,
                            'diff': None, 'status': 'EXTRA_IN_VENDOR', 'tier': 0,
                            'note': 'payment on vendor SOA not found in our books'})
    for j, z in enumerate(z_pay):
        if j not in z_used:
            results.append({'ref': z['ref'], 'refRaw': z.get('refRaw') or z['ref'],
                            'date': z.get('date') or '', 'dateISO': z.get('dateISO'),
                            'type': 'Payment', 'vendorAmt': None, 'zohoAmt': z['amount'],
                            'diff': None, 'status': 'MISSING_IN_VENDOR', 'tier': 0,
                            'note': 'our payment not reflected on vendor SOA'})

    # same-side contra detection (net-zero clusters) — annotate, do not match
    contra_notes = []
    open_v_rows = [r for r in results if r['status'] == 'EXTRA_IN_VENDOR']
    for a, b in itertools.combinations(open_v_rows, 2):
        va, vb = a.get('vendorAmt'), b.get('vendorAmt')
        if va is not None and vb is not None and abs(round(va + vb, 2)) <= tolerance and va != 0:
            note = f"possible vendor-side contra: {a['ref']} ({va}) offsets {b['ref']} ({vb})"
            a['note'] = (a['note'] + ' · ' if a['note'] else '') + 'possible contra with ' + b['ref']
            b['note'] = (b['note'] + ' · ' if b['note'] else '') + 'possible contra with ' + a['ref']
            contra_notes.append(note)

    order = {'AMOUNT_DIFF': 0, 'EXTRA_IN_VENDOR': 1, 'MISSING_IN_VENDOR': 2, 'MATCHED': 3}
    results.sort(key=lambda r: (order.get(r['status'], 9), -(abs(r['diff'] or r['vendorAmt'] or r['zohoAmt'] or 0))))

    vendor_net = round(sum(v['amount'] for v in vm.values()) + sum(p['amount'] for p in v_pay), 2)
    zoho_net = round(sum(z['amount'] for z in zm.values()) + sum(p['amount'] for p in z_pay), 2)
    n_extra = sum(1 for r in results if r['status'] == 'EXTRA_IN_VENDOR')
    n_missing = sum(1 for r in results if r['status'] == 'MISSING_IN_VENDOR')
    extra_val = round(sum(r['vendorAmt'] or 0 for r in results if r['status'] == 'EXTRA_IN_VENDOR'), 2)
    missing_val = round(sum(r['zohoAmt'] or 0 for r in results if r['status'] == 'MISSING_IN_VENDOR'), 2)
    diff_val = round(sum(r['vendorAmt'] - r['zohoAmt'] for r in results
                         if r['status'] == 'AMOUNT_DIFF' and r['vendorAmt'] is not None
                         and r['zohoAmt'] is not None), 2)

    summary = {
        'totalReferences': len(results),
        'matched': matched, 'amountDiff': amount_diff,
        'extraInVendor': n_extra, 'missingInVendor': n_missing,
        'netDifference': round(vendor_net - zoho_net, 2),
        'vendorNet': vendor_net, 'zohoNet': zoho_net,
        'amountDiffValue': diff_val,
        'extraInVendorValue': extra_val, 'missingInVendorValue': missing_val,
        'matchedByTier': {str(t): sum(1 for p in pairs if p[2] == t) for t in (1, 2, 3)},
        'combosMatched': len(combo_findings),
        'tolerance': tolerance,
        'matchedResidual': round(matched_resid, 2),
        'findings': [f['note'] for f in combo_findings] + contra_notes,
    }

    # ── invariants: fail loudly, never silently wrong ───────────────────
    assert matched + amount_diff + n_extra + n_missing == len(results), 'row count invariant'
    recon_gap = round(diff_val + round(matched_resid, 2) + extra_val - missing_val, 2)
    assert abs(recon_gap - summary['netDifference']) <= 0.02, \
        f"value invariant: {recon_gap} != {summary['netDifference']}"
    summary['invariantsOk'] = True
    return {'summary': summary, 'results': results}
