# PROMPT 12 — wire the mail provider behind the existing email dialog

The compose dialog already exists, with templates, To/Cc, subject, body and Copy / Open in mail
client / Send. This adds the provider so Send works, and removes the
"No mail provider is configured" banner.

Prerequisites, both must already be true:
- Google Auth Platform configured, audience **Internal**, Gmail API enabled, scopes
  `gmail.send` and `userinfo.email` added under Data Access, and a **Web application** OAuth
  client created with the app's callback as an authorised redirect URI.
- Lovable Cloud secrets set: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
  `GOOGLE_OAUTH_REDIRECT_URI`.

```
Wire a Gmail provider behind the existing vendor email dialog. Do not redesign the dialog —
keep its templates, fields and the Copy email / Open in mail client buttons exactly as they are.
Send currently does nothing because no provider is configured; this makes it work.

Each person sends as themselves from their own @feelvaleo.com address. Not a shared mailbox,
not a no-reply address — a vendor replying to an invoice request must reach the person who
asked.

TABLE user_email_accounts
  user_id uuid pk references auth.users
  email text not null                  -- must end @feelvaleo.com
  refresh_token text not null          -- encrypted at rest, never returned to the browser
  scope text, connected_at timestamptz, last_used_at timestamptz,
  status text not null default 'active',   -- active | revoked | error
  last_error text
RLS: a user reads and writes only their own row. No policy may expose refresh_token to the
client; it is read server-side only.

TABLE email_log
  id uuid pk, sent_by uuid, from_email text, to_email text, cc text[],
  vendor_id uuid, entity text, period text, template text,
  subject text, body text, attachment_names text[],
  message_id text, thread_id text,
  status text not null,                -- sent | failed | copied | opened_in_client
  error text, created_at timestamptz
Rows are append-only: no update, no delete. This is the record of what was said to a vendor.
Copy email and Open in mail client already write to Communication history — point them at this
table so all three routes land in one place with a status saying which was used.

CONNECT FLOW at /settings/email
  "Connect Gmail" redirects to Google with:
      scope         https://www.googleapis.com/auth/gmail.send
                    https://www.googleapis.com/auth/userinfo.email
      access_type   offline        -- without this no refresh token is issued
      prompt        consent        -- without this a RE-connect returns no refresh token
      hd            feelvaleo.com  -- pre-filters the account chooser
      state         signed, single-use, verified on the callback

  On callback:
    1. Verify state; reject a mismatch.
    2. Exchange the code and REQUIRE refresh_token in the response. If it is absent, store
       nothing and tell the user to disconnect and reconnect. A half-connection that looks
       fine and fails a week later is worse than an error now.
    3. Read the address from userinfo. Reject unless it ends @feelvaleo.com AND equals the
       signed-in user's own address. hd is a UI hint and can be bypassed — check server-side.
    4. Store encrypted. Show the connected address, the date, and a Disconnect button.
  Disconnect revokes the token with Google, then sets status='revoked'. Do not just delete the
  row — a token still live at Google but invisible to you is the worst state to be in.

EDGE FUNCTION send-vendor-email
  Input: vendor_id, to, cc, subject, body, optional attachment paths already in storage.
  - If the caller has no active user_email_accounts row, return a message naming
    /settings/email. Do not return a generic failure.
  - Refresh the access token server-side and cache it for its lifetime.
  - Build an RFC 2822 message; for attachments use multipart/mixed. Base64url-encode
    (URL-safe alphabet, padding stripped) and POST to
    https://gmail.googleapis.com/gmail/v1/users/me/messages/send
  - Write an email_log row on success AND on failure, always, before returning.
  - On 401: refresh once and retry. On a second 401: set status='error' with last_error and
    tell the user to reconnect.
  - Reject attachments totalling over 20 MB with a readable message rather than letting Gmail
    reject them opaquely.

DIALOG CHANGES — minimal
  - Replace the "No mail provider is configured" banner with the connection state:
      not connected  -> "Send as yourself — connect your feelvaleo Gmail" linking to
                        /settings/email, with Send disabled
      connected      -> "Sending as <address>"
  - Leave Copy email and Open in mail client working in both states. They are the fallback when
    someone has not connected, and they stay useful afterwards.
  - After a successful send, show the log entry inline on the vendor record.

TEMPLATE FIX — do this in the same pass
  The monthly invoice request currently renders "Our records show an expected value of 0 AED"
  when no figure exists for that vendor and period. 1MG is an example: no activity, so the
  placeholder resolves to zero. Telling a vendor you expect 0 AED reads as an error.
  Omit that sentence entirely when the amount is zero, null or unknown. Apply the same rule to
  every placeholder in every template: a placeholder with no value removes its sentence rather
  than printing 0, blank or "undefined". Show the resolved text in the dialog before sending,
  as it already does, so the author sees exactly what the vendor will see.

PERMISSION
  Gate sending on `vendors.email`. Grant to Finance Manager and Accountant; not to Analyst or
  Viewer. Copy email and Open in mail client need only `vendors.view`.

Never send to an address the user has not seen in the dialog. No silent BCC, no automatic copy
to a shared mailbox unless the user typed it into Cc themselves.
```

## Verify

- [ ] connecting stores a refresh token, and it appears in no browser network response
- [ ] a personal Google account is rejected, with the reason shown
- [ ] a different @feelvaleo.com address than the signed-in user is rejected
- [ ] the dialog banner switches to "Sending as <your address>" once connected
- [ ] a test send arrives from your address, and a reply comes back to you
- [ ] email_log gets a row for a failed send, not only a successful one
- [ ] Copy email and Open in mail client still work and still log, with a status saying which
- [ ] disconnect revokes at Google — reconnecting asks for consent again
- [ ] a vendor with no expected amount produces a draft with **no** "expected value" sentence,
      not "0 AED" — check 1MG for September 2026
- [ ] a user without `vendors.email` sees Send disabled and the edge function refuses them
