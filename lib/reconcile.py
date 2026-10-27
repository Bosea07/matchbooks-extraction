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
from collections import Counter, defaultdict

TOL_DEFAULT = 1.0
DATE_WINDOW_DEFAULT = 7  # days, for tier-3 same-sign pairing
RATE_TOL_DEFAULT = 0.10  # ±10% around the implied rate, when the two sides
                         # are in different currencies
# Two different questions, two different tolerances. "Are these the same
# transaction?" is answered generously and in proportion to size — a 9.05 gap on
# a 1.34m payment is a bank charge, not a different payment. "Do the amounts
# agree?" is then answered strictly, and any gap is reported as a difference.
# Sharing one tolerance turns a single small discrepancy into two enormous
# phantom exceptions, one on each side.
# 2% was too generous: it paired DN/HO/2026/335 (1,806.99) with invoice 5439
# (1,771.00) — two unrelated documents 35.99 apart — which both invented a
# discrepancy and orphaned a genuine match. 0.5% keeps the bank-charge case
# working while refusing pairs that only look close because they are large.
PAIR_TOL_PCT_DEFAULT = 0.005
# Above this absolute gap, closeness alone is not evidence: the two rows must
# also agree on date before they may be paired.
PAIR_ABS_NEEDS_DATE = 25.0
NEAREST_HINT_PCT = 0.25   # how far apart two unmatched rows can be and
                          # still be worth pointing at each other
SCALE_BAND = (0.80, 1.25)  # median ratio inside this band = same currency
MIN_RATE_SAMPLE = 3        # reference-matched rows needed to trust a rate


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


def _prefix_of(vref, zref):
    """The letters one side puts in front of the other's number: Humantra
    quotes Shopify order "2391", we book bill "AEB2391". Returns 'AEB', or ''
    when the two do not differ by a leading prefix alone."""
    a, b = _alnum(vref), _alnum(zref)
    for short, long_ in ((a, b), (b, a)):
        if short and long_ != short and long_.endswith(short):
            head = long_[:-len(short)]
            if head.isalpha() and len(head) <= 6:
                return head
    return ''


def _is_synthetic(ref):
    """A reference the extractor invented for a row that had a valid amount
    but no readable reference. Such rows carry real money and must be
    reconciled, but must never match by reference — only by amount/date."""
    return str(ref or '').startswith('~')


def _day(dateiso):
    try:
        y, m, d = str(dateiso)[:10].split('-')
        return int(y) * 372 + int(m) * 31 + int(d)
    except Exception:
        return None


# ── aggregation ─────────────────────────────────────────────────────────
def _aggregate(rows, side, dropped):
    """Aggregate by reference. Rows with no reference are NOT discarded —
    they are given a unique synthetic key so their value stays in the
    reconciliation. Only rows with an unusable amount are dropped, and those
    are counted so the caller can report them."""
    agg = {}
    for i, r in enumerate(rows):
        ref = str(r.get('ref') or '').strip()
        if ref.upper() in ('TOTALS', 'TOTAL'):
            continue
        try:
            amt = round(float(r.get('amount')), 2)
        except (TypeError, ValueError):
            dropped.append({'side': side, 'ref': ref,
                            'amount': r.get('amount'), 'reason': 'unreadable amount'})
            continue
        if not ref:
            ref = f'~{side.upper()}{i + 1}'
        e = agg.setdefault(ref, {
            'ref': ref, 'refRaw': r.get('refRaw') or ref, 'amount': 0.0,
            'lines': 0, 'date': r.get('dateISO') or None,
            'dateRaw': r.get('date') or '', 'types': [], 'aliases': set(),
            'docIds': set(), 'statuses': [], 'orig': 0.0})
        # Two DIFFERENT documents under one number are not one document in
        # two lines — on our side that is a bill booked twice. Kept apart from
        # the line count so the two cases can be told apart.
        did = r.get('docId')
        if did not in (None, ''):
            e['docIds'].add(str(did))
        st = str(r.get('status') or '').strip().lower()
        if st and st not in e['statuses']:
            e['statuses'].append(st)
        try:
            e['orig'] = round(e['orig'] + float(r.get('originalAmount', amt)), 2)
        except (TypeError, ValueError):
            e['orig'] = round(e['orig'] + amt, 2)
        for a in (r.get('refAliases') or [ref]):
            if a and not _is_synthetic(a):
                e['aliases'].add(str(a).strip().upper())
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


def _match_payment_lane(v_pay, z_pay, tolerance, date_window, rate=None,
                        rate_tol=None, pairable=None):
    """Line-level payment matching: ref equality first, then amount(+date).

    When `rate` is given the two sides are in different currencies, so the
    amount pass compares the ratio of the two figures against the rate implied
    by the reference-matched rows instead of their difference."""
    used_z, pairs = set(), []
    for i, v in enumerate(v_pay):
        for j, z in enumerate(z_pay):
            if j in used_z:
                continue
            if v['ref'] and v['ref'] == z['ref']:
                pairs.append((i, j, 1, 'payment ref match'))
                used_z.add(j)
                break

    def _near(v, z):
        if rate:
            if not v['amount'] or not z['amount']:
                return False
            if (v['amount'] >= 0) != (z['amount'] >= 0):
                return False
            return abs(abs(v['amount']) / abs(z['amount']) / rate - 1) <= rate_tol
        if pairable:
            return pairable(v['amount'], z['amount'])
        return abs(abs(z['amount']) - abs(v['amount'])) <= tolerance

    def _dates_agree(v, z):
        vd, zd = _day(v.get('dateISO')), _day(z.get('dateISO'))
        return bool(vd and zd and abs(zd - vd) <= date_window)

    for i, v in enumerate(v_pay):
        if any(p[0] == i for p in pairs):
            continue
        cands = [j for j, z in enumerate(z_pay) if j not in used_z and _near(v, z)]
        # Being the only near-enough candidate is not evidence on its own. A
        # payment of 46,046.34 paired with one of 45,885.48 fourteen months
        # earlier purely because nothing else was close — and in doing so it
        # consumed the row its real counterpart needed, turning one bad match
        # into two exceptions. Past a material gap the dates must agree too.
        # Exact amounts still pair freely: those are the same payment.
        if not rate:
            cands = [j for j in cands
                     if abs(abs(v['amount']) - abs(z_pay[j]['amount'])) <= PAIR_ABS_NEEDS_DATE
                     or _dates_agree(v, z_pay[j])]
        if len(cands) > 1:
            dated = [j for j in cands if _dates_agree(v, z_pay[j])]
            cands = dated or cands
        if len(cands) == 1:
            pairs.append((i, cands[0], 3,
                          'payment rate match' if rate else 'payment amount match'))
            used_z.add(cands[0])
    return pairs


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 6)


def _detect_scale(pairs, vm, zm):
    """Infer whether the two statements are denominated differently.

    Neither a Tally ledger nor a Zoho SOA reliably prints a currency code next
    to each figure, so the currency is deduced from evidence instead: the ratio
    of the reference-matched rows. Same-currency books cluster at 1.0; an
    INR ledger against AED books clusters around 24. Returns (rate, ratios) or
    (None, ratios) when the two sides are on the same scale."""
    ratios = []
    for vref, zref, _tier, _note in pairs:
        va, za = vm[vref]['amount'], zm[zref]['amount']
        if not va or not za or (va >= 0) != (za >= 0):
            continue
        ratios.append(abs(va) / abs(za))
    if len(ratios) < MIN_RATE_SAMPLE:
        return None, ratios
    med = _median(ratios)
    if med is None or SCALE_BAND[0] <= med <= SCALE_BAND[1]:
        return None, ratios
    return med, ratios


