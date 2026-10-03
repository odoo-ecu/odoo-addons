# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage
# License LGPL-3.0 or later.
"""Immutable reviewed-policy snapshots and optimistic restore."""
import difflib

from psycopg2 import sql
import yaml

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


def _manager(env):
    if not env.su and not env.user.has_group("eh_access_studio.group_eh_access_manager"):
        raise AccessError(_("Only Access Studio managers can manage policy revisions."))


class EhAccessRevision(models.Model):
    _name = "eh.access.revision"
    _description = "Access Policy Revision"
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(required=True, readonly=True)
    profile_id = fields.Many2one("eh.access.profile", ondelete="set null", readonly=True, index=True)
    profile_name = fields.Char(required=True, readonly=True)
    author_id = fields.Many2one("res.users", readonly=True, ondelete="set null")
    reason = fields.Char(readonly=True)
    schema_version = fields.Integer(readonly=True, required=True, default=2)
    fingerprint = fields.Char(readonly=True, required=True, index=True)
    policy = fields.Json(readonly=True, required=True)
    provenance = fields.Json(readonly=True)
    policy_text = fields.Text(compute="_compute_policy_text")
    provenance_text = fields.Text(compute="_compute_policy_text")

    @api.depends("policy", "provenance")
    def _compute_policy_text(self):
        for revision in self:
            revision.policy_text = yaml.safe_dump(revision.policy or {}, sort_keys=False, allow_unicode=True)
            revision.provenance_text = yaml.safe_dump(revision.provenance, sort_keys=False) if revision.provenance else False

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Revisions are created only by validated policy operations."))

    def write(self, vals):
        raise AccessError(_("Policy revisions are immutable."))

    def unlink(self):
        raise AccessError(_("Policy revisions cannot be deleted through the application."))

    def _policy_fingerprint(self, profile):
        _manager(self.env)
        from ..wizards.eh_access_yaml_wizard import _digest
        profile.ensure_one()
        return _digest(self.env["eh.access.yaml.wizard"]._serialise_profile(profile))

    def _lock_profile(self, profile):
        """Serialize changes against the profile and its existing rule rows."""
        _manager(self.env)
        profile.ensure_one()
        profile._eh_lock_policy()
        self.env.flush_all()
        self.env.cr.execute("SELECT id FROM eh_access_profile WHERE id = %s FOR UPDATE", (profile.id,))
        if not self.env.cr.fetchone():
            raise UserError(_("This profile was deleted. Review the policy again."))
        for table in ("eh_access_field", "eh_access_model", "eh_access_node", "eh_access_chatter", "eh_access_domain"):
            self.env.cr.execute(sql.SQL("SELECT id FROM {} WHERE profile_id = %s FOR UPDATE").format(sql.Identifier(table)), (profile.id,))
        self.env.invalidate_all()

    def _snapshot(self, profile, reason, provenance=None):
        _manager(self.env)
        profile.ensure_one()
        profile._eh_lock_policy()
        from ..wizards.eh_access_yaml_wizard import _digest
        policy = self.env["eh.access.yaml.wizard"]._serialise_profile(profile)
        # Bypass this class's deliberately forbidden public create method.
        # sudo is limited to writing the immutable audit record, after the
        # caller's manager gate and normal policy read checks above.
        return super(EhAccessRevision, self.sudo()).create({
            "name": "%s — %s" % (profile.name, fields.Datetime.now()),
            "profile_id": profile.id, "profile_name": profile.name,
            "author_id": self.env.uid, "reason": str(reason or "Policy snapshot")[:500],
            "schema_version": 2, "fingerprint": _digest(policy), "policy": policy,
            "provenance": provenance or {},
        })

    def _restore(self, expected_fingerprint, confirm_active=False):
        _manager(self.env)
        self.ensure_one()
        if not self.profile_id:
            raise UserError(_("The original profile no longer exists. Export this snapshot and review it as a new import."))
        from ..wizards.eh_access_yaml_wizard import _digest
        if self.schema_version != 2 or _digest(self.policy) != self.fingerprint:
            raise UserError(_("Revision integrity check failed."))
        self._lock_profile(self.profile_id)
        if self._policy_fingerprint(self.profile_id) != expected_fingerprint:
            raise UserError(_("Policy changed after the restore preview. Open a new restore preview."))
        Wizard = self.env["eh.access.yaml.wizard"]
        entry = dict(self.policy)
        # Resolve against the original record, even when it was renamed.
        # Portable external IDs still protect exports, but must not redirect
        # an in-database restore to another profile.
        entry["name"] = self.profile_id.name
        entry.pop("xml_id", None)
        target, vals = Wizard._prepare_entry(entry)
        if target != self.profile_id:
            raise UserError(_("Restore target changed. Review the policy again."))
        active = vals.get("active", target.active)
        if (active or target.active) and not confirm_active:
            raise UserError(_("Confirm changes to active profiles before restoring."))
        # Keep the current profile identity; restoring settings must not
        # rename the role or transfer another profile's external ID.
        with self.env.cr.savepoint():
            self._snapshot(target, "Before restore of revision %s" % self.id)
            target.write(vals)
            self._snapshot(target, "Restored revision %s" % self.id)
        return target

    def action_restore(self):
        _manager(self.env)
        self.ensure_one()
        wizard = self.env["eh.access.restore.wizard"].create({"revision_id": self.id})
        return {"type": "ir.actions.act_window", "res_model": wizard._name,
                "res_id": wizard.id, "view_mode": "form", "target": "new"}


