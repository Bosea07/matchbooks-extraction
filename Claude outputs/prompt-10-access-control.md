# PROMPT 10 — access control: single owner-admin, roles, per-page permissions, entity scope

One prompt. Send it on its own — it touches every page, and a half-applied permission
model is worse than none.

```
Replace the current access control with a permission model that is enforced in the database,
not in the UI. Hiding a nav item is not access control: if a route still returns data to a
direct URL or an API call, the page is not protected. Every rule below must be enforced by RLS
on the tables, with the UI reading the same source so the two can never disagree.

THE OWNER ADMIN IS ONE FIXED ACCOUNT
  ashutosh.bose@feelvaleo.com
This account is the only administrator. It is seeded at migration, bypasses every permission
check rather than being granted each permission, and cannot be demoted, deleted, or stripped of
access through any screen. No other account can be made an administrator through the UI.

  is_owner_admin(user_id) = the user's email = 'ashutosh.bose@feelvaleo.com'

Use that function as the first clause of every RLS policy: the owner passes, everyone else is
evaluated against the permission model below. Store the address once, in a single constant or
settings row, so changing it later is one edit rather than a search across policies.

THREE TABLES

permissions          -- the catalogue, seeded, not user-editable
  key text pk                    -- e.g. 'expense_analysis.view'
  resource text not null         -- the page or feature
  action text not null           -- view | create | edit | delete | export | approve
  label text not null
  description text

roles
  id uuid pk, name text unique not null, description text,
  is_system boolean default false      -- system roles cannot be deleted
  created_at, updated_at

role_permissions
  role_id uuid, permission_key text, PRIMARY KEY (role_id, permission_key)

user_permissions      -- per-user overrides on top of the role
  user_id uuid, permission_key text,
  effect text not null,                -- 'grant' | 'deny'
  granted_by uuid, granted_at timestamptz, reason text,
  PRIMARY KEY (user_id, permission_key)

A user may hold more than one role.

EFFECTIVE PERMISSION — one function, used everywhere
  has_permission(user_id, key) =
      is_owner_admin(user_id)
      OR ( NOT EXISTS (user_permissions deny for that user+key)
           AND ( EXISTS (any of the user's roles grants it)
                 OR EXISTS (user_permissions grant for that user+key) ) )
Deny beats grant, including over a role. Implement this as a SQL function and use it in every
RLS policy; do not re-implement the logic in TypeScript.

PERMISSION CATALOGUE — seed exactly these. There are no admin.* permissions: administration
belongs to the owner account and is not grantable.
  home.view
  transactions.view / .export          tax.view
  vendors.view / .create / .edit       vendors.mapping.view / .edit
  reconciliation.view / .run           reconciliation.view_all
  reports.view                         reports.view_all
  ap.view                              ap.actuals.view
  ap.exclusions.view / .edit           ap.snapshots.import
  expense_analysis.view                expense_analysis.import
  expense_analysis.mapping.edit
  pemo.view / .import / .mapping.edit
  mis.view

Three of these are data scope, not page access, and matter most:
  reconciliation.view_all   without it, a user sees only reconciliations they ran
  reports.view_all          without it, a user sees only their own reports
  ap.actuals.view           the unfiltered live AP view, separate from ap.view
Default all three OFF for every seeded role.

ENTITY SCOPE — a second dimension, independent of permissions
  user_entities (user_id, entity_code)    -- KUWA, DMCC, KSA, SAHA, SHIFA, VWHC, PVT, HOLDING
A user with no rows sees every entity. A user with rows sees only those, everywhere — AP,
expense analysis, MIS, reconciliation, vendors. Enforce in RLS on each table carrying an entity
column, not by filtering in the query layer. The owner is never entity-scoped.
This is what lets an entity accountant use the platform without seeing group numbers.

SEED FOUR ROLES
  Finance Manager   every permission in the catalogue, including all three *_all scopes
  Accountant        view + create/edit on transactions, vendors, reconciliation, expense
                    analysis, pemo, ap; no *_all scopes
  Analyst           view + export only, across all pages; no create, edit or import
  Viewer            home.view, reports.view, ap.view only
Mark all four is_system = true. None of them confers administration.

ADMIN SCREEN at /admin/access — visible only to the owner account
Two tabs.

  ROLES — a matrix: permissions as rows grouped by resource, roles as columns, a checkbox at
  each intersection. Save writes role_permissions. System roles are editable but not deletable.
  Show, per role, how many users hold it.

  USERS — one row per user: name, email, roles, entity scope, and an "Overrides" count.
  Expanding a user shows the same permission matrix for them alone, with each row rendered as
  one of: inherited-allow (from a named role), inherited-deny, granted, denied. A click cycles
  inherit → grant → deny. Always show WHICH role an inherited permission came from — debugging
  someone's access needs the origin, not just the outcome.
  Entity scope is a multi-select on the same panel.
  The owner's own row is shown as "Owner — full access" with no editable controls.

ROUTE AND NAV
Derive the sidebar and the route guards from the same effective-permission function. A page the
user lacks `.view` on is absent from the nav AND returns a 403 page on direct navigation.
Never render a page shell and then hide its contents — a half-rendered page leaks structure and
sometimes data.
Sub-pages follow their parent: no transactions.view means tax.view is unreachable even if
granted.

AUDIT
permission_audit (id, actor_id, target_user_id, target_role_id, permission_key, action,
old_value, new_value, reason, created_at)
Write a row for every change to roles, role_permissions, user_permissions and user_entities.
Show it at /admin/audit, owner only, filterable by actor, target and date. Never update these
rows.

THE OWNER ACCOUNT CANNOT BE LOCKED OUT
  - No screen may remove, demote or deactivate ashutosh.bose@feelvaleo.com. RLS refuses it and
    the UI does not offer it.
  - Deleting that user account is blocked at the database level, not only in the UI.
  - If the account is somehow absent at startup, the app must surface a clear error naming the
    missing owner rather than silently running with no administrator.

MIGRATION
Seed the owner account first. Then map existing users onto the four roles by their current role
field, closest match, and list the mapping on screen for confirmation before it commits. Nobody
should lose access silently — when a current role is ambiguous, assign Analyst and flag it for
review rather than guessing upward.
```

## Verify

- [ ] the owner account reaches every page, including with no rows in `user_permissions`
- [ ] no screen offers a way to grant administration to another account
- [ ] a Viewer navigating directly to `/expense-analysis` gets 403, not a blank page
- [ ] the same Viewer calling that table's API directly gets no rows — RLS, not UI
- [ ] a user without `reports.view_all` sees only reports they ran, and the page count agrees
- [ ] a user without `reconciliation.view_all` sees only their own runs
- [ ] a user scoped to KUWA sees KUWA-only figures on AP, Expense Analysis and MIS, and the
      totals differ from an unscoped user's
- [ ] a deny override beats a role that grants the same permission
- [ ] attempting to delete or deactivate the owner account fails at the database, not just the UI
- [ ] every permission change appears in the audit log with the actor
- [ ] the user panel names the role each inherited permission came from

## One thing to decide, not urgent

A single hard-pinned admin is one account away from nobody being able to administer the
platform — a lost password, a disabled Google account, a change of email. Two ways to cover it,
either is fine:

1. Add a second owner address you also control (the constant becomes a short list), or
2. Keep one owner and write down that recovery means a database migration changing the
   constant — which is fine as long as someone other than you knows that is the procedure.

Worth settling before other people depend on the platform, not before tomorrow.