def reconcile(vendor_rows, zoho_rows, tolerance=TOL_DEFAULT,
              date_window=DATE_WINDOW_DEFAULT, enable_combos=True,
              rate_tolerance=RATE_TOL_DEFAULT,
              pair_tolerance_pct=PAIR_TOL_PCT_DEFAULT,
              vendor_states_total=None, zoho_states_total=None,
              vendor_statement_date=None):
    from .normalize import parse_date

    def _iso(r):
        if r.get('dateISO'):
            return r
        iso, _ = parse_date(r.get('date'))
        return dict(r, dateISO=iso) if iso else r
    vendor_rows = [_iso(r) for r in vendor_rows]
    zoho_rows = [_iso(r) for r in zoho_rows]

    # ── void documents are not in the books ───────────────────────────────
    # A voided bill keeps its number, its total and even a "balance" in the
    # sync, and nothing else about it says it does not count. Excluded here,
    # counted, and said out loud.
    void_rows = [r for r in zoho_rows
                 if str(r.get('status') or '').strip().lower() == 'void']
    if void_rows:
        zoho_rows = [r for r in zoho_rows
                     if str(r.get('status') or '').strip().lower() != 'void']

    # ── open-items statements ─────────────────────────────────────────────
    # Some suppliers send not a ledger but a list of what is still unpaid:
    # one row per open invoice, its original value and what remains. Compared
    # with every bill and payment we have ever posted, every settled bill and
    # every payment becomes "missing in vendor" — Humantra's eight open
    # invoices produced 28 such rows and a 474,056.20 "difference". The right
    # comparison is balance against balance: what they say is unpaid against
    # what our books say is unpaid. Payments are then not compared at all —
    # they are already inside both balances.
    open_mode = any(r.get('openAmount') is not None for r in vendor_rows)
    open_mode_unavailable = False
    paid_history = []
    v_dates = sorted(r['dateISO'] for r in vendor_rows if r.get('dateISO'))
    stmt_end = vendor_statement_date or (v_dates[-1] if v_dates else None)
    if open_mode:
        docs = [r for r in zoho_rows if not _is_payment(r)]
        if docs and all(r.get('balance') is not None for r in docs):
            vendor_rows = [dict(r, originalAmount=r['amount'],
                                amount=r['openAmount']) for r in vendor_rows]
            conv = []
            for r in zoho_rows:
                if _is_payment(r):
                    paid_history.append(r)
                    continue
                try:
                    amt = float(r['amount'])
                    bal = abs(float(r['balance']))
                except (TypeError, ValueError, KeyError):
                    conv.append(r)
                    continue
                conv.append(dict(r, originalAmount=amt,
                                 amount=-bal if amt < 0 else bal))
            zoho_rows = conv
        else:
            # Without our balances there is nothing to compare an
            # outstanding figure with. Fall back to document amounts, and say so.
            open_mode = False
            open_mode_unavailable = True
            vendor_rows = [dict(r) for r in vendor_rows]

    def _pairable(a, b):
        """Could these two rows be the same transaction? Generous and relative,
        so bank charges and rounding do not split one transaction into two
        exceptions. Whether the amounts actually agree is judged separately."""
        if a is None or b is None:
            return False
        if (a >= 0) != (b >= 0):
            return False
        return abs(abs(a) - abs(b)) <= max(tolerance,
                                           pair_tolerance_pct * max(abs(a), abs(b)))
    def _fold_payments(rows):
        """Sum payment rows that share a reference, exactly as the value lane
        already does for everything else.

        SAP posts one payment as several clearing rows under one document
        number: KWA-VP-6428 of 125,247.00 arrives as 1400013121 twice,
        -46,046.34 and -79,201.14. Matched row by row neither half can ever
        find its counterpart, and both land in the exception list. Summed, they
        are the payment they always were. Synthetic references are left alone —
        they are not identities and must never be pooled."""
        out, byref = [], {}
        for r in rows:
            if not (_is_payment(r) and r.get('amount') is not None):
                continue
            row = dict(r, amount=round(float(r['amount']), 2))
            ref = str(row.get('ref') or '')
            if not ref or _is_synthetic(ref):
                out.append(row)
                continue
            seen = byref.get(ref)
            if seen is None:
                row['lines'] = 1
                byref[ref] = row
                out.append(row)
                continue
            seen['amount'] = round(seen['amount'] + row['amount'], 2)
            seen['lines'] = seen.get('lines', 1) + 1
            # keep the earliest date: the payment was made once
            if row.get('dateISO') and (not seen.get('dateISO')
                                       or row['dateISO'] < seen['dateISO']):
                seen['dateISO'] = row['dateISO']
                seen['date'] = row.get('date') or seen.get('date')
        return out

    v_pay = _fold_payments(vendor_rows)
    z_pay = _fold_payments(zoho_rows)
    folded_v = sum(r.get('lines', 1) - 1 for r in v_pay)
    folded_z = sum(r.get('lines', 1) - 1 for r in z_pay)
    vendor_rows = [r for r in vendor_rows if not _is_payment(r)]
    zoho_rows = [r for r in zoho_rows if not _is_payment(r)]
    unusable = []
    vm = _aggregate(vendor_rows, 'vendor', unusable)
    zm = _aggregate(zoho_rows, 'zoho', unusable)
    results, pairs = [], []          # pairs: (vref, zref, tier, note)
    v_open = set(vm.keys())
    z_open = set(zm.keys())
    # references the extractor invented — value-matchable, never ref-matchable
    v_syn = {r for r in v_open if _is_synthetic(r)}
    z_syn = {r for r in z_open if _is_synthetic(r)}

    def pair(vref, zref, tier, note=''):
        pairs.append((vref, zref, tier, note))
        v_open.discard(vref)
        z_open.discard(zref)

    # tier 1 — exact normalized ref
    for vref in sorted(v_open - v_syn):
        if vref in z_open and vref not in z_syn:
            pair(vref, vref, 1)

    # tier 2a — alias overlap. A row often carries several identifiers (a bank
    # reference and a voucher number, a PO number and a credit-note number) and
    # the two sides seldom quote the same one. Any shared identifier is strong
    # evidence, and much safer than the digit-soup forms below.
    if v_open and z_open:
        zbyalias = defaultdict(set)
        for z in z_open - z_syn:
            for a in zm[z].get('aliases', ()):
                zbyalias[a].add(z)
        for vref in sorted(list(v_open - v_syn)):
            hits = set()
            for a in vm[vref].get('aliases', ()):
                hits |= {z for z in zbyalias.get(a, ()) if z in z_open}
            hits = {z for z in hits
                    if (zm[z]['amount'] >= 0) == (vm[vref]['amount'] >= 0)}
            if len(hits) == 1:
                z = hits.pop()
                shared = sorted(vm[vref].get('aliases', set()) & zm[z].get('aliases', set()))
                pair(vref, z, 2, f"ref match (shared identifier {shared[0]})" if shared
                     else 'ref match (shared identifier)')

    # tier 2 — relaxed reference forms
    # A relaxed form must still carry enough information to identify anything.
    # "OLD-1" and "Z-1" both reduce to the digit string "1", and matching on
    # that is not a relaxed reference — it is a coincidence. Four characters is
    # the shortest form that distinguishes real document numbers.
    MIN_RELAXED_KEY = 4

    def _index(refs, fn):
        idx = defaultdict(list)
        for r in refs:
            k = fn(r)
            if k and len(str(k)) >= MIN_RELAXED_KEY:
                idx[k].append(r)
        return idx
    for fn, label in ((_alnum, 'alnum'), (_zstrip, 'zero-stripped'), (_digits, 'digits-only')):
        if not v_open or not z_open:
            break
        zidx = _index(z_open - z_syn, fn)
        for vref in sorted(list(v_open - v_syn)):
            vkey = fn(vref)
            if not vkey or len(str(vkey)) < MIN_RELAXED_KEY:
                continue
            cands = zidx.get(vkey, [])
            cands = [c for c in cands if c in z_open]
            # A relaxed reference match must also agree in direction. Digit-soup
            # forms collide easily across document families (MH/EXC/2/25-26 and
            # M/002/25-26 both reduce to 22526), and without this a credit note
            # pairs with a bill and is reported as a confident amount difference.
            cands = [c for c in cands
                     if (zm[c]['amount'] >= 0) == (vm[vref]['amount'] >= 0)]
            if len(cands) == 1:
                pfx = _prefix_of(vref, cands[0])
                pair(vref, cands[0], 2, f'ref match (prefix {pfx})' if pfx
                     else f'ref match ({label})')

    # tier 2b — a prefix one side adds to every number. Once several pairs
    # agree on it (AEB on ours, nothing on theirs), it is a house style, not
    # a coincidence, and it holds for the short numbers too — "AEB999" and
    # "#999" are too short for the digit forms above to be trusted alone.
    learned = Counter(_prefix_of(v, z) for v, z, t, _ in pairs if t == 2)
    learned.pop('', None)
    for pfx, n in learned.most_common(2):
        if n < 3:
            break
        zidx = {_alnum(z): z for z in z_open - z_syn}
        for vref in sorted(list(v_open - v_syn)):
            a = _alnum(vref)
            for cand in (pfx + a, a[len(pfx):] if a.startswith(pfx) else None):
                z = zidx.get(cand) if cand else None
                if z and z in z_open and (zm[z]['amount'] >= 0) == (vm[vref]['amount'] >= 0):
                    pair(vref, z, 2, f'ref match (prefix {pfx}, seen on {n} other pairs)')
                    break
    # single-typo refs, only when amounts also agree within tolerance
    for vref in sorted(list(v_open - v_syn)):
        va = _alnum(vref)
        hits = [z for z in z_open - z_syn if _one_typo(va, _alnum(z))
                and abs(vm[vref]['amount'] - zm[z]['amount']) <= tolerance]
        if len(hits) == 1:
            pair(vref, hits[0], 2, 'ref match (1-char difference)')

    # ── currency check, on the evidence of the rows matched by reference ──
    # Statements rarely repeat their currency next to every figure, so it is
    # inferred: same-currency books cluster at a ratio of 1.0, an INR ledger
    # against AED books clusters around 24.
    fx_rate, fx_ratios = _detect_scale(pairs, vm, zm)
    pay_pairs = _match_payment_lane(v_pay, z_pay, tolerance, date_window,
                                    rate=fx_rate, rate_tol=rate_tolerance,
                                    pairable=_pairable)

    # tier 3 — value matching for the leftovers
    def _amount_key(x):
        return round(abs(x['amount']), 2)
    z_by_amt = defaultdict(list)
    for z in z_open:
        z_by_amt[_amount_key(zm[z])].append(z)
    for vref in sorted(list(v_open), key=lambda r: -abs(vm[r]['amount'])):
        v = vm[vref]
        if fx_rate:
            # different currencies: pair on the ratio, not the difference
            if not v['amount']:
                continue
            cands = [z for z in z_open if zm[z]['amount']
                     and (zm[z]['amount'] >= 0) == (v['amount'] >= 0)
                     and abs(abs(v['amount']) / abs(zm[z]['amount']) / fx_rate - 1)
                     <= rate_tolerance]
            hit_note = f'rate match (within {rate_tolerance:.0%} of {fx_rate:,.2f})'
        else:
            cands = [z for z in z_by_amt.get(_amount_key(v), []) if z in z_open]
            exact = bool(cands)
            if not cands:
                cands = [z for z in z_open if _pairable(v['amount'], zm[z]['amount'])]
                # A gap of tens of dirhams is not itself evidence that two rows
                # are the same document. Beyond PAIR_ABS_NEEDS_DATE the dates
                # must agree too, or the pair is refused.
                vd0 = _day(v['date'])
                cands = [z for z in cands
                         if abs(abs(v['amount']) - abs(zm[z]['amount'])) <= PAIR_ABS_NEEDS_DATE
                         or (vd0 and _day(zm[z]['date'])
                             and abs(_day(zm[z]['date']) - vd0) <= date_window)]
            same_sign = [z for z in cands if (zm[z]['amount'] >= 0) == (v['amount'] >= 0)]
            cands = same_sign or cands
            hit_note = ('amount match (unique value)' if exact
                        else f'near-amount match (within {pair_tolerance_pct:.1%})')
        if not cands:
            continue
        if len(cands) == 1:
            pair(vref, cands[0], 3, hit_note)
            continue
        vd = _day(v['date'])
        dated = [z for z in cands if vd and _day(zm[z]['date'])
                 and abs(_day(zm[z]['date']) - vd) <= date_window]
        if len(dated) == 1:
            pair(vref, dated[0], 3,
                 (hit_note + f' + date (±{date_window}d)') if fx_rate
                 else f'amount+date match (±{date_window}d)')

    # tier 4 — combination sums (2-3 leftover lines equal one line opposite)
    # combination sums are meaningless across two currencies
    if enable_combos and not fx_rate and v_open and z_open \
            and len(v_open) + len(z_open) <= 80:
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
    def _label(ref, entry):
        """Never show a synthetic key to the user — fall back to whatever raw
        text the row had, or say plainly that there was no reference."""
        if not _is_synthetic(ref):
            return ref
        raw = str(entry.get('refRaw') or '').strip()
        return raw if raw and not _is_synthetic(raw) else '(no reference)'

    def _agree(vamt, zamt):
        """Do the two sides agree? Same currency: the difference is within
        tolerance. Different currencies: the rate this row implies is within
        rate_tolerance of the rate the statement as a whole implies — an
        outlier there is a real discrepancy, ordinary FX drift is not."""
        if not fx_rate:
            return abs(round(vamt - zamt, 2)) <= tolerance, None
        if not vamt or not zamt or (vamt >= 0) != (zamt >= 0):
            return False, None
        implied = abs(vamt) / abs(zamt)
        return abs(implied / fx_rate - 1) <= rate_tolerance, round(implied, 4)

    def _zoho_flags(z, row):
        """What our own books say about this entry beyond its amount."""
        n = len(z.get('docIds') or ())
        if n > 1:
            row['zohoDuplicate'] = n
            each = (z.get('orig') or z['amount']) / n
            row['note'] = (row['note'] + ' · ' if row['note'] else '') + (
                f'BOOKED {n} TIMES in our books under the same number '
                f'({n} separate bills, about {each:,.2f} each) — one is a duplicate')
        sts = z.get('statuses') or []
        if any(s in ('pending_approval', 'draft') for s in sts):
            row['zohoNotApproved'] = True
            row['note'] = (row['note'] + ' · ' if row['note'] else '') + \
                'not yet approved in Zoho (pending approval / draft)'
        return row

    matched = amount_diff = 0
    matched_resid = 0.0
    for vref, zref, tier, note in pairs:
        v, z = vm[vref], zm[zref]
        diff = round(v['amount'] - z['amount'], 2)
        ok, implied = _agree(v['amount'], z['amount'])
        if ok:
            matched += 1
            matched_resid += diff
        else:
            amount_diff += 1
        vlab, zlab = _label(vref, v), _label(zref, z)
        if _is_synthetic(vref) or _is_synthetic(zref):
            note = (note + ' · ' if note else '') + 'one side had no readable reference'
        if fx_rate and implied:
            dev = implied / fx_rate - 1
            note = (note + ' · ' if note else '') + (
                f'implied rate {implied:,.2f}' if ok else
                f'implied rate {implied:,.2f} is {dev:+.1%} off the statement rate '
                f'{fx_rate:,.2f} — investigate')
        results.append({
            'ref': vlab if vlab == zlab else f'{vlab} = {zlab}',
            'vendorRef': vlab, 'zohoRef': zlab, 'lane': 'reference',
            'refRaw': v['refRaw'], 'date': v['dateRaw'] or z['dateRaw'],
            'dateISO': v['date'] or z['date'],
            'vendorDateISO': v['date'], 'zohoDateISO': z['date'],
            'type': (v['types'] or z['types'] or ['Invoice'])[0],
            'vendorAmt': v['amount'], 'zohoAmt': z['amount'],
            'impliedRate': implied,
            # across two currencies a subtraction is not a difference
            'diff': None if fx_rate else (diff if not ok else 0.0),
            'status': 'MATCHED' if ok else 'AMOUNT_DIFF',
            'tier': tier, 'note': note,
            'vendorOriginal': v.get('orig') if open_mode else None,
            'zohoOriginal': z.get('orig') if open_mode else None,
        })
        _zoho_flags(z, results[-1])
    for f in combo_findings:
        results.append({'ref': f['ref'], 'refRaw': f['ref'], 'date': '', 'dateISO': None,
                        'type': 'Combination', 'vendorAmt': None, 'zohoAmt': None,
                        'diff': 0.0, 'status': 'MATCHED', 'tier': 4, 'note': f['note']})
        matched += 1
        matched_resid += f['resid'] if f['direction'].startswith('vendor line') else -f['resid']
    for vref in sorted(v_open):
        v = vm[vref]
        note = 'missing in our books'
        if _is_synthetic(vref):
            note += ' · no readable reference on the statement'
        results.append({'ref': _label(vref, v), 'vendorRef': _label(vref, v),
                        'zohoRef': None, 'lane': 'reference',
                        'refRaw': v['refRaw'], 'date': v['dateRaw'],
                        'dateISO': v['date'], 'type': (v['types'] or ['Invoice'])[0],
                        'vendorAmt': v['amount'], 'zohoAmt': None, 'diff': None,
                        'status': 'EXTRA_IN_VENDOR', 'tier': 0, 'note': note})
    for zref in sorted(z_open):
        z = zm[zref]
        note = 'only in our books'
        if _is_synthetic(zref):
            note += ' · no readable reference on the statement'
        results.append({'ref': _label(zref, z), 'vendorRef': None,
                        'zohoRef': _label(zref, z), 'lane': 'reference',
                        'refRaw': z['refRaw'], 'date': z['dateRaw'],
                        'dateISO': z['date'], 'type': (z['types'] or ['Invoice'])[0],
                        'vendorAmt': None, 'zohoAmt': z['amount'], 'diff': None,
                        'status': 'MISSING_IN_VENDOR', 'tier': 0, 'note': note,
                        'zohoOriginal': z.get('orig') if open_mode else None})
        _zoho_flags(z, results[-1])

    # payment lane results
    v_used = {p[0] for p in pay_pairs}
    z_used = {p[1] for p in pay_pairs}
    for i, j, tier, note in pay_pairs:
        v, z = v_pay[i], z_pay[j]
        d = round(v['amount'] - z['amount'], 2)
        ok, implied = _agree(v['amount'], z['amount'])
        if ok:
            matched += 1
            matched_resid += d
        else:
            amount_diff += 1
        if fx_rate and implied:
            dev = implied / fx_rate - 1
            note = (note + ' · ' if note else '') + (
                f'implied rate {implied:,.2f}' if ok else
                f'implied rate {implied:,.2f} is {dev:+.1%} off the statement rate '
                f'{fx_rate:,.2f} — investigate')
        ref = v['ref'] if v['ref'] == z['ref'] else f"{v['ref']} = {z['ref']}"
        results.append({'ref': ref, 'vendorRef': v['ref'], 'zohoRef': z['ref'],
                        'lane': 'payment', 'refRaw': v.get('refRaw') or v['ref'],
                        'date': v.get('date') or z.get('date') or '',
                        'dateISO': v.get('dateISO') or z.get('dateISO'),
                        'vendorDateISO': v.get('dateISO'), 'zohoDateISO': z.get('dateISO'),
                        'type': 'Payment', 'vendorAmt': v['amount'], 'zohoAmt': z['amount'],
                        'impliedRate': implied,
                        'diff': None if fx_rate else (d if not ok else 0.0),
                        'status': 'MATCHED' if ok else 'AMOUNT_DIFF',
                        'tier': tier, 'note': note})
    for i, v in enumerate(v_pay):
        if i not in v_used:
            results.append({'ref': v['ref'], 'vendorRef': v['ref'], 'zohoRef': None,
                            'lane': 'payment', 'refRaw': v.get('refRaw') or v['ref'],
                            'date': v.get('date') or '', 'dateISO': v.get('dateISO'),
                            'type': 'Payment', 'vendorAmt': v['amount'], 'zohoAmt': None,
                            'diff': None, 'status': 'EXTRA_IN_VENDOR', 'tier': 0,
                            'note': 'payment on vendor SOA not found in our books'})
    for j, z in enumerate(z_pay):
        if j not in z_used:
            results.append({'ref': z['ref'], 'vendorRef': None, 'zohoRef': z['ref'],
                            'lane': 'payment', 'refRaw': z.get('refRaw') or z['ref'],
                            'date': z.get('date') or '', 'dateISO': z.get('dateISO'),
                            'type': 'Payment', 'vendorAmt': None, 'zohoAmt': z['amount'],
                            'diff': None, 'status': 'MISSING_IN_VENDOR', 'tier': 0,
                            'note': 'our payment not reflected on vendor SOA'})

    # nearest unmatched counterpart — when the engine cannot justify a pair, it
    # still says which row on the other side is closest, so a human closes the
    # loop in one glance instead of scanning two lists.
    open_v = [r for r in results if r['status'] == 'EXTRA_IN_VENDOR' and r['vendorAmt']]
    open_z = [r for r in results if r['status'] == 'MISSING_IN_VENDOR' and r['zohoAmt']]
    for a in open_v:
        best, gap = None, None
        for b in open_z:
            if (a['vendorAmt'] >= 0) != (b['zohoAmt'] >= 0):
                continue
            d = abs(abs(a['vendorAmt']) - abs(b['zohoAmt']))
            rel = d / max(abs(a['vendorAmt']), abs(b['zohoAmt']), 1)
            if rel <= NEAREST_HINT_PCT and (gap is None or rel < gap):
                best, gap = b, rel
        if best is not None:
            a['nearest'] = best['ref']
            a['note'] = (a['note'] + ' · ' if a['note'] else '') + \
                f"closest unmatched row in our books is {best['ref']} " \
                f"({best['zohoAmt']:,.2f}), {gap:.1%} apart — check before writing this off"
            best['nearest'] = a['ref']
            best['note'] = (best['note'] + ' · ' if best['note'] else '') + \
                f"closest unmatched row on the vendor statement is {a['ref']} " \
                f"({a['vendorAmt']:,.2f}), {gap:.1%} apart — check before writing this off"

    # same-side contra detection (net-zero clusters) — annotate, do not match
    # Each row may be claimed by ONE contra. Without this a single large
    # posting pairs with every equal-and-opposite row in the ledger and the
    # findings fill with the same amount stated three ways, which reads as
    # three problems instead of one ambiguity.
    contra_notes = []
    claimed = set()
    open_v_rows = [r for r in results if r['status'] == 'EXTRA_IN_VENDOR']
    for a, b in itertools.combinations(open_v_rows, 2):
        if id(a) in claimed or id(b) in claimed:
            continue
        va, vb = a.get('vendorAmt'), b.get('vendorAmt')
        if va is not None and vb is not None and abs(round(va + vb, 2)) <= tolerance and va != 0:
            claimed.add(id(a))
            claimed.add(id(b))
            # Flagged on the row so the UI can lift them out of the exception
            # count. A reversal pair is the vendor's own bookkeeping — it nets
            # to zero and there is nothing for us to have booked. Counting them
            # as exceptions is how 66 "missing" items turn out to be 40.
            a['contraPaired'] = True
            b['contraPaired'] = True
            note = f"possible vendor-side contra: {a['ref']} ({va}) offsets {b['ref']} ({vb})"
            a['note'] = (a['note'] + ' · ' if a['note'] else '') + 'possible contra with ' + b['ref']
            b['note'] = (b['note'] + ' · ' if b['note'] else '') + 'possible contra with ' + a['ref']
            contra_notes.append(note)
    if len(contra_notes) > 8:
        kept = contra_notes[:8]
        kept.append(f'... and {len(contra_notes) - 8} further contra pairs '
                    f'(a ledger with this many self-cancelling postings is '
                    f'usually carrying reversals, not discrepancies)')
        contra_notes = kept

    # ── contras leave the exception list entirely ────────────────────────
    # A reversal pair is the vendor's own bookkeeping: booked, then unbooked,
    # netting to zero. There was never anything for us to record, so it is not
    # a missing item and must not sit in the same list as one. It gets its own
    # status — still present, still counted, still adding up, but out of the
    # way of the rows a human has to act on.
    # The comparable window: where the two documents actually overlap.
    def _span(rows):
        ds = sorted(d for d in (r.get('dateISO') for r in rows) if d)
        return (ds[0], ds[-1]) if ds else (None, None)

    v_lo, v_hi = _span(vendor_rows + v_pay)
    z_lo, z_hi = _span(zoho_rows + z_pay)
    window = [max(v_lo, z_lo), min(v_hi, z_hi)] if (v_lo and z_lo) else [None, None]
    # Two documents that do not overlap at all produce an inverted window
    # (start after end). There is no comparable period then, and treating every
    # row as "outside" it would empty the exception list entirely.
    overlaps = bool(window[0] and window[1] and window[0] <= window[1])

    n_contra = 0
    for r in results:
        if r.get('contraPaired') and r['status'] == 'EXTRA_IN_VENDOR':
            r['status'] = 'CONTRA_PAIRED'
            n_contra += 1

    # ── probable duplicates on our side ──────────────────────────────────
    # Two bills, same value, same few days, numbers one character apart:
    # AE82439 and AEB2439 at 20,580.00 each, one paid and one still open.
    # That is one invoice keyed twice, and the open copy overstates what we
    # owe by its full value.
    zres = {r['zohoRef']: r for r in results if r.get('zohoRef')}
    zkeys = [k for k in zm if not _is_synthetic(k) and k in zres]
    for a, b in itertools.combinations(zkeys, 2):
        ea, eb = zm[a], zm[b]
        if not _one_typo(_alnum(a), _alnum(b)):
            continue
        if abs((ea.get('orig') or ea['amount']) - (eb.get('orig') or eb['amount'])) > tolerance:
            continue
        da, db = _day(ea.get('date')), _day(eb.get('date'))
        if da is None or db is None or abs(da - db) > 7:
            continue
        for x, y in ((a, b), (b, a)):
            rx = zres[x]
            rx['probableDuplicateOf'] = y
            rx['note'] = (rx['note'] + ' · ' if rx['note'] else '') + (
                f'probable duplicate of {y} in our books — same value '
                f'{(zm[y].get("orig") or zm[y]["amount"]):,.2f}, same week, '
                f'reference one character different')

    n_settled = n_after_stmt = 0
    paid_after = []
    if open_mode:
        for r in results:
            if r['status'] != 'MISSING_IN_VENDOR':
                continue
            d = r.get('dateISO')
            if stmt_end and d and d > stmt_end:
                r['status'] = 'AFTER_STATEMENT'
                n_after_stmt += 1
                rest = r['note'].replace('only in our books', '').strip(' ·')
                r['note'] = (f'dated after the statement ({stmt_end}) — it cannot '
                             f'appear on it yet' + (' · ' + rest if rest else ''))
            elif (r.get('zohoAmt') or 0) == 0:
                r['status'] = 'SETTLED'
                n_settled += 1
                orig = r.get('zohoOriginal')
                rest = r['note'].replace('only in our books', '').strip(' ·')
                r['note'] = ('fully settled in our books'
                             + (f' (raised for {orig:,.2f})' if orig else '')
                             + ' — an open-items statement does not list paid invoices'
                             + (' · ' + rest if rest else ''))
        for r in results:
            if r['status'] == 'MISSING_IN_VENDOR' and r['type'] != 'Payment':
                r['note'] = r['note'].replace(
                    'only in our books',
                    'unpaid in our books but not on the vendor\'s list of open items')
        # Payments stay on the page — every row we were given is accounted
        # for — but carry no balance: on an open-items statement a payment is
        # already inside both sides' outstanding figures.
        for p in paid_history:
            try:
                pa = round(float(p['amount']), 2)
            except (TypeError, ValueError):
                pa = None
            ref = str(p.get('ref') or p.get('docNo') or '').strip() or '(payment)'
            results.append({
                'ref': ref, 'vendorRef': None, 'zohoRef': ref, 'lane': 'payment',
                'refRaw': ref, 'date': p.get('date') or '', 'dateISO': p.get('dateISO'),
                'type': 'Payment', 'vendorAmt': None, 'zohoAmt': None, 'diff': None,
                'status': 'SETTLED', 'tier': 0, 'zohoOriginal': pa,
                'note': (f'our payment {p.get("docNo") or ""} of '
                         f'{abs(pa or 0):,.2f} — already inside both outstanding '
                         f'balances, not compared line by line').replace('  ', ' ')})
            n_settled += 1
        # A difference the vendor shows as unpaid that our books show as paid,
        # where we paid exactly that amount after the statement was drawn up,
        # is not a disagreement: the statement is simply older than the payment.
        used = set()
        later = [p for p in paid_history
                 if p.get('dateISO') and stmt_end and p['dateISO'] > stmt_end]
        for r in results:
            if r['status'] != 'AMOUNT_DIFF' or r.get('lane') != 'reference':
                continue
            gap = round((r['vendorAmt'] or 0) - (r['zohoAmt'] or 0), 2)
            if gap <= tolerance:
                continue
            for i, p in enumerate(later):
                if i in used:
                    continue
                try:
                    pa = abs(float(p['amount']))
                except (TypeError, ValueError):
                    continue
                if abs(pa - gap) <= tolerance:
                    used.add(i)
                    pref = p.get('docNo') or p.get('ref') or '(payment)'
                    r['paidAfterStatement'] = {'ref': pref, 'date': p['dateISO'],
                                               'amount': round(pa, 2)}
                    r['note'] = (r['note'] + ' · ' if r['note'] else '') + (
                        f'paid after the statement date: {pref} for {pa:,.2f} on '
                        f'{p["dateISO"]} — timing, not a disagreement')
                    paid_after.append(r)
                    break

    # ── rows from before the comparable period leave the exception list ──
    # You cannot be missing something from before the period you are
    # reconciling. Honasa's ledger opens 29 Feb 2024, our statement 16 Sep
    # 2024: 18 of 41 "missing" rows simply predate the document they are being
    # compared against. They stay visible in their own section, but they are
    # not open items and must not be counted as such.
    #
    # Rows dated AFTER our statement ends are the opposite — those are charges
    # the other side has raised and we have not booked, which is usually the
    # whole point of the exercise. They stay exceptions, and are flagged.
    n_before = 0
    # An open-items statement has no period to be outside of: an invoice from
    # three years ago that is still unpaid belongs on it as much as last week's.
    if overlaps and not open_mode:
        for r in results:
            if r['status'] not in ('EXTRA_IN_VENDOR', 'MISSING_IN_VENDOR'):
                continue
            d = r.get('dateISO')
            if d and d < window[0]:
                r['status'] = 'BEFORE_PERIOD'
                n_before += 1
            elif d and window[1] and d > window[1]:
                r['afterPeriod'] = True

    order = {'AMOUNT_DIFF': 0, 'EXTRA_IN_VENDOR': 1, 'MISSING_IN_VENDOR': 2,
             'CONTRA_PAIRED': 3, 'BEFORE_PERIOD': 4, 'AFTER_STATEMENT': 5,
             'MATCHED': 6, 'SETTLED': 7}
    results.sort(key=lambda r: (order.get(r['status'], 9), -(abs(r['diff'] or r['vendorAmt'] or r['zohoAmt'] or 0))))

    vendor_net = round(sum(v['amount'] for v in vm.values()) + sum(p['amount'] for p in v_pay), 2)
    zoho_net = round(sum(z['amount'] for z in zm.values()) + sum(p['amount'] for p in z_pay), 2)
    n_extra = sum(1 for r in results if r['status'] == 'EXTRA_IN_VENDOR')
    n_missing = sum(1 for r in results if r['status'] == 'MISSING_IN_VENDOR')
    extra_val = round(sum(r['vendorAmt'] or 0 for r in results if r['status'] == 'EXTRA_IN_VENDOR'), 2)
    missing_val = round(sum(r['zohoAmt'] or 0 for r in results if r['status'] == 'MISSING_IN_VENDOR'), 2)
    # Pairs cancel to within tolerance, not to exactly zero, so their residue
    # is carried into the arithmetic rather than dropped. Removing rows from a
    # reconciliation without accounting for their value is how a total stops
    # tying.
    contra_val = round(sum(r['vendorAmt'] or 0 for r in results
                           if r['status'] == 'CONTRA_PAIRED'), 2)
    before_val = round(sum((r['vendorAmt'] or 0) - (r['zohoAmt'] or 0)
                           for r in results if r['status'] == 'BEFORE_PERIOD'), 2)
    after_stmt_val = round(sum(r['zohoAmt'] or 0 for r in results
                               if r['status'] == 'AFTER_STATEMENT'), 2)
    # An open-items statement is a balance struck on one day. Our bills dated
    # after that day are out of the comparison altogether — left in, they
    # would make the headline difference a figure nobody can act on.
    zoho_net = round(zoho_net - after_stmt_val, 2)
    n_after = sum(1 for r in results if r.get('afterPeriod'))
    after_val = round(sum(r['vendorAmt'] or 0 for r in results
                          if r.get('afterPeriod')), 2)
    diff_val = round(sum(r['vendorAmt'] - r['zohoAmt'] for r in results
                         if r['status'] == 'AMOUNT_DIFF' and r['vendorAmt'] is not None
                         and r['zohoAmt'] is not None), 2)

    n_syn = len(v_syn) + len(z_syn)
    syn_notes = []
    if folded_v or folded_z:
        syn_notes.append(
            f'{folded_v + folded_z} payment row(s) were clearing splits — one '
            f'payment posted across several lines under a single document '
            f'number — and were summed back into the payment they belong to '
            f'before matching.')
    # A statement with no stated closing balance offers nothing to check the
    # extraction against. Neither side can prove it read the document whole,
    # so every figure below is unverifiable at the bottom line — which is worth
    # saying before anyone acts on one.
    if vendor_states_total is False or zoho_states_total is False:
        who = []
        if vendor_states_total is False:
            who.append('the vendor statement')
        if zoho_states_total is False:
            who.append('our statement')
        syn_notes.insert(0, (
            f'{" and ".join(who).capitalize()} state(s) no closing balance, so '
            f'there is no control total to prove the document was read '
            f'completely. Ask for a statement that carries one before relying '
            f'on the totals below.'))
    if n_before:
        syn_notes.append(
            f'{n_before} vendor row(s) predate our statement (before '
            f'{window[0]}) and have their own section. We cannot be missing an '
            f'item from before the period being reconciled — settle those '
            f'against the prior statement, not this one.')
    if n_after:
        syn_notes.append(
            f'{n_after} row(s) are dated after our statement ends ({window[1]}), '
            f'worth {after_val:,.2f}. These ARE open items: charges the vendor '
            f'has raised that we have not booked.')
    if n_contra:
        syn_notes.append(
            f'{n_contra} vendor row(s) — {n_contra // 2} self-cancelling pair(s) '
            f'— have been moved out of the missing list into their own section. '
            f'They are postings the vendor reversed in its own ledger, netting '
            f'to {contra_val:,.2f}. Nothing was ever ours to record. '
            f'{n_extra} genuine vendor-only row(s) remain, worth '
            f'{extra_val:,.2f}.')
    if fx_rate:
        lo, hi = min(fx_ratios), max(fx_ratios)
        syn_notes.append(
            f'The two statements are in different currencies — their reference-matched '
            f'rows imply about {fx_rate:,.2f} of the vendor\'s currency per unit of ours '
            f'(range {lo:,.2f}–{hi:,.2f} across {len(fx_ratios)} rows). Rows are paired '
            f'by reference and rate, not by amount. "Net difference" below is a raw '
            f'subtraction of two currencies and is NOT a monetary figure — read '
            f'vendorNet and zohoNet separately, each in its own currency.')
    if n_syn:
        syn_val = round(sum(vm[r]['amount'] for r in v_syn)
                        - sum(zm[r]['amount'] for r in z_syn), 2)
        syn_notes.append(
            f'{n_syn} row(s) had no readable reference (net {syn_val}) — '
            f'reconciled by amount and date only, verify before posting')
    if unusable:
        syn_notes.append(
            f'{len(unusable)} row(s) dropped with an unreadable amount: ' +
            ', '.join(f"{u['side']}:{u['ref'] or '(no ref)'}={u['amount']!r}"
                      for u in unusable[:10]))

    # ── open-items headline ──────────────────────────────────────────────
    open_notes = []
    dup_rows = [r for r in results if r.get('zohoDuplicate') or
                (r.get('probableDuplicateOf') and r['status'] in ('AMOUNT_DIFF', 'MISSING_IN_VENDOR'))]
    z_vendors = sorted({str(r.get('vendor')).strip() for r in zoho_rows + paid_history + z_pay
                        if r.get('vendor')})
    if len(z_vendors) > 1:
        open_notes.append(
            f'Our books carry this supplier under {len(z_vendors)} vendor records '
            f'({", ".join(z_vendors[:4])}). Bills split across records are easy to '
            f'pay twice or miss — worth merging.')
        zv = defaultdict(set)
        for r in zoho_rows:
            if r.get('vendor'):
                zv[str(r.get('ref') or '').strip().upper()].add(str(r['vendor']).strip())
        for r in results:
            vs = zv.get(str(r.get('zohoRef') or '').upper())
            if vs and r['status'] not in ('MATCHED', 'SETTLED'):
                r['note'] = (r['note'] + ' · ' if r['note'] else '') + \
                    'booked under ' + ' and '.join(sorted(vs))
    if void_rows:
        open_notes.append(
            f'{len(void_rows)} void document(s) in our books were excluded — '
            f'a voided bill is not owed: ' + ', '.join(
                str(r.get('ref') or r.get('docNo') or '?') for r in void_rows[:6]))
    if open_mode_unavailable:
        open_notes.append(
            'The vendor sent an OPEN-ITEMS statement (outstanding balances only), '
            'but our side arrived without bill balances, so it was compared on '
            'document amounts instead. Every paid bill and every payment will '
            'show as "missing in vendor" — that is a limitation of this run, '
            'not a finding.')
    if open_mode:
        v_open_total = round(sum(v['amount'] for v in vm.values()), 2)
        z_open_total = round(sum(z['amount'] for z in zm.values()) - after_stmt_val, 2)
        open_notes.append(
            f'Open-items statement to {stmt_end}: the vendor shows '
            f'{v_open_total:,.2f} unpaid; our books show {z_open_total:,.2f} unpaid '
            f'on bills dated up to that day — a difference of '
            f'{v_open_total - z_open_total:,.2f}. Paid bills ({n_settled - len(paid_history)}) and '
            f'payments ({len(paid_history)}) are not compared: they are already '
            f'inside both balances.')
        if paid_after:
            t = round(sum(r['paidAfterStatement']['amount'] for r in paid_after), 2)
            open_notes.append(
                f'{len(paid_after)} invoice(s) worth {t:,.2f} were paid after the '
                f'statement date — timing only; they will drop off the next statement.')
        if n_after_stmt:
            open_notes.append(
                f'{n_after_stmt} bill(s) worth {after_stmt_val:,.2f} are dated after '
                f'the statement and cannot appear on it — listed separately, not '
                f'counted as exceptions.')
    if dup_rows:
        open_notes.append(
            f'{len(dup_rows)} bill(s) in our books look like DUPLICATES: ' + '; '.join(
                (f"{r['zohoRef']} booked {r['zohoDuplicate']} times" if r.get('zohoDuplicate')
                 else f"{r['zohoRef']} duplicates {r['probableDuplicateOf']}")
                for r in dup_rows[:6]) + '. Void the extra copy before paying.')
    for n in reversed(open_notes):
        syn_notes.insert(0, n)

    # ── payment lane totals ──────────────────────────────────────────────
    # The two sides often share no payment reference at all — one numbers its
    # receipts, the other its vouchers — so every payment lands unmatched and
    # the reader is left adding a dozen orphan rows by hand. State the
    # aggregate instead: that single number is usually the finding.
    # ── declared currencies ──────────────────────────────────────────────
    # The rate inferred from matched rows says the two sides are on different
    # scales. What each side is actually DENOMINATED in is a separate fact, and
    # a stated one. Holding both lets each check the other: a rate of 3.67 with
    # both sides declaring AED is a mis-parse, not an exchange rate.
    def _declared(rows):
        c = Counter(r.get('currency') for r in rows if r.get('currency'))
        return (c.most_common(1)[0][0] if c else None), dict(c)

    v_ccy, v_ccy_counts = _declared(vendor_rows + v_pay)
    z_ccy, z_ccy_counts = _declared(zoho_rows + z_pay)
    ccy_notes = []
    if v_ccy and z_ccy and v_ccy != z_ccy:
        ccy_notes.append(
            f'The two statements are in different currencies — the vendor\'s is '
            f'in {v_ccy}, ours in {z_ccy}. Every total below mixes them, so '
            f'"net difference" is not a monetary figure: read vendorNet and '
            f'zohoNet separately, each in its own currency.')
    if len(v_ccy_counts) > 1:
        ccy_notes.append('The vendor statement itself mixes currencies: ' +
                         ', '.join(f'{k}x{n}' for k, n in sorted(
                             v_ccy_counts.items(), key=lambda kv: -kv[1])) + '.')
    if len(z_ccy_counts) > 1:
        ccy_notes.append('Our statement mixes currencies: ' +
                         ', '.join(f'{k}x{n}' for k, n in sorted(
                             z_ccy_counts.items(), key=lambda kv: -kv[1])) + '.')

    # ── period overlap ───────────────────────────────────────────────────
    # A vendor ledger reaching back two years against a statement covering
    # eight months produces a hundred "missing in our books" rows that are
    # simply out of period. The engine cannot know the intended window, but it
    # can see the two sides do not describe the same one, and must say so
    # rather than let the reader mistake history for a discrepancy.
    period_notes = []
    after_window = {'count': 0, 'value': 0.0}
    if v_lo and z_lo and not open_mode:
        lo, hi = window[0], window[1]

        # Before and after are not the same finding. Rows dated before the
        # other document begins are history neither side disputes. Rows dated
        # AFTER it ends are documents the other side has not recorded yet —
        # which is usually the whole reason for running the reconciliation.
        def _split(rows, label, other_hi):
            before = [r for r in rows if r.get('dateISO') and r['dateISO'] < lo]
            after = [r for r in rows if r.get('dateISO') and r['dateISO'] > hi]
            if before:
                period_notes.append(
                    f'{len(before)} {label} row(s) predate the other document '
                    f'(before {lo}), net {sum(r["amount"] for r in before):,.2f}. '
                    f'They appear as exceptions below but are outside the '
                    f'comparable period.')
            if after:
                inv = [r for r in after if (r.get('amount') or 0) > 0]
                period_notes.append(
                    f'{len(after)} {label} row(s) are dated after the other '
                    f'document ends ({other_hi}), net '
                    f'{sum(r["amount"] for r in after):,.2f}'
                    + (f' — of which {len(inv)} charge(s) totalling '
                       f'{sum(r["amount"] for r in inv):,.2f} are most likely '
                       f'documents the other side has not recorded yet.'
                       if inv else '.'))
            return after, inv if after else []

        v_after, v_after_inv = _split(vendor_rows + v_pay, 'vendor statement', z_hi)
        _split(zoho_rows + z_pay, 'our own', v_hi)
        after_window = {'count': len(v_after_inv),
                        'value': round(sum(r['amount'] for r in v_after_inv), 2)}
        if period_notes:
            period_notes.insert(0, (
                f'The two documents cover different periods — the vendor '
                f'statement runs {v_lo} to {v_hi}, ours {z_lo} to {z_hi}. '
                f'They can only be compared like with like between {lo} and {hi}.'))

    v_pay_total = round(sum(p['amount'] for p in v_pay), 2)
    z_pay_total = round(sum(p['amount'] for p in z_pay), 2)
    pay_gap = round(v_pay_total - z_pay_total, 2)
    pay_unmatched = len(v_pay) + len(z_pay) - 2 * len(pay_pairs)
    # The declared currencies and the inferred rate must agree. When they do
    # not, one of them is wrong, and saying which is beyond the engine — but
    # saying THAT is not.
    if fx_rate and v_ccy and z_ccy and v_ccy == z_ccy:
        ccy_notes.insert(0, (
            f'CONTRADICTION: both statements declare {v_ccy}, yet the matched '
            f'rows imply a rate of {fx_rate:,.2f} between them. Same-currency '
            f'books cluster at 1.0. Either a statement is mislabelled or a '
            f'column has been read wrongly — do not rely on this '
            f'reconciliation until it is resolved.'))
    elif v_ccy and z_ccy and v_ccy != z_ccy and not fx_rate:
        ccy_notes.insert(0, (
            f'The statements declare different currencies ({v_ccy} vs {z_ccy}) '
            f'but the matched rows imply a rate of about 1.0, which would mean '
            f'the figures are on the same scale. One of the two labels is '
            f'likely wrong.'))

    for n in reversed(period_notes):
        syn_notes.insert(0, n)
    for n in reversed(ccy_notes):
        syn_notes.insert(0, n)
    if pay_unmatched and abs(pay_gap) > tolerance:
        syn_notes.insert(0, (
            f'Payments do not agree: the vendor statement shows {abs(v_pay_total):,.2f} '
            f'across {len(v_pay)} payment(s), our books show {abs(z_pay_total):,.2f} across '
            f'{len(z_pay)} — a gap of {abs(pay_gap):,.2f}. '
            + ('None of the payment references appear on both sides, so none could be '
               'matched individually; the gap is the figure to chase, not the row count.'
               if not pay_pairs else
               f'{len(pay_pairs)} payment(s) matched by reference, {pay_unmatched} did not.')))

    # ── what kind of item is this, and whose move is it? ─────────────────
    # Status says where a row sits. It does not say what anyone should do, and
    # a reviewer reading "41 missing" cannot tell an invoice we must book from
    # a posting internal to the vendor's ledger. These categories answer the
    # only question a reconciliation is really asked: what now, and by whom.
    BANK_CHARGE_ABS = 500.0
    BANK_CHARGE_PCT = 0.005

    def _category(r):
        st = r['status']
        if st in ('CONTRA_PAIRED', 'BEFORE_PERIOD', 'SETTLED', 'AFTER_STATEMENT'):
            return 'NOISE'
        # a bill booked twice is ours to void, whatever else is true of it
        if st in ('AMOUNT_DIFF', 'MISSING_IN_VENDOR') and (
                r.get('zohoDuplicate') or r.get('probableDuplicateOf')):
            return 'DUPLICATE'
        if st == 'MATCHED':
            return 'MATCHED'
        if st == 'AMOUNT_DIFF' and r.get('paidAfterStatement'):
            return 'TIMING'
        if st == 'AMOUNT_DIFF':
            v, z = r.get('vendorAmt') or 0, r.get('zohoAmt') or 0
            gap = abs(v - z)
            if r['type'] == 'Payment' and gap <= max(BANK_CHARGE_ABS,
                                                     BANK_CHARGE_PCT * max(abs(v), abs(z))):
                # a fee deducted in transit, not a disagreement about the debt
                return 'BANK_CHARGE'
            return 'TO_QUERY'
        if st == 'MISSING_IN_VENDOR':
            if open_mode and r['type'] != 'Payment':
                # open in our books, nothing outstanding on theirs: either it
                # sits under another account of theirs, or we owe less than
                # we think
                return 'TO_QUERY'
            # we recorded it, they have not credited it
            return 'WE_SENT'
        # EXTRA_IN_VENDOR
        if r['type'] in ('Invoice', 'Bill', 'Debit Note', 'Credit Note'):
            return 'TO_BOOK'
        # journals and payments we cannot identify are theirs to explain
        return 'VENDOR_EXPLAIN'

    by_category = {}
    for r in results:
        c = _category(r)
        r['category'] = c
        b = by_category.setdefault(c, {'rows': 0, 'value': 0.0})
        b['rows'] += 1
        b['value'] = round(b['value'] + ((r.get('vendorAmt') if r.get('vendorAmt') is not None
                                          else r.get('zohoAmt')) or 0), 2)

    # ── group the results by calendar year ───────────────────────────────
    # Statements routinely span several years, and a 2024 exception left open
    # is a different conversation from one raised last month. Grouping happens
    # HERE, after matching, and never before: reconciling year by year would
    # break every cross-year settlement — an invoice raised in 2024 and
    # cleared in 2026 would become an exception on both sides.
    by_year = {}
    cross_year = 0
    for r in results:
        vd, zd = r.get('vendorDateISO'), r.get('zohoDateISO')
        if vd and zd and vd[:4] != zd[:4]:
            cross_year += 1
            r['crossYear'] = True
        yr = (r.get('dateISO') or '')[:4] or 'undated'
        r['year'] = yr
        b = by_year.setdefault(yr, {
            'matched': 0, 'amountDiff': 0, 'extraInVendor': 0,
            'missingInVendor': 0, 'vendorTotal': 0.0, 'zohoTotal': 0.0,
            'rows': 0, 'crossYear': 0})
        b['rows'] += 1
        key = {'MATCHED': 'matched', 'AMOUNT_DIFF': 'amountDiff',
               'EXTRA_IN_VENDOR': 'extraInVendor',
               'MISSING_IN_VENDOR': 'missingInVendor'}.get(r['status'])
        if key:
            b[key] += 1
        b['vendorTotal'] = round(b['vendorTotal'] + (r.get('vendorAmt') or 0), 2)
        b['zohoTotal'] = round(b['zohoTotal'] + (r.get('zohoAmt') or 0), 2)
        if r.get('crossYear'):
            b['crossYear'] += 1
    for b in by_year.values():
        b['netDifference'] = round(b['vendorTotal'] - b['zohoTotal'], 2)
    years = sorted(y for y in by_year if y != 'undated')
    if cross_year:
        syn_notes.append(
            f'{cross_year} matched pair(s) span two calendar years — a charge '
            f'raised in one year and settled in another. They are grouped under '
            f'the vendor\'s date and flagged, so a year total can legitimately '
            f'differ from that year\'s own movement.')

    summary = {
        'totalReferences': len(results),
        'byCategory': by_category,
        'byYear': by_year,
        'years': years,
        'multiYear': len(years) > 1,
        'crossYearPairs': cross_year,
        'matched': matched, 'amountDiff': amount_diff,
        'extraInVendor': n_extra, 'missingInVendor': n_missing,
        # Own status, own section — not part of extraInVendor any more.
        'contraPaired': n_contra,
        'contraPairs': n_contra // 2,
        'contraPairedValue': contra_val,
        'controlTotalMissing': (vendor_states_total is False
                                or zoho_states_total is False),
        'beforePeriod': n_before, 'beforePeriodValue': before_val,
        'afterPeriod': n_after, 'afterPeriodValue': after_val,
        'netDifference': round(vendor_net - zoho_net, 2),
        'statementKind': 'open_items' if open_mode else 'ledger',
        'openItemsUnavailable': open_mode_unavailable,
        'statementDate': stmt_end if open_mode else None,
        'settled': n_settled,
        'afterStatement': n_after_stmt, 'afterStatementValue': after_stmt_val,
        # the figure to act on: what each side says is owed on the same day
        'comparableDifference': round(vendor_net - zoho_net, 2),
        'paymentsNotCompared': len(paid_history),
        'paidAfterStatement': len(paid_after),
        'paidAfterStatementValue': round(sum(r['paidAfterStatement']['amount']
                                             for r in paid_after), 2),
        'duplicatesInOurBooks': len(dup_rows),
        'voidExcluded': len(void_rows),
        'vendorNet': vendor_net, 'zohoNet': zoho_net,
        'amountDiffValue': diff_val,
        'extraInVendorValue': extra_val, 'missingInVendorValue': missing_val,
        'matchedByTier': {str(t): sum(1 for p in pairs if p[2] == t) for t in (1, 2, 3)},
        'matchedByTierPayments': {str(t): sum(1 for p in pay_pairs if p[2] == t)
                                  for t in (1, 3)},
        'combosMatched': len(combo_findings),
        'unreferencedRows': n_syn,
        'droppedRows': len(unusable),
        'vendorPaymentTotal': v_pay_total, 'zohoPaymentTotal': z_pay_total,
        'paymentGap': pay_gap, 'paymentsUnmatched': pay_unmatched,
        'paymentSplitLinesFolded': folded_v + folded_z,
        'vendorPeriod': [v_lo, v_hi], 'zohoPeriod': [z_lo, z_hi],
        'comparableWindow': window,
        'periodMismatch': bool(period_notes),
        # charges the vendor raised after our statement ends — the usual
        # headline of a reconciliation like this
        'vendorChargesAfterWindow': after_window['count'],
        'vendorChargesAfterWindowValue': after_window['value'],
        'vendorCurrency': v_ccy, 'zohoCurrency': z_ccy,
        'vendorCurrencyCounts': v_ccy_counts, 'zohoCurrencyCounts': z_ccy_counts,
        'currencyDeclaredMismatch': bool(v_ccy and z_ccy and v_ccy != z_ccy),
        'currencyContradiction': bool(fx_rate and v_ccy and z_ccy and v_ccy == z_ccy),
        'currencyMismatch': bool(fx_rate),
        'netDifferenceMeaningful': not fx_rate,
        'impliedRate': round(fx_rate, 4) if fx_rate else None,
        'impliedRateRange': ([round(min(fx_ratios), 4), round(max(fx_ratios), 4)]
                             if fx_rate else None),
        'rateTolerance': rate_tolerance if fx_rate else None,
        'tolerance': tolerance,
        'matchedResidual': round(matched_resid, 2),
        'findings': syn_notes[:1] + [f['note'] for f in combo_findings] + contra_notes + syn_notes[1:],
    }

    # ── invariants: fail loudly, never silently wrong ───────────────────
    assert matched + amount_diff + n_extra + n_missing + n_contra + n_before \
        + n_settled + n_after_stmt == len(results), 'row count invariant'
    # settled rows carry a zero balance, so they add nothing; rows dated after
    # the statement carry ours, and are their own component
    assert all((r.get('zohoAmt') or 0) == 0 for r in results if r['status'] == 'SETTLED'), \
        'settled rows must carry no balance'
    recon_gap = round(diff_val + round(matched_resid, 2) + extra_val
                      + contra_val + before_val - missing_val, 2)
    assert abs(recon_gap - summary['netDifference']) <= 0.02, \
        f"value invariant: {recon_gap} != {summary['netDifference']}"
    summary['invariantsOk'] = True
    return {'summary': summary, 'results': results}
