# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Governed role grants with explicit ownership and preserved manual access.

Company is the governance boundary, not a scope for native Odoo groups: those
groups still apply database-wide. Timed grants reconcile every minute while
the scheduler is healthy; they are not an exact wall-clock security boundary.
"""
from odoo import api, fields, models, release, SUPERUSER_ID, _
from odoo.exceptions import AccessError, UserError, ValidationError

_GOVERNANCE_TOKEN = object()
_MANAGER_GROUP = "eh_access_studio.group_eh_access_manager"
_GOVERNANCE_LOCK_KEY = 0x4548414343455353


def _lock_governance_users(env, user_ids=()):
    """Serialize graph/inverse-link edits, then take ordered recipient locks.

    The advisory lock makes the affected-user discovery stable while a native
    group hierarchy is changing. Every governed membership writer and the
    reconciler take it before any recipient row lock, avoiding inverse-write
    races and inconsistent lock ordering. Ordinary business writes do not use
    this lock.
    """
    env.cr.execute("SELECT pg_advisory_xact_lock(%s)", [_GOVERNANCE_LOCK_KEY])
    if user_ids:
        env.cr.execute("SELECT id FROM res_users WHERE id = ANY(%s) ORDER BY id FOR UPDATE", [list(user_ids)])


def _refresh(records, names=None):
    if hasattr(records, "invalidate_recordset"):
        records.invalidate_recordset(names)
    else:
        records.invalidate_cache(names)


def _group_field(env):
    for name in ("group_ids", "groups_id"):
        if name in env["res.users"]._fields:
            return name
    raise UserError(_("This Odoo version does not expose editable user groups."))


def _manager(env):
    return env.su or env.user.has_group(_MANAGER_GROUP)


def _assert_manager(env):
    if not _manager(env):
        raise AccessError(_("Only Access Studio managers can change governance settings."))


def _assert_company(env, company):
    if not company or (not env.su and company not in env.companies):
        raise AccessError(_("Select an allowed company before changing its access configuration."))


def _record_access(records, operation):
    if hasattr(records, "check_access"):
        records.check_access(operation)
    else:
        records.check_access_rights(operation)
        records.check_access_rule(operation)


def _group_closure(groups):
    pending = groups
    while pending:
        children = pending.implied_ids - groups
        groups |= children
        pending = children
    return groups


def _manual_intent(before, commands):
    """SET preserves ownership of unchanged links; explicit LINK claims a link.

    A form commonly resends all visible memberships. That must not convert an
    existing temporary grant into permanent manual access accidentally.
    """
    current = set(before)
    intent = {}
    for command in commands or []:
        op = command[0]
        if op == 4:
            current.add(command[1])
            intent[command[1]] = True
        elif op in (2, 3):
            current.discard(command[1])
            intent[command[1]] = False
        elif op == 5:
            intent.update((record_id, False) for record_id in current)
            current.clear()
        elif op == 6:
            replacement = set(command[2])
            intent.update((record_id, record_id in replacement) for record_id in current ^ replacement)
            current = replacement
    return intent


class EhAccessRole(models.Model):
    _name = "eh.access.role"
    _description = "Access Studio Role"
    _order = "name, id"

    name = fields.Char(required=True)
    description = fields.Text()
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    requestable = fields.Boolean(default=False, help="Employees in this company may request this role for themselves.")
    group_ids = fields.Many2many("res.groups", "eh_access_role_group_rel", "role_id", "group_id", string="Native Odoo Groups",
                                 help="Native Odoo groups apply database-wide. Company limits who may manage or receive this role, not the data granted by its groups.")
    profile_ids = fields.Many2many("eh.access.profile", "eh_access_role_profile_rel", "role_id", "profile_id", string="Restriction Profiles")
    included_role_ids = fields.Many2many("eh.access.role", "eh_access_role_included_rel", "role_id", "included_id", string="Included Roles")
    assignment_ids = fields.One2many("eh.access.assignment", "role_id", string="Assignments")

    def action_open_assignments(self):
        self.ensure_one()
        _record_access(self, "read")
        return {"type": "ir.actions.act_window", "name": _("Role Assignments"),
                "res_model": "eh.access.assignment", "view_mode": "list,form" if release.version_info[0] >= 18 else "tree,form",
                "domain": [("role_id", "=", self.id)], "context": {"default_role_id": self.id}}

    def _expanded_roles(self):
        result = self.browse()
        pending = self
        while pending:
            result |= pending
            pending = pending.included_role_ids - result
        return result

    def _expanded_groups(self):
        return _group_closure(self._expanded_roles().group_ids)

    def _active_roles(self):
        result = self.browse()
        pending = self.filtered("active")
        while pending:
            result |= pending
            pending = pending.included_role_ids.filtered("active") - result
        return result

    def _assert_grantable(self):
        _assert_manager(self.env)
        for role in self:
            _assert_company(self.env, role.company_id)
            _record_access(role, "read")
            if not self.env.su and not self.env.user.has_group("base.group_system"):
                groups = role.sudo()._expanded_groups()
                privileged = self.env.ref("base.group_system") | self.env.ref("base.group_erp_manager") | self.env.ref(_MANAGER_GROUP)
                if groups & privileged:
                    raise AccessError(_("Only a system administrator may configure or grant roles with administration privileges, including privileges inherited through groups or included roles."))
                user = self.env.user.sudo()
                own = user["all_group_ids"] if "all_group_ids" in user._fields else user[_group_field(self.env)]
                if groups - own:
                    raise AccessError(_("A manager may only grant native groups they already hold. Ask a system administrator to grant this role."))

    @api.constrains("included_role_ids", "group_ids", "profile_ids", "company_id", "active")
    def _check_configuration(self):
        for role in self:
            frontier = role.included_role_ids
            seen = self.browse()
            while frontier:
                if role in frontier:
                    raise ValidationError(_("Included roles cannot form a cycle."))
                seen |= frontier
                frontier = frontier.included_role_ids - seen
            if any(item.company_id != role.company_id for item in role.included_role_ids):
                raise ValidationError(_("Included roles must belong to the same company."))
            for profile in role.profile_ids:
                if profile.apply_to_all_companies or set(profile.company_ids.ids) != {role.company_id.id}:
                    raise ValidationError(_("A role's restriction profiles must be scoped only to its company; disable 'Apply to all companies' on the profile first."))
            forbidden = self.env.ref("base.group_portal") | self.env.ref("base.group_public")
            if role._expanded_groups() & forbidden:
                raise ValidationError(_("Internal access roles cannot grant Portal or Public user types."))

    @api.model_create_multi
    def create(self, vals_list):
        _assert_manager(self.env)
        _lock_governance_users(self.env)
        for vals in vals_list:
            _assert_company(self.env, self.env["res.company"].browse(vals.get("company_id") or self.env.company.id))
        records = super().create(vals_list)
        records._assert_grantable()
        for record in records:
            self.env["eh.access.audit"]._record("role_created", record, details={"groups": record.group_ids.ids, "profiles": record.profile_ids.ids})
        return records

    def write(self, vals):
        _assert_manager(self.env)
        _record_access(self, "write")
        _lock_governance_users(self.env)
        for role in self:
            _assert_company(self.env, role.company_id)
        if "company_id" in vals:
            _assert_company(self.env, self.env["res.company"].browse(vals["company_id"]))
            if self.env["eh.access.assignment"].sudo().search_count([("role_id", "in", self.ids)]):
                raise UserError(_("A role with assignment history cannot change company."))
        result = super().write(vals)
        self._assert_grantable()
        # Included-role edits affect assignments to ancestor roles too.
        self.search([])._check_configuration()
        for role in self:
            self.env["eh.access.audit"]._record("role_changed", role, details={"changes": vals})
        self.env["eh.access.assignment"]._synchronize_all()
        return result

    def unlink(self):
        _assert_manager(self.env)
        _record_access(self, "unlink")
        _lock_governance_users(self.env)
        for role in self:
            _assert_company(self.env, role.company_id)
        if self.env["eh.access.assignment"].sudo().search_count([("role_id", "in", self.ids)]):
            raise UserError(_("Archive a role with assignment history instead of deleting it."))
        if self.sudo().search_count([("included_role_ids", "in", self.ids)]):
            raise UserError(_("Remove this role from its parent roles before deleting it."))
        for role in self:
            self.env["eh.access.audit"]._record("role_deleted", role)
        return super().unlink()


class EhAccessAssignment(models.Model):
    _name = "eh.access.assignment"
    _description = "Access Studio Role Assignment"
    _order = "id desc"
    _rec_name = "role_id"

    role_id = fields.Many2one("eh.access.role", required=True, ondelete="restrict", index=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="restrict", index=True, domain=[("share", "=", False)])
    company_id = fields.Many2one(related="role_id.company_id", store=True, index=True)
    starts_at = fields.Datetime(required=True, default=fields.Datetime.now)
    ends_at = fields.Datetime(help="Exclusive expiry. Reconciliation runs every minute; scheduler outages delay revocation.")
    reason = fields.Text(required=True)
    request_id = fields.Many2one("eh.access.request", readonly=True, ondelete="restrict", index=True)
    granted_by_id = fields.Many2one("res.users", readonly=True, required=True, default=lambda self: self.env.user, ondelete="restrict")
    revoked = fields.Boolean(readonly=True, default=False)
    revoked_at = fields.Datetime(readonly=True)
    revoked_by_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    applied_state = fields.Selection([("scheduled", "Scheduled"), ("active", "Active"), ("expired", "Expired"), ("revoked", "Revoked"), ("suspended", "Suspended")],
                                    readonly=True, default="scheduled", string="Last Applied State")

    @api.constrains("starts_at", "ends_at", "user_id", "role_id")
    def _check_assignment(self):
        for assignment in self:
            if assignment.ends_at and assignment.ends_at <= assignment.starts_at:
                raise ValidationError(_("Expiry must be later than the start time."))
            if assignment.user_id.id == SUPERUSER_ID or assignment.user_id.share:
                raise ValidationError(_("Roles can only be assigned to ordinary internal users."))
            if assignment.company_id not in assignment.user_id.company_ids:
                raise ValidationError(_("The user must have access to the role's company."))
            if not (assignment.reason or "").strip():
                raise ValidationError(_("Provide a reason for the assignment."))

    def _current_state(self, now):
        self.ensure_one()
        if self.revoked:
            return "revoked"
        if self.ends_at and self.ends_at <= now:
            return "expired"
        if self.starts_at > now:
            return "scheduled"
        if not self.role_id.active or not self.user_id.active or self.user_id.share or self.company_id not in self.user_id.company_ids:
            return "suspended"
        return "active"

    @api.model_create_multi
    def create(self, vals_list):
        _assert_manager(self.env)
        _lock_governance_users(self.env, [values["user_id"] for values in vals_list if values.get("user_id")])
        safe_vals = []
        for vals in vals_list:
            vals = dict(vals)
            role = self.env["eh.access.role"].browse(vals.get("role_id"))
            role._assert_grantable()
            defaults = {"granted_by_id": self.env.uid, "revoked": False, "revoked_at": False,
                        "revoked_by_id": False, "applied_state": "scheduled", "company_id": role.company_id.id}
            for name, expected in defaults.items():
                if name in vals:
                    if vals.pop(name) != expected:
                        raise AccessError(_("Assignment provenance is managed by Access Studio."))
            if vals.get("request_id") and self.env.context.get("_eh_governance_token") is not _GOVERNANCE_TOKEN:
                raise AccessError(_("Assignment provenance is managed by Access Studio."))
            if not role.active:
                raise UserError(_("An archived role cannot be assigned."))
            vals["granted_by_id"] = self.env.uid
            safe_vals.append(vals)
        records = super().create(safe_vals)
        for assignment in records:
            self.env["eh.access.audit"]._record("assignment_created", assignment, assignment.user_id,
                                               {"role_id": assignment.role_id.id, "request_id": assignment.request_id.id, "starts_at": assignment.starts_at, "ends_at": assignment.ends_at, "reason": assignment.reason})
        records._synchronize_users(records.user_id)
        return records

    def write(self, vals):
        if self.env.context.get("_eh_governance_token") is _GOVERNANCE_TOKEN:
            return super().write(vals)
        _assert_manager(self.env)
        _record_access(self, "write")
        _lock_governance_users(self.env, self.user_id.ids)
        if set(vals) - {"starts_at", "ends_at", "reason"}:
            raise AccessError(_("Role, recipient and provenance cannot be changed. Revoke this assignment and create another."))
        for assignment in self:
            assignment.role_id._assert_grantable()
            if assignment.revoked or assignment.applied_state == "expired":
                raise UserError(_("Create a new assignment to renew expired or revoked access."))
        result = super().write(vals)
        for assignment in self:
            self.env["eh.access.audit"]._record("assignment_changed", assignment, assignment.user_id, {"changes": vals})
        self._synchronize_users(self.user_id)
        return result

    def unlink(self):
        raise AccessError(_("Assignment history cannot be deleted. Revoke the assignment instead."))

    def action_revoke(self):
        _assert_manager(self.env)
        _record_access(self, "write")
        _lock_governance_users(self.env, self.user_id.ids)
        for assignment in self:
            _assert_company(self.env, assignment.company_id)
        records = self.filtered(lambda record: not record.revoked)
        records.with_context(_eh_governance_token=_GOVERNANCE_TOKEN).write({
            "revoked": True, "revoked_at": fields.Datetime.now(), "revoked_by_id": self.env.uid,
        })
        for assignment in records:
            self.env["eh.access.audit"]._record("assignment_revoked", assignment, assignment.user_id)
        self._synchronize_users(records.user_id)
        return True

    @api.model
    def _synchronize_all(self):
        assignments = self.sudo().search([])
        users = assignments.user_id | self.env["eh.access.grant"].sudo().search([]).user_id
        self._synchronize_users(users)

    @api.model
    def _synchronize_users(self, users):
        """All grant mutations serialize on each user; one ledger owns each link."""
        if not users:
            return
        _lock_governance_users(self.env, users.ids)
        context = dict(self.env.context, _eh_governance_token=_GOVERNANCE_TOKEN, tracking_disable=True)
        elevated = self.sudo().with_context(context)
        Ledger = self.env["eh.access.grant"].sudo().with_context(context)
        now = fields.Datetime.now()
        gfield = _group_field(self.env)
        _refresh(users, [gfield, "access_profile_ids", "active", "company_ids", "share"])
        for user in users.sudo().with_context(context):
            assignments = elevated.search([("user_id", "=", user.id)])
            _refresh(assignments)
            wanted = {}
            for assignment in assignments:
                state = assignment._current_state(now)
                if state != assignment.applied_state:
                    assignment.write({"applied_state": state})
                    self.env["eh.access.audit"]._record("assignment_" + state, assignment, user)
                if state != "active":
                    continue
                roles = assignment.role_id._active_roles()
                for group in _group_closure(roles.group_ids):
                    wanted.setdefault(("group", group.id), []).append(assignment.id)
                for profile in roles.profile_ids:
                    wanted.setdefault(("profile", profile.id), []).append(assignment.id)
            current_groups = set(user[gfield].ids)
            current_profiles = set(user.access_profile_ids.ids)
            ledgers = Ledger.search([("user_id", "=", user.id)])
            _refresh(ledgers)
            existing = {(line.kind, line.group_id.id if line.kind == "group" else line.profile_id.id): line for line in ledgers}
            for (kind, record_id), owners in wanted.items():
                if (kind, record_id) not in existing:
                    line = Ledger.create({"user_id": user.id, "kind": kind,
                                          "group_id" if kind == "group" else "profile_id": record_id,
                                          "manual": record_id in (current_groups if kind == "group" else current_profiles)})
                    existing[(kind, record_id)] = line
            # Older Odoo versions materialize implied memberships in the same
            # relation as direct groups. Preserve groups still implied by any
            # independently held group, including a group added after a role.
            role_owned = {record_id for (kind, record_id), line in existing.items()
                          if kind == "group" and not line.manual}
            independent = self.env["res.groups"].sudo().browse(list(current_groups - role_owned))
            manual_implied = set(_group_closure(independent).ids)
            # Modern Odoo computes implied rights separately. Retaining an
            # owned direct link merely because another group implies it
            # would turn it permanent once our ledger is uninstalled.
            materializes_implied = "all_group_ids" not in user._fields
            group_commands = []
            profile_commands = []
            for (kind, record_id), line in existing.items():
                owners = wanted.get((kind, record_id), [])
                if set(line.assignment_ids.ids) != set(owners):
                    line.write({"assignment_ids": [(6, 0, owners)]})
                should_exist = bool(line.manual or owners or (kind == "group" and materializes_implied and record_id in manual_implied))
                current = current_groups if kind == "group" else current_profiles
                if should_exist != (record_id in current):
                    (group_commands if kind == "group" else profile_commands).append((4 if should_exist else 3, record_id))
            if group_commands:
                user.write({gfield: group_commands})
            # Write through the profile model so its existing safety checks run.
            for command, profile_id in profile_commands:
                self.env["eh.access.profile"].sudo().with_context(context).browse(profile_id).write({"user_ids": [(command, user.id)]})

    @api.model
    def _cron_reconcile_governance(self):
        self._synchronize_all()

    @api.model
    def _uninstall_cleanup(self):
        """Release owned links while assignments and the ledger still exist.

        The module loader calls this before deleting our models. Using the
        ordinary revocation/reconciliation path preserves independently held
        and implied memberships; removing every role group would not.
        """
        if not self.env.su:
            raise AccessError(_("Only the module loader may clean up role grants."))
        _lock_governance_users(self.env)
        # An older installed version may be removed immediately after its
        # package is replaced, without first creating the new role tables.
        self.env.cr.execute("SELECT to_regclass('eh_access_assignment'), to_regclass('eh_access_grant')")
        assignment_table, ledger_table = self.env.cr.fetchone()
        if not assignment_table and not ledger_table:
            return
        if not assignment_table or not ledger_table:
            raise UserError(_("The grant ownership tables are incomplete. Repair the module before uninstalling so temporary permissions can be removed safely."))
        assignments = self.with_context(active_test=False).search([("revoked", "=", False)])
        assignments.action_revoke()
        # Includes ledgers whose assignments already expired or were revoked.
        # Repeated cleanup is safe and cannot leave a stale owned link behind.
        self._synchronize_all()


class EhAccessGrant(models.Model):
    _name = "eh.access.grant"
    _description = "Access Studio Grant Ownership"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    kind = fields.Selection([("group", "Native Group"), ("profile", "Restriction Profile")], required=True)
    group_id = fields.Many2one("res.groups", ondelete="cascade", index=True)
    profile_id = fields.Many2one("eh.access.profile", ondelete="cascade", index=True)
    manual = fields.Boolean(string="Independent Manual Membership", readonly=True)
    assignment_ids = fields.Many2many("eh.access.assignment", "eh_access_grant_assignment_rel", "grant_id", "assignment_id", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("_eh_governance_token") is not _GOVERNANCE_TOKEN:
            raise AccessError(_("Grant ownership is maintained by Access Studio."))
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get("_eh_governance_token") is not _GOVERNANCE_TOKEN:
            raise AccessError(_("Grant ownership is maintained by Access Studio."))
        return super().write(vals)

    def unlink(self):
        raise AccessError(_("Grant ownership cannot be deleted manually."))

    @api.model
    def _set_manual(self, user, kind, intent):
        for record_id, present in intent.items():
            lines = self.sudo().search([("user_id", "=", user.id), ("kind", "=", kind),
                                        ("group_id" if kind == "group" else "profile_id", "=", record_id)])
            if lines:
                lines.with_context(_eh_governance_token=_GOVERNANCE_TOKEN).write({"manual": present})


class ResUsersGovernance(models.Model):
    _inherit = "res.users"

    def write(self, vals):
        gfield = _group_field(self.env)
        governed = {gfield, "access_profile_ids", "company_ids", "active"} & set(vals)
        if not governed or self.env.context.get("_eh_governance_token") is _GOVERNANCE_TOKEN:
            return super().write(vals)
        _lock_governance_users(self.env, self.ids)
        _refresh(self, [gfield, "access_profile_ids"])
        before = {user.id: (set(user.sudo()[gfield].ids), set(user.sudo().access_profile_ids.ids)) for user in self}
        result = super().write(vals)
        Ledger = self.env["eh.access.grant"]
        for user in self:
            if gfield in vals:
                Ledger._set_manual(user, "group", _manual_intent(before[user.id][0], vals[gfield]))
            if "access_profile_ids" in vals:
                Ledger._set_manual(user, "profile", _manual_intent(before[user.id][1], vals["access_profile_ids"]))
        self.env["eh.access.assignment"]._synchronize_users(self)
        return result


class EhAccessProfileGovernance(models.Model):
    _inherit = "eh.access.profile"

    def write(self, vals):
        if self.env.context.get("_eh_governance_token") is _GOVERNANCE_TOKEN:
            return super().write(vals)
        before = {}
        if {"company_ids", "apply_to_all_companies"} & set(vals):
            _lock_governance_users(self.env)
        if "user_ids" in vals:
            _lock_governance_users(self.env)
            _refresh(self, ["user_ids"])
            locked_ids = set(self.sudo().user_ids.ids)
            for command in vals["user_ids"]:
                if command[0] in (3, 4):
                    locked_ids.add(command[1])
                elif command[0] == 6:
                    locked_ids.update(command[2])
            if locked_ids:
                _lock_governance_users(self.env, locked_ids)
            before = {profile.id: set(profile.sudo().user_ids.ids) for profile in self}
        result = super().write(vals)
        if {"company_ids", "apply_to_all_companies"} & set(vals):
            self.env["eh.access.role"].sudo().search([("profile_ids", "in", self.ids)])._check_configuration()
        if "user_ids" in vals:
            users = self.env["res.users"].browse()
            for profile in self:
                intent = _manual_intent(before[profile.id], vals["user_ids"])
                for user_id, present in intent.items():
                    user = self.env["res.users"].browse(user_id)
                    users |= user
                    self.env["eh.access.grant"]._set_manual(user, "profile", {profile.id: present})
            self.env["eh.access.assignment"]._synchronize_users(users)
        return result


class ResGroupsGovernance(models.Model):
    _inherit = "res.groups"

    def write(self, vals):
        member_field = next((name for name in ("users", "user_ids") if name in self._fields and name in vals), None)
        hierarchy_change = {"implied_ids", "implied_by_ids"} & set(vals)
        if self.env.context.get("_eh_governance_token") is _GOVERNANCE_TOKEN or not (member_field or hierarchy_change):
            return super().write(vals)
        _record_access(self, "write")
        _lock_governance_users(self.env)
        _refresh(self)
        affected = self.env["res.users"].browse()
        if member_field:
            affected |= self.sudo()[member_field]
            for command in vals[member_field]:
                if command[0] in (2, 3, 4):
                    affected |= self.env["res.users"].browse(command[1])
                elif command[0] == 6:
                    affected |= self.env["res.users"].browse(command[2])
        if hierarchy_change:
            # Existing effective users and scheduled recipients can all be
            # affected by a hierarchy edit. Lock them before changing it.
            effective_field = next((name for name in ("all_user_ids", "users", "user_ids") if name in self._fields), None)
            if effective_field:
                affected |= self.sudo()[effective_field]
            affected |= self.env["eh.access.assignment"].sudo().search([]).user_id
            affected |= self.env["eh.access.grant"].sudo().search([]).user_id
        _lock_governance_users(self.env, affected.ids)
        before = {group.id: set(group.sudo()[member_field].ids) for group in self} if member_field else {}
        result = super(ResGroupsGovernance, self.with_context(_eh_governance_token=_GOVERNANCE_TOKEN)).write(vals)
        users = self.env["res.users"].browse()
        for group in self if member_field else []:
            for user_id, present in _manual_intent(before[group.id], vals[member_field]).items():
                user = self.env["res.users"].browse(user_id)
                users |= user
                self.env["eh.access.grant"]._set_manual(user, "group", {group.id: present})
        if hierarchy_change:
            self.env["eh.access.assignment"]._synchronize_all()
        elif users:
            self.env["eh.access.assignment"]._synchronize_users(users)
        return result
