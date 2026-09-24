# PROMPT 11 — connect Gmail and send vendor emails

Prerequisite: the Google side is done — Gmail API enabled, Google Auth Platform configured with
an **Internal** audience, `gmail.send` and `userinfo.email` added under Data Access, and a Web
application OAuth client created. Client ID and secret in Lovable Cloud secrets.

```
Add per-user Gmail connection and vendor email sending. Each person sends as themselves from
their own @feelvaleo.com address — not from a shared mailbox and not from a no-reply address.
A vendor replying to a statement query must reach the person who sent it.

SECRETS — Lovable Cloud only, never in client code
  GOOGLE_OAUTH_CLIENT_ID
  GOOGLE_OAUTH_CLIENT_SECRET
  GOOGLE_OAUTH_REDIRECT_URI

TABLE user_email_accounts
  user_id uuid pk references auth.users
  email text not null                  -- must end @feelvaleo.com
  refresh_token text not null          -- encrypted at rest; never returned to the client
  scope text, connected_at timestamptz, last_used_at timestamptz,
  status text not null default 'active'   -- active | revoked | error
  last_error text
RLS: a user reads and writes only their own row. The refresh token is never selectable from the
browser under any policy — it is used server-side only.

TABLE email_log
  id uuid pk, sent_by uuid, from_email text, to_email text, cc text[],
  vendor_id uuid, entity text, subject text, body_preview text,
  attachment_names text[], message_id text, thread_id text,
  status text not null,                -- sent | failed
  error text, created_at timestamptz
Everyone with vendors.view can read the log for vendors they can see. Nobody can edit or delete
a row — this is the record of what was said to a vendor and when.

CONNECT FLOW at /settings/email
  "Connect Gmail" starts the OAuth redirect with:
      scope         https://www.googleapis.com/auth/gmail.send
                    https://www.googleapis.com/auth/userinfo.email
      access_type   offline          -- required, or no refresh token comes back
      prompt        consent          -- required, or a re-connect returns no refresh token
      hd            feelvaleo.com    -- pre-filters the account chooser
      state         a signed, single-use value you verify on the callback

  On callback:
    1. Verify state. Reject anything that does not match.
    2. Exchange the code; require a refresh_token in the response. If absent, delete any stored
       token and tell the user to disconnect and reconnect — a silent half-connection that
       fails at send time is worse than a clear failure now.
    3. Read the email from userinfo. REJECT unless it ends in @feelvaleo.com AND matches the
       signed-in user's own address. hd is a UI hint, not a security control — it can be
       bypassed, so check the returned address server-side.
    4. Store encrypted. Show connected address, date, and a Disconnect button.

  Disconnect revokes the token with Google and sets status = 'revoked'. Do not merely delete
  the row — a token that still works at Google but is invisible to you is the worst state.

SENDING
An edge function `send-vendor-email` that:
  - takes vendor_id, to, cc, subject, body, and optional attachments already in storage
  - refuses if the caller has no active user_email_accounts row, with a message pointing at
    /settings/email rather than a generic error
  - refreshes the access token server-side, caching it for its lifetime
  - builds an RFC 2822 message, base64url-encoded (URL-safe alphabet, padding stripped), and
    POSTs to https://gmail.googleapis.com/gmail/v1/users/me/messages/send
  - writes an email_log row on both success and failure, always, before returning
  - on 401, refreshes once and retries; on a second 401, marks the account status 'error' with
    last_error and tells the user to reconnect

  Attachments: multipart/mixed. Cap at 20 MB total and reject above that with a clear message
  rather than letting Gmail bounce it back opaquely.

UI — a "Email vendor" action on the vendor record and on a reconciliation result
  Dialog with: To (defaults to the vendor's email, editable), Cc, Subject, Body, and an
  attachment picker offering the reconciliation export and statement files for that vendor.
  Show the sending address as "Sending as <your address>" so it is never ambiguous.
  Templates: a small set of editable subject/body templates with placeholders for vendor name,
  entity, balance and period. Store them in a table, not in code, so finance can edit them.
  After sending, show the log entry inline on the vendor record.

Gate the whole feature on a new permission `vendors.email`. Add it to the catalogue, grant it
to Finance Manager and Accountant, not to Analyst or Viewer.

Never send to an address the user has not seen in the dialog. No silent BCC, no automatic
copying to a shared mailbox unless the user adds it themselves.
```

## Verify

- [ ] connecting stores a refresh token; the token is not visible in any browser network response
- [ ] connecting with a personal Google account is rejected, naming the reason
- [ ] connecting with a different @feelvaleo.com address than the signed-in user is rejected
- [ ] a send arrives from the connected person's address, and a reply goes back to them
- [ ] the email_log row is written for a failed send as well as a successful one
- [ ] disconnect revokes at Google — reconnecting requires consent again
- [ ] a user without `vendors.email` sees no send action and the edge function refuses them
- [ ] an attachment over 20 MB is rejected with a readable message
