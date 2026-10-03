# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Read-only access questions, with evidence rather than impersonation."""
import logging

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, UserError

_logger = logging.getLogger(__name__)
OPERATIONS = [("read", "Read"), ("create", "Create"), ("write", "Edit"), ("unlink", "Delete")]


class EhAccessInspector(models.TransientModel):
    _name = "eh.access.inspector"
    _description = "Access Studio Access Inspector"

    user_id = fields.Many2one("res.users", required=True, string="Inspect user")
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade", string="Document type")
    model_name = fields.Char(related="model_id.model", readonly=True)
    record_id = fields.Many2oneReference(model_field="model_name", string="Sample record")
    operation = fields.Selection(OPERATIONS, required=True, default="read")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    decision = fields.Selection([
        ("allowed", "Access checks passed"), ("denied", "Denied"),
        ("conditional", "Model checks passed; record/value checks remain"),
        ("unknown", "Unable to establish access"),
    ], readonly=True)
    result_html = fields.Html(readonly=True, sanitize=True)

    def _require_manager(self):
        if not self.env.su and not self.env.user.has_group("eh_access_studio.group_eh_access_manager"):
            raise AccessError(_("Only Access Studio managers may inspect another user's access."))

    @api.onchange("user_id")
    def _onchange_user(self):
        if self.user_id and self.company_id not in self.user_id.company_ids:
            shared = self.user_id.company_ids & self.env.user.company_ids
            self.company_id = shared[:1]
        self.decision = False
        self.result_html = False

    @api.onchange("model_id", "operation", "company_id", "record_id")
    def _onchange_question(self):
        self.decision = False
        self.result_html = False

    @api.onchange("model_id")
    def _onchange_model(self):
        self.record_id = False

    def _target(self):
        self.ensure_one()
        self._require_manager()
        if not self.user_id or not self.model_id or not self.company_id:
            raise UserError(_("Choose a user, document type and company."))
        if self.company_id not in self.user_id.sudo().company_ids:
            raise UserError(_("The selected user cannot enter this company."))
        if not self.env.su and self.company_id not in self.env.user.company_ids:
            raise AccessError(_("You may inspect only companies you can enter."))
        if self.model_name not in self.env:
            raise UserError(_("This document type is not installed."))
        # Do not inherit RPC defaults, bypass switches, active_ids or timezone
        # overrides from the person asking the question.
        context = {"allowed_company_ids": [self.company_id.id], "active_test": False,
                   "lang": self.user_id.lang or "en_US", "tz": self.user_id.tz or "UTC"}
        target = self.env[self.model_name].with_user(self.user_id).with_context(context).sudo(False)
        if self.record_id and self.operation != "create":
            if not target.sudo().browse(self.record_id).exists():
                raise UserError(_("The sample record no longer exists."))
            target = target.browse(self.record_id)
        return target

    def _evaluate(self, target):
        """Never call a business create/write/unlink method to test it."""
        decision = "allowed" if target and self.operation != "create" else "conditional"
        message = _("The configured access checks passed for this question.")
        try:
            if hasattr(target, "check_access"):
                target.check_access(self.operation)
            else:
                target.check_access_rights(self.operation)
                if target:
                    target.check_access_rule(self.operation)
            # Ask the explicit Studio gate too: an inspector does not execute
            # the business mutation that normally triggers this gate.
            blocked = target._eh_access_studio_operation_blocked(self.operation)
            if blocked:
                decision, message = "denied", str(blocked)
        except (AccessError, MissingError) as error:
            decision, message = "denied", str(error)
        except Exception:
            _logger.exception("Access Studio inspector could not establish %s/%s", self.model_name, self.operation)
            decision, message = "unknown", _("An access check failed unexpectedly. Review the server log; no business operation was executed.")
        return decision, message

    @staticmethod
    def _html_table(title, headings, rows):
        header = Markup("").join(Markup("<th>%s</th>") % escape(x) for x in headings)
        body = Markup("").join(
            Markup("<tr>%s</tr>") % Markup("").join(Markup("<td>%s</td>") % escape(str(x or "—")) for x in row)
            for row in rows
        )
        if not rows:
            body = Markup('<tr><td colspan="%s">%s</td></tr>') % (len(headings), escape(_("None for this question.")))
        return Markup('<h3>%s</h3><table class="table table-sm table-bordered"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>') % (escape(title), header, body)

    def _evidence(self, target):
        user = target.env.user.sudo()
        group_field = "all_group_ids" if "all_group_ids" in user._fields else "groups_id"
        if group_field not in user._fields:
            group_field = "group_ids"
        groups = user[group_field]
        chunks = [self._html_table(_("User and scope"), [_('User'), _('Company'), _('Operation'), _('Sample record')], [
            (user.name, self.company_id.name, dict(OPERATIONS)[self.operation], self.record_id if self.operation != 'create' else _('New record; values not evaluated')),
        ])]
        chunks.append(self._html_table(_("Effective Odoo groups"), [_('Group')], [(g.display_name,) for g in groups]))
        operation_field = "perm_" + self.operation
        if "ir.access" in self.env:
            accesses = self.env["ir.access"].sudo().search([
                ("active", "=", True), ("model_id", "=", self.model_id.id),
                "|", ("group_id", "=", False), ("group_id", "in", groups.ids),
            ])
            letter = {"read": "r", "write": "u", "create": "c", "unlink": "d"}[self.operation]
            rows = [(r.name, r.group_id.display_name or _('Global'), r.kind, r.domain or '[]')
                    for r in accesses if letter in r.operation]
            chunks.append(self._html_table(_("Native access contributions"), [_('Name'), _('Group'), _('Kind'), _('Domain')], rows))
            rule_engine = target.env["ir.access"]
        else:
            acls = self.env["ir.model.access"].sudo().search([
                ("active", "=", True), ("model_id", "=", self.model_id.id),
                "|", ("group_id", "=", False), ("group_id", "in", groups.ids),
            ])
            chunks.append(self._html_table(_("Native model permissions (grants combine)"), [_('Name'), _('Group'), _('Grants operation')], [
                (r.name, r.group_id.display_name or _('Everyone'), _('Yes') if r[operation_field] else _('No grant')) for r in acls
            ]))
            rules = target.env["ir.rule"]._get_rules(self.model_name, self.operation).sudo()
            chunks.append(self._html_table(_("Native record rules (global intersection, group union)"), [_('Name'), _('Scope'), _('Domain')], [
                (r.name, ', '.join(r.groups.mapped('display_name')) if r.groups else _('Global'), r.domain_force or '[]') for r in rules
            ]))
            rule_engine = target.env["ir.rule"]
        if "ir.access" in self.env and hasattr(target, "_access_domain"):
            combined = target._access_domain(self.operation)
        else:
            combined = rule_engine._compute_domain(self.model_name, self.operation)
        chunks.append(self._html_table(_("Combined record filter for this user"), [_('Domain')], [(str(combined),)]))
        profile_ids = target._eh_access_studio_active_profile_ids()
        if profile_ids is None:
            raise UserError(_("The active profile lookup failed."))
        profiles = self.env["eh.access.profile"].sudo().browse(profile_ids)
        chunks.append(self._html_table(_("Active Studio profiles"), [_('Profile'), _('Read-only'), _('Until'), _('Company scope')], [
            (p.name, _('Yes') if p.readonly else _('No'), p.date_until or _('No end date'),
             _('All companies') if p.apply_to_all_companies else ', '.join(p.company_ids.mapped('name'))) for p in profiles
        ]))
        model_lines = profiles.mapped("model_line_ids").filtered(lambda line: line.model_id == self.model_id)
        chunks.append(self._html_table(_("Studio model operation restrictions"), [_('Profile'), _('Blocked operations')], [
            (line.profile_id.name, ', '.join(label for field, label in [
                ('restrict_create', 'Create'), ('restrict_edit', 'Edit'), ('restrict_delete', 'Delete'),
                ('restrict_archive', 'Archive'), ('restrict_duplicate', 'Duplicate'),
                ('restrict_import', 'Import'), ('restrict_export', 'Export')
            ] if line[field]) or _('No operation switches')) for line in model_lines
        ]))
        lines = profiles.mapped("domain_line_ids").filtered(lambda line: line.model_id == self.model_id)
        chunks.append(self._html_table(_("Studio record-rule contributions (union, within native access)"), [_('Profile'), _('Rights'), _('Filter'), _('Read outside filter')], [
            (line.profile_id.name, ', '.join(label for field, label in [('read_right', 'Read'), ('create_right', 'Create'), ('write_right', 'Edit'), ('delete_right', 'Delete')] if line[field]) or _('None'),
             line.domain if line.apply_filter else _('No filter'), _('Yes') if line.soft_restrict else _('No')) for line in lines
        ]))
        field_lines = profiles.mapped("field_line_ids").filtered(lambda line: line.model_id == self.model_id)
        chunks.append(self._html_table(_("Field restrictions (not a proposed-value validation)"), [_('Profile'), _('Fields'), _('Effects'), _('Condition')], [
            (line.profile_id.name, ', '.join(line.field_ids.mapped('field_description')),
             ', '.join(label for field, label in [('invisible', 'Hidden'), ('readonly', 'Read-only'), ('required', 'Required')] if line[field]), line.condition or _('Always')) for line in field_lines
        ]))
        return chunks

    def action_inspect(self):
        self.ensure_one()
        target = self._target()
        decision, message = self._evaluate(target)
        chunks = [Markup("<h2>%s</h2><p>%s</p>") % (escape(dict(self._fields['decision'].selection)[decision]), escape(message))]
        try:
            chunks.extend(self._evidence(target))
        except Exception:
            _logger.exception("Access Studio inspector could not assemble evidence")
            chunks.append(Markup("<p>%s</p>") % escape(_("Some source evidence could not be loaded. Review the server log.")))
            if decision != "denied":
                decision = "unknown"
                chunks[0] = Markup("<h2>%s</h2><p>%s</p>") % (escape(_("Unable to establish complete evidence")), escape(message))
        chunks.append(Markup("<h3>%s</h3><p>%s</p>") % (escape(_("Limits of this result")), escape(_(
            "This checks current model and sample-record access in the selected company. It does not run create, write, delete, workflow buttons or external calls. "
            "Create values, field changes, constraints, business-method checks and custom execution remain untested. A passed check is not a promise that an arbitrary operation will succeed. "
            "Without a sample record, record-specific permission remains undecided. Group grants and restriction profiles are separate; expiry of a restriction does not revoke native grants."
        ))))
        self.write({"decision": decision, "result_html": Markup("").join(chunks)})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "views": [(False, "form")], "target": "current"}
