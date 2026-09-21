# PROMPT 6 — Actuals vs Report (YTD), with the AP exclusion list

Replaces Prompt 5. Two named views over the same data instead of a single filtered report.

- **Actuals** — live Zoho, every vendor, no exclusions. The source of truth.
- **Report (YTD)** — the curated AP report: the 96 reviewed vendors excluded, each carrying its
  reason, its vendor record and its open bills.

Seed file: `ap_excluded_vendors_seed.csv`, 96 rows, AED 3,452,891.94, from the finance team's
review dated 2026-07-17.

```
Restructure the AP report into two named views over the same underlying Zoho data. Do not
change how AP data is ingested.

VIEW SWITCH
At the top of the AP page, a segmented control with two options, the choice held in the URL
(?view=actuals | ?view=report) so a view is shareable and survives a refresh:

  ACTUALS       — every vendor from the live Zoho books. No exclusions, no curation. This is
                  the reconcilable number and it must always be reachable in one click.
  REPORT (YTD)  — Actuals less the vendors on the exclusion list below.

Default the page to REPORT (YTD). Label each view on screen, in every chart title, and in every
export header, so a screenshot can never be mistaken for the other view.

NEW TABLE ap_excluded_vendors
  id uuid pk default gen_random_uuid()
  vendor_id uuid references the vendor master   -- resolved, nullable until matched
  vendor_name text not null                     -- as written on the review sheet
  match_key text not null                       -- normalised name used to resolve vendor_id
  reason_code text not null                     -- CONTROL_ACCOUNT | SETOFF_RECEIVABLE |
                                                -- REMOVE_FROM_AP | ADVANCE_UNAPPLIED |
                                                -- SOA_PENDING | CURRENT_OK | PAYMENT_PLAN |
                                                -- UNREVIEWED | OTHER
  reviewer_note text, finance_note text
  outstanding_at_review numeric, open_cn_at_review numeric, total_bills_at_review int
  last_bill_date date, last_payment_date date, activity text
  source text, reviewed_on date
  active boolean not null default true
  created_by uuid, created_at timestamptz default now()

Seed it from ap_excluded_vendors_seed.csv — 96 rows. Provide a CSV upload on the manage screen
so the list can be refreshed when finance re-reviews, rather than being a one-off migration.

MATCHING IS THE PART THAT BREAKS — build it carefully
The review sheet carries vendor NAMES, not ids, and the names do not match the AP report
exactly. In the 14-Sep report, six vendors on this list appear under slightly different names
or under a second entity and would slip through a naive name filter:
  Heavenly Secrets Private Limited · Tabby L.L.C · SLICK ORGANICS PRIVATE LIMITED 1 ·
  Inmart Commerce Private Limited · Cambridge Nutraceuticals · MH Enterprises L.L.C

So:
  - Resolve each row to a vendor_id at load, using match_key (lowercase, punctuation stripped,
    and the suffixes LLC / L.L.C / FZCO / FZE / DMCC / Pvt / Private / Limited / Ltd / Inc /
    WLL / Co / Company removed).
  - Exclude on vendor_id once resolved, never on the raw name string.
  - Exclusion applies to the vendor ACROSS ALL ENTITIES. The same vendor trades under more than
    one entity — Honasa, Heavenly Secrets, Blindspot, SMSA, Tabby and twelve others — and an
    entity-scoped exclusion would hide one leg and leave the other.
  - Any row that does not resolve to exactly one vendor goes to an "Unresolved" queue on the
    manage screen with the candidate matches offered, and is NOT excluded until a person picks
    one. Show the unresolved count as a warning on the AP page.

THE RECONCILIATION LINE — mandatory, both views
Directly under the AP heading, always:
  Actuals view:  "Actuals · all <n> vendors · AED <total>. Live from Zoho, nothing excluded."
  Report view:   "Report (YTD) · <n> vendors · AED <total>.
                  <m> vendors excluded · AED <excluded total>.  Actuals: AED <actuals total>."
Report total + excluded total must equal the Actuals total exactly. If they do not, show an
error rather than the numbers — a silent mismatch here is worse than a broken page.

EXCLUDED VENDORS PANEL — reachable from the Report view
A table of the excluded vendors with: name, entity, reason code, the current live balance from
Zoho, the balance at review, the movement between the two, reviewer note, finance note.
Group by reason code with a subtotal per group, since these are different kinds of exclusion:
  CONTROL_ACCOUNT and SETOFF_RECEIVABLE are not trade payables at all
  CURRENT_OK and PAYMENT_PLAN are real payables the reviewers found correct
  ADVANCE_UNAPPLIED and SOA_PENDING are payables whose balance is not yet confirmed
Each row drills through to that vendor's record and to its open bills — vendor id, bill number,
bill date, due date, amount, balance, ageing bucket — read live from the same source the
Actuals view uses. The panel must show today's position, not the reviewed snapshot.

MANAGE SCREEN at /reports/ap/exclusions
List, add, edit, deactivate (never delete — set active = false and keep the history).
Bulk CSV upload with a preview of what will change before it commits.
Restricted to users with the AP write permission. Everyone else sees the list read-only.

EXPORTS
Every AP export carries a header block: view name, run date, vendor count, total, excluded
count, excluded total, and the Actuals total. An exported file must never be ambiguous about
which view produced it.

Do not delete, archive or modify any vendor, bill or transaction. This is a view layer.
Setting active = false on an exclusion must return the vendor to the Report view with no other
change.
```

## Verify

- [ ] Report total + excluded total = Actuals total, exactly, on every entity filter
- [ ] Actuals shows **AED 10,305,116** for 14-Sep data with nothing excluded
- [ ] the exclusion list loads **96** rows totalling **AED 3,452,891.94**
- [ ] all six leak-risk vendors above resolve and are excluded from the Report view —
      check Heavenly Secrets in particular, which appears under two entities
- [ ] unresolved rows are listed, counted, and not silently excluded
- [ ] switching views changes the URL; pasting it reproduces the view
- [ ] an exported file states its view name in the header
- [ ] deactivating an exclusion returns that vendor to the Report view unchanged

## One thing to decide before you seed

16 vendors (AED 438,304) are coded `CURRENT_OK` and 6 (AED 560,229) `PAYMENT_PLAN` — the
reviewers found these correct and current. Excluding them understates AP by roughly AED 1.0m.
Seeding all 96 is fine for tomorrow and the reason codes make it reversible in one click, but
those 22 are the first ones to switch back on.