class EhAccessRestoreWizard(models.TransientModel):
    _name = "eh.access.restore.wizard"
    _description = "Review Policy Restore"

    revision_id = fields.Many2one("eh.access.revision", required=True, readonly=True, ondelete="cascade")
    expected_fingerprint = fields.Char(readonly=True)
    summary = fields.Text(readonly=True)
    confirm_active = fields.Boolean(string="Apply changes to active profile")

    @api.model_create_multi
    def create(self, vals_list):
        _manager(self.env)
        prepared = []
        for supplied in vals_list:
            if set(supplied) - {"revision_id", "confirm_active"}:
                raise AccessError(_("Restore preview evidence cannot be supplied."))
            vals = dict(supplied)
            revision = self.env["eh.access.revision"].browse(vals.get("revision_id")).exists()
            if not revision or not revision.profile_id:
                raise UserError(_("Choose a revision whose original profile still exists."))
            current = self.env["eh.access.yaml.wizard"]._serialise_profile(revision.profile_id)
            proposed = dict(revision.policy, name=revision.profile_id.name)
            proposed["xml_id"] = current.get("xml_id")
            vals.update(expected_fingerprint=revision._policy_fingerprint(revision.profile_id), confirm_active=False,
                        summary="\n".join(difflib.unified_diff(
                            yaml.safe_dump(current, sort_keys=True).splitlines(),
                            yaml.safe_dump(proposed, sort_keys=True).splitlines(),
                            fromfile="Current policy", tofile="Revision to restore", lineterm="")) or _("No policy changes."))
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        _manager(self.env)
        if set(vals) - {"confirm_active"}:
            raise AccessError(_("Restore evidence cannot be edited. Open a new preview."))
        return super().write(vals)

    def action_apply(self):
        _manager(self.env)
        self.ensure_one()
        profile = self.revision_id._restore(self.expected_fingerprint, self.confirm_active)
        return {"type": "ir.actions.act_window", "res_model": profile._name,
                "res_id": profile.id, "view_mode": "form", "target": "current"}


class EhAccessProfileRevisionActions(models.Model):
    _inherit = "eh.access.profile"

    def action_save_revision(self):
        _manager(self.env)
        self.ensure_one()
        revision = self.env["eh.access.revision"]._snapshot(self, "Manual snapshot before policy edits")
        return {"type": "ir.actions.act_window", "res_model": "eh.access.revision",
                "res_id": revision.id, "view_mode": "form", "target": "current"}

    def action_view_revisions(self):
        _manager(self.env)
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Policy Revisions"),
                "res_model": "eh.access.revision", "view_mode": "list,form",
                "domain": [("profile_id", "=", self.id)], "target": "current"}
