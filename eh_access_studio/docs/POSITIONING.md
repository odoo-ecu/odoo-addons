# User Access Studio 3.0: product scope

User Access Studio helps Odoo administrators configure restrictions, answer
access questions, and govern reusable role assignments. The Odoo 19 package
is free under LGPL-3.0 or later. Optional implementation, training and support
services are separate from the module license.

## Customer outcomes

| Need | Included workflow | Boundary |
| --- | --- | --- |
| Configure common restrictions | Inactive templates, a guided record filter with a native domain widget, and discovered interface elements | Advanced expressions remain available; hiding an interface element does not forbid its business method. |
| Understand a permission | Inspect a user, company, operation and optional record; see native and Studio contributions | This checks access gates without running the business operation. Proposed values, constraints and custom workflow methods remain untested. |
| Reuse access bundles | Roles combine native Odoo groups, same-company included roles and restriction profiles | Native groups remain database-wide. Company scope governs administration and eligible recipients. |
| Approve temporary access | Employees submit their own requests; another manager approves/rejects; assignments retain their source | Expiry depends on reconciliation scheduled every minute. It is not an exact wall-clock boundary. |
| Change configuration with a comparison | Versioned YAML import validates references and shows changes before apply; stale previews are rejected | The importer handles Studio profiles, not arbitrary third-party formats. Normal form edits are still normal edits. |
| Recover reviewed configuration | Immutable policy snapshots and a reviewed restore with concurrency checks | Use Save Revision before ordinary form edits. Imports, restores and AI-created drafts create snapshots automatically. |
| Draft a restriction in plain language | Optional OpenAI adapter returns typed controls for one selected business model | Generation does not activate policy. OpenAI is the only included provider; provider charges apply. |
| Follow operational work | Overview lists pending decisions, upcoming expiry, unreconciled expiry, inactive profiles and recent events | Counts describe work and configuration, not a certified security or risk score. |

## What the enforcement layer does

The existing profile engine combines interface presentation controls with
server checks. Model create/edit/delete restrictions, read-only business
access, record domains, selected import/export operations and supported
field/report/action paths have backend enforcement. Menus, arbitrary
buttons/tabs, view choices and presentation-only search controls shape the
interface. Keep that distinction visible in demonstrations and sales answers.

Studio record domains are combined within native Odoo access. Multiple Studio
profiles can widen their combined allowed record set; there is no universal
"most restrictive profile wins" rule. A manager must inspect the composed
result, not infer it from one checkbox.

Hidden-field handling covers supported read/export/search/group and related
paths, with explicit exceptions for framework execution. It is not encryption
or a guarantee against every custom computed copy. Test the actual customer
models, routes and business flows. User-level rules do not constrain arbitrary
superuser code.

## Evidence and claims

The repository includes a test suite covering enforcement, portability,
inspection, templates and governance. Release results must identify the exact
package and Odoo runtime. Do not describe a source count, generated backport,
or unrun test as proof of compatibility. No performance superiority or
competitor security comparison is claimed.

AI tests use mocked provider responses. No live-provider validation is claimed
for this release work. An administrator supplies an available structured-output
model and API key. The prompt and selected model schema are sent only after
consent; record contents and existing user assignments are not automatically
included. The immutable revision retains provider and model identifiers,
generation time, prompt and draft SHA-256 digests, and the validation schema
version. It does not retain the raw prompt or a full provider transcript.

Application-level governance events and revisions cannot be rewritten through
the normal application methods. A database administrator retains database
control; this is not tamper-proof external archival.

## Commercial message

"Configure restrictions, see why access checks pass or fail, and manage role
requests with recorded decisions."

Keep the module free and LGPL-licensed. Explain optional AI provider costs and
any separate service charges clearly. Use actual workflows and version-matched
release evidence in demonstrations. Contact: info@erpheritage.com.au.
