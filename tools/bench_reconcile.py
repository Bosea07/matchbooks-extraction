"""Benchmark and equivalence harness for lib/reconcile.py.

    py -B tools/bench_reconcile.py                 # time the engine on synthetic statements of several sizes
    py -B tools/bench_reconcile.py --dump out.json # also write each result, to diff two versions of the engine

Synthetic data mimics real statements: invoices with references (some re-typed: prefixes, zero padding,
typos), payments without matching references, credit notes, lines missing on either side, amount
differences and a few split payments, with fixed seeds so two runs see identical input."""
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.reconcile import reconcile  # noqa: E402


def make(n, seed):
    rnd = random.Random(seed)
    vendor, zoho = [], []
    day = lambda: f"2026-{rnd.randint(1, 10):02d}-{rnd.randint(1, 28):02d}"
    for i in range(n):
        amt = round(rnd.uniform(50, 60000), 2)
        d = day()
        ref = f"INV-{100000 + i}"
        kind = rnd.random()
        if kind < 0.70:                        # same document both sides, reference re-typed sometimes
            zref = rnd.choice([ref, ref.replace("INV-", ""), ref.replace("INV-", "INV"), "INV-0" + ref[4:]])
            vendor.append({"ref": ref, "amount": amt, "dateISO": d, "type": "Invoice"})
            zoho.append({"ref": zref, "amount": amt, "dateISO": d, "type": "Bill"})
        elif kind < 0.78:                      # amount differs
            vendor.append({"ref": ref, "amount": amt, "dateISO": d, "type": "Invoice"})
            zoho.append({"ref": ref, "amount": round(amt + rnd.choice([-1, 1]) * rnd.uniform(1, 400), 2), "dateISO": d, "type": "Bill"})
        elif kind < 0.84:                      # missing in our books
            vendor.append({"ref": ref, "amount": amt, "dateISO": d, "type": "Invoice"})
        elif kind < 0.88:                      # missing on their statement
            zoho.append({"ref": ref, "amount": amt, "dateISO": d, "type": "Bill"})
        elif kind < 0.97:                      # payment: different references on each side, same amount
            vendor.append({"ref": f"RCPT{rnd.randint(1, 10**7)}", "amount": -amt, "dateISO": d, "type": "Payment"})
            zoho.append({"ref": f"PAY-{rnd.randint(1, 10**7)}", "amount": -amt, "dateISO": d, "type": "Payment"})
        else:                                  # one vendor line paid in two parts on our side
            a1 = round(amt * 0.4, 2)
            vendor.append({"ref": f"RCPT{rnd.randint(1, 10**7)}", "amount": -amt, "dateISO": d, "type": "Payment"})
            zoho.append({"ref": f"PAY-{rnd.randint(1, 10**7)}", "amount": -a1, "dateISO": d, "type": "Payment"})
            zoho.append({"ref": f"PAY-{rnd.randint(1, 10**7)}", "amount": -round(amt - a1, 2), "dateISO": d, "type": "Payment"})
    rnd.shuffle(vendor)
    rnd.shuffle(zoho)
    return vendor, zoho


def canon(r):
    """Result in a stable form, for comparing two engine versions."""
    return json.loads(json.dumps(r, sort_keys=True, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))


if __name__ == "__main__":
    dump = sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv else None
    out = {}
    for n, seed in [(60, 1), (300, 2), (1000, 3), (3000, 4), (6000, 5)]:
        v, z = make(n, seed)
        t = time.perf_counter()
        r = reconcile(v, z)
        ms = (time.perf_counter() - t) * 1000
        s = r["summary"]
        print(f"{n:>5} docs ({len(v):>5} vendor / {len(z):>5} ours): {ms:9.1f} ms   matched {s.get('matched')} diff {s.get('amountDiff')} "
              f"extra {s.get('extraInVendor')} missing {s.get('missingInVendor')}")
        out[str(n)] = canon(r)
    if dump:
        Path(dump).write_text(json.dumps(out, sort_keys=True), encoding="utf-8")
        print("wrote", dump)
