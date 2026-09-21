# matchbooks-extraction

Vendor-statement reconciliation engine for **MatchBooks**, an internal AP
platform covering six Zoho Books entities (KUWA, SAHA, DMCC, KSA, SHIFA, VWHC).
FastAPI on Railway; the frontend lives separately on Lovable and calls this
service. Owner: Ashutosh Bose, finance — not a full-time engineer, so explain
trade-offs rather than only naming them.

## Running things

No virtualenv in the repo. Python is present as the `py` launcher, **not** as
`python` (that resolves to the Windows Store stub and fails).

```
cd C:\mb\matchbooks-extraction
py -B tests\test_allocation_and_signs.py
py -B tests\test_reference_identity.py
py -B tests\test_currency_and_types.py
py -B tests\test_unreferenced_rows.py
```

Tests are plain scripts with a `__main__` runner, not pytest. **Anything
defined below the `if __name__ == '__main__':` block never runs** — three v2.8
tests sat there dead for weeks. Put new tests above it.

Deploy is `git push` to `main`; Railway builds automatically. Verify with
`/health`, which returns the version string.

## Layout

| File | Role |
|---|---|
| `main.py` | FastAPI app — `/extract`, `/reconcile`, `/export`, `/health` |
| `lib/readers.py` | Format sniffing by content, not extension; CSV/XLSX/XLS/PDF/DOCX/HTML |
| `lib/parser.py` | Grid → transaction records; header detection, refs, signs |
| `lib/normalize.py` | Amount/date parsing, `REF_HINT`, type canonicalisation |
| `lib/reconcile.py` | Tiered matching + invariants |
| `lib/export_xlsx.py` | 4-tab workbook (Summary / Zoho / Vendor SOA / Mapping) |
| `lib/claude_extract.py` | Claude API fallback extractor |

## Design rules that matter here

**Fail loudly, never silently wrong.** A clean-looking reconciliation that is
actually incomplete is the worst output this service can produce — worse than
an error. Invariants assert rather than warn. Rows are never dropped without
being counted and reported.

Specific invariants, each added after a real incident:

- **Row accounting** — `kept + rejected == rows read`. Three payments once
  vanished from a Zoho statement with no diagnostic at all.
- **Sign invariant** — a row typed Bill/Invoice/Debit Note cannot be negative.
  When it is, the amount came from the wrong cell, and the symptom is a
  difference of exactly twice the invoice.
- **Arithmetic invariant** — reported components must sum to the net difference.
- Synthetic refs (`~ROW12`) mark rows kept without a readable reference. They
  reach the amount/date lanes but are excluded from reference matching.

**Matching tiers:** exact ref → shared alias → relaxed ref forms → amount(+date)
→ combination sums. Payments run in a separate lane.

**Allocation text is not a transaction.** Zoho statements carry allocations
inside the payment's Details cell (`AED2,541.00 for payment of …/5019`). Read
by word position these become rows that look real — an amount and a reference —
and invent money with the wrong sign. Folded back in `readers.py`
(`_merge_allocation_fragments`). A row is identified by the reference it *has*,
never by the references it *settles*.

## Current state — v2.12.0

**60 tests passing** across five files. Run `run_tests.cmd` from the repo root
(`.\run_tests.cmd` in PowerShell — it will not run without the `.\`).

`tools/diagnose.py <file>` dumps the raw grid, the detected header and column
map, every warning, and the parsed records. When a file extracts badly, run
that first: it shows the failure where it happens rather than three stages
later in the reconciliation.

### v2.12 — SAP Business One ledgers

An export of six transactions totalled 5,127,459.22 when its true movement was
−1,748,166.00. The amount column was `Deb./Cred. (LC)`, unrecognised, so the
parser inferred one and chose `Cumulative Balance (LC)` — reading the running
balance as the transaction amount. The output looked entirely plausible.

1. `_match_header` strips currency brackets, so `Deb./Cred. (LC)` → `deb cred`
   (now an amount synonym), `Amount (AED)` → `amount`
2. **Balance columns are barred from being the amount** — anything headed
   *cumulative*, *running*, *closing*, *opening* or *balance*. Enforced in both
   column inference *and* the last-numeric-cell fallback. Missing the second
   one let an account-header row keep the opening balance as a transaction
3. A space inside a document number (`RC 15400071`, `IN 1300027`) is allowed in
   the reference column only — widening `REF_HINT` globally would pull
   "of 15400071" out of narration
4. `infer_type` matches plurals. `\bpayment\b` does not match "Payments", so
   every row of `Incoming Payments - C000124` was typed as an invoice and kept
   a positive sign

### v2.11 — Reverse Parcel Services LLC

1. `readers.py` — allocation fragments folded into their parent row
2. `parser.py` — sign invariant on debit-type rows
3. `parser.py` — row-accounting invariant
4. `normalize.py` — `REF_HINT` alternation reordered; `CODR2026/HO/105` was
   truncating to `CODR2026`, collapsing eight vouchers into one
5. `reconcile.py` — pair tolerance 2% → 0.5%, plus date agreement required
   above a 25 AED gap. 2% had paired `DN/HO/2026/335` (1,806.99) with invoice
   5439 (1,771.00) — unrelated documents
6. `reconcile.py` — payment lane reports its aggregate gap
7. `main.py` — counterparty guard: zero shared reference tokens across two
   populated documents means different vendors, so say so

## Regression cases

Sample documents are not in the repo; ask before assuming a path.

- **Bionutri** — bare numeric invoice `15195` must stay a reference, not become
  `~ROW7`. Zero-amount unreferenced rows are wrapped-text artifacts, discard.
- **Muscat SOA** — an "Off set" row must not claim bill `016/26/00012`, whose
  reference appears only in its allocation narration.
- **BIOMAX** — `BMX-TI-2469` must keep its prefix; it once truncated to
  `TI-2469`.
- **Reverse Parcel** (v2.11 target) — expect **21 matched**, one amount
  difference of **2,558.00** on invoice 5617, no DN/335 false match, and a
  stated payment gap of **25,068.02**. Net difference ties to **54,567.14**.
- **SAP B1 GL (Kuwa Food Supplements)** — 6 records, 2 of them negative,
  summing to **−1,748,166.00**. Opening 1,929,045.86 less that movement gives
  180,879.86, the closing balance printed in the file. Covered by
  `tests/test_sap_ledger.py` with the real rows, so the tie-out is asserted.

## Traps

- `sh.clear()` vs `clearContents()` in the Apps Script pipeline: clearing
  formats turned FCY totals into dates.
- Zoho's `reports/generalledger` is account-level only and ignores
  `show_details`; `reports/accounttransactions` 404s on this edition. Line-level
  GL has to be assembled from `journals/{id}`, `banktransactions`, `bills/{id}`
  and `expenses/{id}`. `gl_lines` currently holds only journals + banking —
  **41% of OpEx value**, missing bills (35.6%) and expenses (23.4%).
- Currency is pegged: AED 3.6725/USD, SAR 3.75/USD, so AED/SAR is a constant
  0.9793. Use booked `exchange_rate` per document where it exists; never a live
  feed.
