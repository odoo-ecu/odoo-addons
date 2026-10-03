User Access Studio
==================

Release v19.0.3.0.0 for Odoo 19 Community. Free under LGPL-3.0 or later.

Configure restrictions, inspect access decisions, and manage reusable roles
from one Odoo application.

Included workflows
------------------

* Inactive starter profiles, a guided record-filter builder and an interface
  element picker based on installed views.
* Profile rules for menus, fields, models, records, buttons, tabs, search,
  chatter, import/export and selected reports/actions.
* An access inspector showing native permissions, record rules and active
  Studio profiles for a user, company, operation and optional sample record.
* Nested roles, owned assignments, employee access requests, manager decisions
  and a governance overview with pending and overdue work.
* Versioned YAML import/export with validation, change preview and protection
  against applying a stale preview; policy snapshots and reviewed restore.
* Optional OpenAI policy generation. Generation returns a draft; a separate
  action creates an inactive profile without users. Existing policy is not
  replaced automatically.

Important operating boundaries
------------------------------

Profiles restrict existing permissions; roles can grant native Odoo groups.
Those groups apply database-wide. A role's company controls administration
and eligible recipients, not the company scope of native permissions.

Dated profile expiry removes a restriction, not an underlying grant. Role
assignment expiry is reconciled every minute while the scheduler is healthy;
outages can delay revocation. Other active assignments and independent manual
memberships are retained.

The inspector performs access checks, not business create/write/delete calls.
It does not validate arbitrary workflow methods, proposed field values or
external effects. Unknown custom execution remains untested.

Normal profile form edits do not automatically create a revision. Use
**Save Revision** before editing. Reviewed imports/restores save snapshots;
AI-created drafts also receive a snapshot. Governance evidence is protected
from application-level editing, not from a database administrator.

The AI adapter uses OpenAI only and requires administrator configuration,
provider billing and explicit transmission consent. Its test suite uses
mocked provider responses; live-provider validation is separate. Core access
controls need no AI service.

Installation and documentation
------------------------------

Odoo dependencies: ``base``, ``mail``, ``web``. Python dependency: ``PyYAML``.
Install the package matching your Odoo series. See ``docs/HOWTO.md`` for
workflows and limits, ``docs/POSITIONING.md`` for product scope, and
``CHANGELOG.md`` for release history. Consult the release's retained test
results for verified version coverage; test-source presence is not a
compatibility certification.

Author: ERP Heritage (https://www.erpheritage.com.au).
Contact: info@erpheritage.com.au.
