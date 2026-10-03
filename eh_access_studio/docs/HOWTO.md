# User Access Studio 3.0: administrator guide

This guide describes the Odoo 19 package. Use the matching package and retained
release validation results for another Odoo series.

## Install and access

Install `eh_access_studio` through Odoo Apps. Module dependencies are `base`,
`mail` and `web`; the YAML wizard requires Python `PyYAML`. Core controls do not
require an AI key or network service.

Internal employees can open **User Access Studio → Access Requests**.
**Access Studio: User** can read profile configuration. **Access Studio:
Manager** can configure profiles and governance and open the inspector.
System administrators inherit manager access and configure the optional AI
provider. Record and method checks enforce request ownership and company
boundaries; hiding a menu is not the authorization check.

## Choose the right concept

| Concept | Purpose |
| --- | --- |
| Profile | Restrictions over existing Odoo permissions: fields, models, records and interface controls. |
| Role | Reusable native groups, included roles and restriction profiles. Roles can add entitlements. |
| Assignment | A role attached to a user, with a reason and optional time window. |
| Request | An employee's proposed role assignment awaiting another manager's decision. |
| Revision | A saved profile configuration that can be compared and restored. |
| Governance evidence | Server-authored records of role, assignment and request lifecycle events. |

A profile does not grant Odoo access. Multiple domain profiles combine
permissively inside native Odoo rules, while restrictive interface flags
combine across profiles. Adding a tighter-looking profile may leave access
from another profile intact.

A role's company limits who administers and receives it. **Native Odoo groups
remain database-wide**; this is not a company-local permission system. Included
roles must have the same company. Linked restriction profiles must be scoped
only to that company, although their existing profile semantics still apply
to eligible users regardless of the selected company.

## Start with an inactive template

1. Open **New from Template**.
2. Choose the scenario, profile name, users and company scope.
3. For an own-record or contractor scenario, choose an installed document type
   with a responsible-user field.
4. Read the description, then choose **Create inactive draft**.
5. Review the resulting rules and dates. Activate only when the intended
   restriction is ready. Use Overview to find inactive profiles later.

| Template | Result |
| --- | --- |
| Read-only | Restricts existing business write access; framework exceptions such as messaging remain available. |
| Own records | Read/edit records whose `user_id` is the current user; hides export globally. No new native permissions are granted. |
| Contractor | Own-record read-only rule on the chosen model; hides Settings and import/export/spreadsheet controls; disables developer mode. It does not provision a portal or an app allowlist. |
| Auditor | Dated read-only restriction with developer mode disabled. Dates are calculated when the draft is created; review them before activation. At expiry, underlying permissions remain, including any existing write access. |

**A dated restriction is not temporary access.** Use an owned role assignment
when the requirement is to remove an entitlement at the end of an approved
period. Keep a live profile active while preparing its inactive draft copy;
deactivating it merely to edit would remove its protection.

## Build a record filter without writing a domain

On an inactive profile's **Domain Access** tab, select **Build filter with
guided choices**.

1. Choose the document type and allowed operations.
2. Choose all matching records, current user's records, current user plus
   reporting team, or subordinates only.
3. Choose the responsible-user field. Reporting-team scopes require the
   Employees app and its manager hierarchy.
4. Optionally choose a calendar date range. Calendar presets use Date fields;
   use explicit additional conditions for DateTime fields.
5. Add ordinary conditions with Odoo's domain widget. Runtime user/team tokens
   are generated separately, so the widget does not treat them as record IDs.
6. Review the generated filter and add it to the inactive profile.

The builder refuses a second domain line for the same model in that profile
because extra lines can widen access. Edit the existing rule when needed.
**Read outside this filter** leaves reading available through the native
permissions while filtering changes; it is not a warning-only mode.

Advanced filter editing remains available. Common tokens:

