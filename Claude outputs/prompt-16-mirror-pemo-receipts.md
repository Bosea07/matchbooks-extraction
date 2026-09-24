# PROMPT 16 — receipts that still open next week

Pemo's `receipts[0]` is a **signed, time-limited storage URL**, not a permanent link. Observed
lifetime: about 65 minutes.

```
Start  [Tue, 22 Sep 2026 04:17:23 GMT]
Expiry [Tue, 22 Sep 2026 05:22:23 GMT]
Current[Thu, 24 Sep 2026 12:18:02 GMT]   -> AuthenticationFailed
```

Storing that string in `pemo_transactions.receipt_url` means every receipt link is dead within
the hour. It appears to work when tested straight after a sync, which is why it looks like a
permissions or extension problem rather than an expiry one.

```
Receipt links on the Pemo pages are expired signed URLs. Mirror the files into storage at sync
time and serve permanent links from the platform.

STORAGE
Create a private Supabase Storage bucket `pemo-receipts`.
Path: pemo-receipts/{entity}/{yyyy}/{mm}/{expense_id}-{n}.{ext}
Private, not public. Access is through a short-lived signed URL the platform mints on demand,
so the file is never world-readable.

SCHEMA — add to pemo_transactions
  receipt_url        text   -- keep, but it is now a HISTORICAL record of the source link only
  receipt_path       text   -- the storage path of the mirrored file
  receipt_mirrored_at timestamptz
  receipt_status     text   -- mirrored | missing | failed | expired_at_source
  receipt_error      text
Do not overwrite receipt_url; a future audit may want to know where the file came from.

MIRRORING — during the Pemo sync, before the link expires
For every transaction with a receipt:
  - fetch the signed URL immediately, in the same run that produced it
  - store the bytes at the path above, preserving the content type
  - set receipt_path, receipt_mirrored_at, receipt_status='mirrored'
  - on a 403 or AuthenticationFailed, set receipt_status='expired_at_source' and record the
    error. That means the sync took longer than the signature's life — surface it rather than
    silently leaving a dead link.
  - a transaction with no receipt gets receipt_status='missing'
Skip anything already mirrored: a row with receipt_status='mirrored' and a file present at
receipt_path is not re-fetched. Re-running the sync must not re-download the whole history.

SERVING
An edge function `pemo-receipt` that takes a transaction id, checks the caller has pemo.view,
mints a short-lived signed URL for receipt_path, and redirects.
The UI links to that function, never to receipt_url. If receipt_status is not 'mirrored', show
the reason in place of a link — "no receipt", "source link expired before it could be saved" —
rather than a link that fails when clicked.

BACKFILL
Existing rows hold expired URLs and cannot be recovered from the stored string. Re-run the Pemo
sync so fresh signed URLs are issued and mirrored in the same pass. Report how many rows moved
from expired to mirrored, and how many had no receipt at source.

CLOSE READINESS
The missing-receipt metric currently counts rows with no receipt_url. Change it to count
receipt_status='missing'. A row whose receipt exists but failed to mirror is a different
problem from a receipt that was never attached, and lumping them together hides both.
```

## Verify

- [ ] a sync mirrors receipts and sets receipt_status='mirrored'
- [ ] clicking a receipt on the platform opens the file, and still does tomorrow
- [ ] a transaction with no receipt shows "no receipt", not a broken link
- [ ] re-running the sync does not re-download already-mirrored files
- [ ] a user without `pemo.view` cannot open a receipt through the edge function
- [ ] the bucket is private — a receipt path pasted directly into a browser is refused
- [ ] the missing-receipt count on the dashboard counts only receipt_status='missing'

## If you would rather not store the files

The alternative is fetching on click: the platform calls the Pemo API for that transaction,
gets a fresh signed URL, and redirects. No storage, always current.

The trade-offs are real, so decide deliberately. It puts the Pemo API keys on the platform,
makes every receipt view a live API call, and means no receipts at all when Pemo is slow or
down. It also loses the audit copy — if the card programme ever moves, the evidence goes with
it. Mirroring costs storage; this costs availability and permanence.
