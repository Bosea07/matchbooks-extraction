# PROMPT 13 — import the vendor → account mapping into the existing page

The Vendor Management mapping page already exists, with columns
VENDOR · ENTITIES · SUGGESTED ACCOUNT · ASSIGN ACCOUNT · STATUS and a Confirm action.
Most rows currently read "no activity to suggest from". This loads suggestions into them from a
file, without changing the page's design.

File: `vendor_account_mapping_upload.csv` — 1,252 rows, one per vendor display name.
Columns: `Vendor, Entities, Suggested Account, Assign Account, Status, Confidence, Txns,
Amount, Accounts Used, Source, Matched As`.
419 carry a suggestion (349 Suggested, 70 Needs review); 833 are Unmapped.

Evidence behind it: 33,719 Zoho bill line items across 7,241 bills, where the vendor and the
expense account sit on the same row. The dominant account by value wins; confidence is that
account's share of the vendor's total.

```
Add a bulk import to the vendor account mapping page. Do not redesign the page — keep its
columns, its Confirm action and its layout exactly as they are.

UPLOAD at the top of /vendors/account-mapping (or wherever the mapping page lives)
"Import suggestions" accepts a CSV with these exact headers:
  Vendor, Entities, Suggested Account, Assign Account, Status, Confidence, Txns, Amount,
  Accounts Used, Source, Matched As
A missing header aborts the import and names it. Never partially load.

MATCHING
Match the CSV's Vendor to the existing vendor row on a normalised key: lowercase, punctuation
to spaces, then strip these suffix words —
  llc, l l c, fzco, fz, fz llc, fze, dmcc, pvt, private, limited, ltd, inc, llp, co, company,
  the, plc, gmbh, bv, pte, ou, corp, corporation, trading, general, est, establishment
— then collapse whitespace. The page is vendor-level and aggregates entities, so match on the
vendor, not on vendor+entity.
Report three counts in the preview: matched, unmatched-in-file, vendors-with-no-row-in-file.

WHAT THE IMPORT WRITES
For a matched vendor:
  suggested_account   <- Suggested Account
  confidence          <- Confidence
  evidence_txns       <- Txns
  evidence_amount     <- Amount
  accounts_used       <- Accounts Used
  evidence_source     <- Source
  matched_as          <- Matched As
  status              <- Suggested | Needs review, from the file

THREE RULES THE IMPORT MUST OBEY

1. NEVER TOUCH A CONFIRMED ROW. A vendor whose status is already Confirmed keeps its assigned
   account, its status and its confirmation metadata. Import the suggestion columns beside it
   so drift is visible, but do not change what a person decided.

2. AN UNMAPPED ROW IN THE FILE CLEARS NOTHING. 833 rows carry no suggestion. Those vendors keep
   whatever they already have. A blank in the file means "no new evidence", not "delete the
   evidence you had".

3. ASSIGN ACCOUNT IS NOT AUTO-FILLED. The file pre-fills it for convenience, but the import
   leaves the page's Assign Account empty and puts the value in Suggested Account only.
   Confirming stays a human action, one click or one bulk action — never a side effect of an
   upload.

PREVIEW BEFORE COMMIT
Show, before anything is written:
  - matched / unmatched counts
  - how many rows would change, and how many are skipped because they are Confirmed
  - ACCOUNT NAME CHECK: every distinct Suggested Account in the file that does not exist in the
    Assign Account dropdown, listed by name with a row count. The file's accounts come straight
    from Zoho and include near-duplicate spellings — Staff Welfare Expenses vs Staff Welfare
    Related Expenses, Subscription Charges vs Subscription Expenses, Professional Fees vs
    Professional & Legal Fees. If one of a pair is missing from your list, those rows will not
    map cleanly and you need to know before, not after.
Commit only on an explicit confirm.

ON THE PAGE, AFTER IMPORT
  - Suggested Account shows the account with the confidence beside it, e.g.
    "Inventory Asset (93%)". Where there is no suggestion keep the existing
    "no activity to suggest from".
  - Hovering or expanding a suggestion shows the evidence: transaction count, amount, how many
    different accounts the vendor hit, and the source. Someone confirming should see what they
    are agreeing to, not just a label.
  - Filter on Status, and on a confidence band.
  - Multi-select with "Confirm suggestion" so a run of high-confidence rows is one action.
    Each vendor still writes its own history row, not one row for the batch.
  - Sort by evidence amount descending by default, so the money is at the top rather than the
    alphabet.

IMPORT HISTORY
Record file name, uploaded by, timestamp, rows matched, rows changed, rows skipped as
Confirmed. Show the last ten imports on the page.

Do not modify the vendor master from this screen.
```

## Verify

- [ ] the import matches **419** vendors with a suggestion and reports **833** without
- [ ] `PUCOV TECHNOLOGIES PRIVATE LIMITED` and `PUCOV Technologies Pvt Ltd.` both match —
      the suffix stripping is what makes that work
- [ ] a vendor already Confirmed before the import keeps its account and status afterwards
- [ ] a vendor that had a suggestion and appears as Unmapped in the file keeps its old one
- [ ] Assign Account is still empty after the import — nothing auto-confirmed
- [ ] the preview lists any Suggested Account names missing from the dropdown
- [ ] Amazon Marketplace shows `3PL - Commission and Charges (74%)` and sits under
      **Needs review**, not Suggested
- [ ] `ChromaDex Inc.` shows `Inventory Asset (100%)` under Suggested
- [ ] bulk-confirming ten rows writes ten history entries

## Worth knowing before you import

**Inventory Asset is the top account by a wide margin** — 21,784 of the 33,719 bill lines. It is
correct for a supplements business but it is a balance-sheet account, not a P&L line. If this
mapping feeds expense categorisation anywhere, those vendors will point at inventory rather than
at an expense account, which may be right or may need a second rule.

**These bills are KUWA only.** Every bill id starts 4242483…, and 406 distinct vendors appear.
Your master is 783 DMCC, 396 KUWA, 114 KSA and the rest — so most of the 833 blanks are an
entity gap, not missing data. Exporting Bills the same way for DMCC, KSA, SAHA, VWHC and Pvt Ltd
would fill most of them, and the same import handles the next file unchanged.