| Token | Meaning |
| --- | --- |
| `__uid__` | Current user |
| `__cid__` | Resolved current company |
| `__company_ids__` | User's permitted companies |
| `__subordinate_uids__` | Users below the current user in the HR manager chain |
| `__team_uids__` | Current user plus those subordinates |
| `__today__`, `__yesterday__`, `__tomorrow__` | Relative dates |
| `__week_start__` … `__year_end__` | Supported calendar boundaries |
| `__last_7_days__`, `__last_30_days__`, `__last_90_days__`, `__last_365_days__` | Rolling start dates |

For example, `[("user_id", "=", "__uid__")]` selects own records. The advanced
HR resolver returns no subordinates when HR is unavailable; the team token
still includes the current user. The guided builder requires HR before
creating a reporting-team filter.

## Choose buttons, tabs and search entries

On **Hide Buttons / Tabs / Filters**, select **Choose from installed views**.
Choose the document type and element type, then **Find elements**. Select a
label from the results and add it to the inactive profile. Results include
source view names; technical identifiers remain available for advanced work.

Discovery combines installed primary/inherited views and skips embedded
submodel elements. Elements without supported stable names cannot be targeted
by this picker. A rule affects matching names across the selected model's
views. Hiding a Confirm button, menu or tab does not authorize or forbid the
underlying business operation; add appropriate server restrictions separately.

## Inspect current access

Open **Inspect Access**, choose a user, permitted company, document type and
operation. Select a sample record for record-specific checking, then inspect.
The results show native groups and model permissions, native record rules,
the combined filter and relevant Studio profiles/model/field rules.

Interpret the result carefully:

- **Access checks passed:** the checked model/record gates allow the question.
- **Denied:** one of the checked gates rejects it.
- **Model checks passed; record/value checks remain:** no sample record or a
  create question leaves additional checks undecided.
- **Unable to establish access:** a check or its supporting evidence failed;
  review the server log before relying on the result.

The inspector does not run create/write/delete, workflow methods or external
calls. It does not evaluate proposed field changes, arbitrary business
constraints or custom execution. The existing profile **Manual Test Instructions** button
still returns manual sign-in instructions; it is not an automatic simulation.

## Create roles and assignments

Create a **Role**, choose its company, native groups, optional same-company
included roles and eligible restriction profiles. Use **Manage Assignments**
or the **Assignments** menu to choose an internal user, dates and reason.

Managers may grant only native groups they already hold. Only a system
administrator can configure or grant administrative privileges, including
privileges inherited through groups or included roles. Cyclic role inclusion
is rejected.

Active assignments own their contributions. Overlapping assignments and
independent manual memberships are retained when one assignment is revoked.
An explicit manual link can claim independent ownership; resaving an unchanged
membership list does not intentionally convert a temporary grant to permanent.
Use **Revoke Assignment** to end an assignment; history is retained.

The reconciliation job is scheduled every minute. Future activation and expiry
can be delayed by scheduler outages or workload. **There is no guaranteed
exact wall-clock cutoff.** The overview's unreconciled-expiry count identifies
assignments whose recorded active state has passed the expiry time.

## Request and approve access

1. A manager marks an appropriate active role **Requestable**.
2. An employee opens **Access Requests**, selects the role, gives a reason and
   enters start/expiry dates. Requests are created for that employee only.
3. The employee submits the draft. Submitted details cannot be freely edited.
4. Another manager in the permitted company reviews it. A manager cannot
   approve or reject their own request. Rejection requires a decision reason.
5. Approval creates an assignment linked to the request. The assignment's
   state shows activation/expiry; the request retains its approval history.
6. A manager can revoke the approved request's assignment contributions.

Employees can cancel their own draft/submitted requests. Request history is
not deleted. Direct assignment remains a separate manager operation; this is
not a universal two-person approval requirement for every administrative edit.

## Preview imports and restore revisions

Use **Configuration → Import / Export** for Studio profile YAML. The current
export format is schema version 2. It includes supported profile settings,
conditions and stable references. It is not a converter for arbitrary vendor
configuration formats.

