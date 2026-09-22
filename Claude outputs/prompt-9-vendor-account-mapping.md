# PROMPT 9 — vendor → expense account mapping, maintained in the platform

Follows Prompt 8, which created `opex_gl_lines`. This puts the vendor→account mapping in the
app so it refreshes itself as GL loads, instead of living in a spreadsheet that goes stale.

Seed: `vendor_account_map_seed.csv` — 1,359 vendors, 190 exact / 17 needs-review / 1,152
unmapped, carrying AED 4.36m of GL evidence.

```
Add a vendor → expense account mapping that maintains itself. Do not change opex_gl_lines or
the Expense Analysis view.

TABLE vendor_account_map
  id uuid pk default gen_random_uuid()
  vendor_id uuid                       -- resolved against the vendor master, nullable
  vendor_display_name text not null
  vendor_company_name text
  vendor_key text not null             -- normalised, see below
  entity text
  vendor_currency text, vendor_status text

  confirmed_account text               -- the decision. Only a person writes this.
  confirmed_by uuid, confirmed_at timestamptz

  suggested_account text               -- recomputed from evidence, never by hand
  suggested_confidence numeric         -- dominant account's share of that vendor's GL value
  suggested_at timestamptz

  mapping_status text not null         -- confirmed | suggested | needs_review | unmapped
  mis_category text, accounts_used int
  gl_txn_count int, gl_amount_base numeric
  matched_gl_names text
  drift boolean not null default false -- evidence now disagrees with the confirmation
  notes text
  updated_at timestamptz
  UNIQUE (vendor_key, entity)

TABLE vendor_account_map_history
  id uuid pk, map_id uuid, changed_at timestamptz, changed_by uuid,
  field text, old_value text, new_value text, reason text
Write a row for every change to confirmed_account or mapping_status. Never update in place
without recording it — this table is the audit trail for how spend gets coded.

NORMALISATION — vendor_key
lowercase, punctuation to spaces, then strip these suffix words:
  llc, l l c, fzco, fz, fz llc, fze, dmcc, pvt, private, limited, ltd, inc, llp, co, company,
  the, plc, gmbh, bv, pte, ou, corp, corporation, trading, general, est, establishment
then collapse whitespace. This is what makes "PUCOV TECHNOLOGIES PRIVATE LIMITED" and
"PUCOV Technologies Pvt Ltd." one vendor.

THE SUGGESTION ENGINE — runs after every GL load, and on demand
For each vendor, over opex_gl_lines rows whose extracted vendor name normalises to the same
vendor_key:
  suggested_account    = the account with the largest total net_amount
  suggested_confidence = that account's share of the vendor's total
  gl_txn_count, gl_amount_base, accounts_used, mis_category, matched_gl_names = recomputed
Then set mapping_status:
  confirmed     confirmed_account is set
  suggested     no confirmation, evidence exists, confidence >= 0.80
  needs_review  no confirmation, evidence exists, confidence < 0.80
  unmapped      no evidence at all

THREE RULES THE ENGINE MUST OBEY

1. A SUGGESTION NEVER OVERWRITES A CONFIRMATION. confirmed_account is written only by a person,
   through the screen. The engine writes only the suggested_* columns.

2. DRIFT IS FLAGGED, NOT APPLIED. When a confirmed vendor's suggested_account no longer equals
   its confirmed_account, set drift = true and surface it. Do not change the confirmation and
   do not clear the flag automatically — a person resolves it by re-confirming or by changing
   the decision. This is the mechanism that catches a vendor whose coding has genuinely moved.

3. NEVER INVENT AN ACCOUNT. A vendor with no GL evidence stays unmapped with an empty
   suggested_account. Do not guess from the vendor's name, industry, or country. A wrong
   default is worse than a blank, because a filled cell stops being questioned.

SCREEN at /vendors/account-mapping
A table of all vendors, default sorted by gl_amount_base descending so the money is at the top.
Columns: vendor, entity, mapping status, suggested account, confidence, confirmed account,
GL transactions, GL amount, accounts used, MIS category, drift flag.
  - Filter on mapping_status, entity, drift, and confidence band.
  - Search on vendor name.
  - Inline edit of confirmed_account, choosing from the accounts present in opex_gl_lines plus
    a free-text fallback.
  - Multi-select with "Confirm suggestion" so a run of high-confidence rows is one action.
    Confirming in bulk writes one history row per vendor, not one for the batch.
  - Each row expands to its GL evidence: account, MIS category, transaction count, amount, and
    the extracted names that matched — so the person confirming can see what they are agreeing
    to rather than trusting a label.
  - A banner counts rows needing attention: needs_review plus drift.
Write access requires the expense-analysis write permission; everyone else sees it read-only.

DUPLICATE VENDORS
Vendor keys that collide across records — the master has "Deloitte & Touche (M.E.)" twice and
once as "(M.E.) LLP" — group under one key and show the source records together. Confirming the
group confirms all of them. Show a duplicate count on the screen; this list is also worth
handing back to whoever maintains the vendor master.

IMPORT AND EXPORT
Upload vendor_account_map_seed.csv with a preview before commit. On upload, a row whose
mapping_status is "suggested" or "needs_review" fills only the suggested_* columns; the seed
must not create confirmations. Re-uploading updates evidence columns and leaves every
confirmed_account untouched.
Export the current mapping as CSV, including status, confidence and confirmed-by.

Do not modify opex_gl_lines or the vendor master from this screen.
```

## Verify

- [ ] the seed loads **1,359** rows with zero confirmations
- [ ] status split on load: **190 suggested · 17 needs_review · 1,152 unmapped**
- [ ] `PUCOV TECHNOLOGIES PRIVATE LIMITED` and `PUCOV Technologies Pvt Ltd.` share one
      vendor_key and appear as one grouped vendor
- [ ] the three Deloitte records group into one
- [ ] Deloitte lands in **needs_review**, not suggested — its confidence is 52%
- [ ] Google shows suggested `COS - Marketing Expenses` at 92% over 16 transactions
- [ ] confirm a vendor, re-run the suggestion engine, and the confirmation is unchanged
- [ ] force a disagreement (confirm an account that is not the dominant one, re-run) and the
      row comes back with drift = true and its confirmation intact
- [ ] an unmapped vendor stays blank after a re-run — no invented account
- [ ] every confirmation writes a history row naming who and when
