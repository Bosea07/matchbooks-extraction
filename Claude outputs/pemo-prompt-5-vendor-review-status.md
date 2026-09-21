# PROMPT 5 — vendor review status and AP report exclusions

Small, self-contained. Build this before the presentation; it stays useful after the cleanup.

```
Add a review status to the vendor master and let the AP report exclude vendors that are under
review, with the exclusion visible on the report rather than silent.

VENDOR MASTER
Add to the vendor table:
  review_status text not null default 'clean'    -- 'clean' | 'under_review'
  review_note   text                             -- why, e.g. "opening balance wrong in Zoho"
  review_set_by uuid, review_set_at timestamptz

On the Vendor Master list:
  - a "Review status" column with an inline toggle
  - multi-select with a bulk "Mark under review" / "Mark clean" action, so a dozen vendors can
    be flagged in one pass
  - a filter for status
Restrict changing the status to users with vendor-master write permission, and record who set
it and when.

AP REPORT
Default the report to vendors with review_status = 'clean'.

Directly under the report heading, always show a disclosure line — not a tooltip, not a
footnote:
  "Showing <n> vendors · AED <total>.  <m> vendors (AED <excluded total>) excluded pending
   ledger review."
When m = 0, show only the first sentence.

Next to it:
  - a toggle "Include vendors under review", which switches the report to the full population
    and changes the line to "Showing all <n+m> vendors, including <m> under review"
  - the excluded vendor names and amounts available in a collapsible panel or a modal, so the
    question "which ones?" is answerable on the spot

Carry the same treatment to any export of the report: a header row stating the filter applied
and the excluded count and value. An exported file must never look like a complete AP report
when it is not.

Do not delete, archive or alter any vendor or transaction data. This is a view filter only.
Clearing review_status back to 'clean' must restore the vendor to the report with no other
change.
```

## Verify

- [ ] AP total with the filter on, plus the excluded total, equals the unfiltered total exactly
- [ ] the disclosure line appears with a zero count when nothing is excluded
- [ ] the toggle restores the full population and the totals return to the unfiltered figures
- [ ] an export carries the filter statement in its header
- [ ] marking a vendor clean again returns it to the report unchanged