For import, upload the file and select **Validate and Preview**. Review every
change and resolve missing or ambiguous references. Missing keys preserve
existing values; explicit `null` or `[]` clears supported optional values.
Unknown settings and unresolved references are rejected rather than skipped.
Select the separate active-profile confirmation when applicable, then
**Apply Reviewed Import**. If the file or target policy changed, preview again.
The apply operation records snapshots and uses an enclosing savepoint so a
failed import does not leave a partially applied batch.

**Normal profile form edits do not automatically create snapshots.** Select
**Save Revision** before ordinary edits. **Policy Revisions** stores snapshots;
open one to preview a restore. Restore verifies the stored fingerprint and
current target, requires confirmation for an active profile, and saves
before/after snapshots. It restores policy settings, not business records.

Reviewed imports/restores save snapshots automatically. AI-created inactive
profiles receive a snapshot. Governance evidence and revisions are protected
from editing through normal application methods; a database administrator
still controls the database. Neither is a log of every denied read or write.

## Optional AI policy drafts

Only OpenAI is included. A system administrator opens **Configuration → AI
Provider**, selects an available Responses model with structured-output
support, supplies an API key and enables drafts. Provider charges are separate
from the free module. Core operation does not depend on AI.

A manager opens **AI Policy Draft**, selects one installed business model,
describes the restriction and explicitly consents to sending the prompt and
selected field schema. Record values, user lists, existing policies and
secrets are not automatically included; do not place sensitive material in
the prompt yourself.

**Generate draft** returns validated structured controls and an explanation.
Review the complete output. **Create inactive profile** is a separate action;
it creates an inactive profile without assigned users. Inspect and correct it
before assigning users or activating it. Generation does not replace existing
policy, and AI does not generate role grants in this version.

The created policy revision retains the provider, model, generation time,
prompt and draft SHA-256 digests, and validation schema version. It does not
store the raw prompt or full provider transcript. Changing the prompt or model
requires generation again; generated output cannot be edited and passed off
as the original provider response. Edit the resulting inactive profile through
the normal policy controls if it needs adjustment.

Global read-only/export settings affect more than the selected model; review
scope carefully. Unknown field references, unsupported controls and unsafe
condition syntax are rejected. An explanation is generated text, not proof
that the policy achieves the business request.

The test suite covers the adapter with mocked provider responses. This release
work does not claim live-provider validation. Preserve release evidence and
validate your configured account/model separately before operational use.

## Operational overview and enforcement boundaries

**Overview** opens pending requests, upcoming assignments, unreconciled expiry,
inactive profiles, active roles and recent governance events. These counts
show operational work; they are not a security-risk score. The older health
and conflict actions remain useful heuristics, not proofs of effective access.

| Control family | Boundary |
| --- | --- |
| Record rules, read-only business access, supported model operation restrictions | Server checks, within native Odoo permissions and documented framework exceptions |
| Hidden/read-only/required fields | View changes plus supported read/export/search/group/write checks; not encryption or universal protection for every custom computed copy |
| Selected reports and server actions | Rendering/run guards in addition to menu filtering; already stored attachments follow attachment access |
| Menus, arbitrary buttons/tabs, view choices, presentation-only search controls | Interface presentation; the underlying business operation requires its own authorization |
| Chatter | Separate user message/activity restrictions with framework-generated communication exceptions |

User-level rules do not constrain arbitrary superuser code. Test real customer
models, inherited fields, computed copies, routes and successful business
flows. Read-only profiles intentionally permit messaging and some framework
operations. A dated restriction ends without removing native permissions.

For a surprising result, inspect the exact user/company/record, inspect all
active profiles, check native groups/rules and review scheduler state. After
an upgrade, run the matching release test suite in an isolated database before
relying on new behavior. Contact: info@erpheritage.com.au.

## Uninstalling

Export policy and governance evidence before removal. Uninstall revokes Studio-owned
role assignments and reconciles their native-group memberships before removing the
ownership ledger. Independently held memberships are preserved. Removing the addon
also removes its restriction overlays and application-held history; keep required
archives separately. Rehearse this operation on a database copy.
