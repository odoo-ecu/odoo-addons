# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: profile-template wizard.

Bootstraps a new profile from one of a few well-tested scenarios that
cover the bulk of customer requests:

* read_only          restrict existing business access to read-only
* own_records_only   read / edit own records on res.partner-style
                     models keyed on user_id
* vendor_portal      own records on a selected model, fewer interface
                     controls; this does not provision a portal
* auditor            dated read-only restriction, not an expiring grant

Each template is a small data factory: it knows the field bag to
write on eh.access.profile and the lines to attach. Template logic
lives in code (not data) so it can reference Odoo records (admin
groups, model lookups) without depending on XML data ordering.
Every generated profile starts inactive for review.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


TEMPLATE_OPTIONS = [
    ("read_only", "Read-only across the system"),
    ("own_records_only", "Sales rep: own records only"),
    ("vendor_portal", "Contractor: own records with fewer interface controls"),
    ("auditor", "Auditor: dated read-only restriction"),
]


class EhAccessTemplateWizard(models.TransientModel):
    _name = "eh.access.template.wizard"
    _description = "User Access Studio Template Wizard"

    template = fields.Selection(
        TEMPLATE_OPTIONS,
        required=True,
        default="own_records_only",
    )
    name = fields.Char(
        string="Profile name",
        required=True,
        help="The new profile is created with this name.",
    )
    user_ids = fields.Many2many(
        "res.users",
        string="Apply to users",
        help="Users assigned to the new profile.",
    )
    target_model = fields.Char(
        string="Target model (own-records template)",
        default=lambda self: "sale.order" if "sale.order" in self.env else "res.partner",
        help=(
            "For the 'own records only' template: the model whose"
            " records the user should be limited to. The template"
            " stamps a domain of [('user_id', '=', '__uid__')] on"
            " this model."
        ),
    )
    target_model_id = fields.Many2one(
        "ir.model", string="Document type", compute="_compute_target_model_id",
        inverse="_inverse_target_model_id", ondelete="cascade",
    )
    company_ids = fields.Many2many("res.company", string="Companies", default=lambda self: self.env.company)
    apply_to_all_companies = fields.Boolean(default=False)
    duration_days = fields.Integer(
        string="Duration in days (auditor template)",
        default=30,
        help=(
            "For the 'auditor' template: sets the end date this many days"
            " after draft creation. Review the dates before activation. At"
            " expiry the restriction ends; native group access is not revoked."
        ),
    )

    description = fields.Text(
        compute="_compute_description",
        readonly=True,
    )

    @api.depends("target_model")
    def _compute_target_model_id(self):
        for wizard in self:
            wizard.target_model_id = self.env["ir.model"].search([("model", "=", wizard.target_model)], limit=1)

    def _inverse_target_model_id(self):
        for wizard in self:
            wizard.target_model = wizard.target_model_id.model

    @api.depends("template", "target_model", "duration_days")
    def _compute_description(self):
        descriptions = {
            "read_only": _(
                "Prepares a read-only restriction on existing business access."
                " This grants no new access. Framework exceptions such as"
                " messaging remain available. Administrators are exempt."
            ),
            "own_records_only": _(
                "Prepares a profile with a domain rule on the chosen"
                " model that limits read / edit to records where"
                " user_id equals the current user. Hides the export"
                " button on every model."
            ),
            "vendor_portal": _(
                "Prepares own-record read-only access on the selected document"
                " type, hides Settings and import/export/spreadsheet controls,"
                " and disables developer mode. This is not a portal account"
                " or an app allowlist; other existing access remains."
            ),
            "auditor": _(
                "Prepares a dated read-only restriction with developer mode"
                " disabled. At expiry this restriction ends: underlying"
                " Odoo permissions remain, including any write access."
                " This does not create or revoke a temporary access grant."
            ),
        }
        for record in self:
            record.description = descriptions.get(record.template, "") + _(
                "\nThe profile is created inactive. Review its users, company scope"
                " and rules before activating it."
            )

    def action_create_profile(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("eh_access_studio.group_eh_access_manager"):
            raise AccessError(_("Only Access Studio managers may create profiles from templates."))
        if not self.name:
            raise UserError(_("Pick a name for the new profile."))
        builder = getattr(self, "_build_" + self.template, None)
        if builder is None:
            raise UserError(_("Unknown template: %(t)s.", t=self.template))
        if not self.apply_to_all_companies and not self.company_ids:
            raise UserError(_("Choose the companies this restriction applies to."))
        if not self.env.su and self.company_ids - self.env.user.company_ids:
            raise AccessError(_("Choose only companies you can enter."))
        vals = builder()
        profile = self.env["eh.access.profile"].create(vals)
        return {
            "type": "ir.actions.act_window",
            "res_model": "eh.access.profile",
            "view_mode": "form",
            "res_id": profile.id,
            "target": "current",
        }

    # -- builders -------------------------------------------------------

    def _base_vals(self):
        return {
            "name": self.name,
            "user_ids": [(6, 0, self.user_ids.ids)],
            "active": False,
            "apply_to_all_companies": self.apply_to_all_companies,
            "company_ids": [(6, 0, self.company_ids.ids)],
        }

    def _build_read_only(self):
        vals = self._base_vals()
        vals.update({"readonly": True})
        return vals

    def _validated_owner_model(self):
        IrModel = self.env["ir.model"]
        target = IrModel.search([("model", "=", self.target_model)], limit=1)
        if not target or target.model not in self.env:
            raise UserError(_(
                "Model %(m)s does not exist in this database.",
                m=self.target_model,
            ))
        owner = self.env[target.model]._fields.get("user_id")
        if not owner or owner.type != "many2one" or owner.comodel_name != "res.users":
            raise UserError(_("Choose a document type with a responsible user (user_id), such as Contacts or Sales Orders."))
        return target

    def _build_own_records_only(self):
        vals = self._base_vals()
        target = self._validated_owner_model()
        vals.update({
            "hide_export": True,
            "domain_line_ids": [(0, 0, {
                "model_id": target.id,
                "read_right": True,
                "write_right": True,
                "create_right": False,
                "delete_right": False,
                "apply_filter": True,
                "domain": '[("user_id", "=", "__uid__")]',
            })],
        })
        return vals

    def _build_vendor_portal(self):
        vals = self._base_vals()
        sale_model = self._validated_owner_model()
        settings_menu = self.env.ref("base.menu_administration", raise_if_not_found=False)
        line_specs = []
        if sale_model:
            line_specs.append((0, 0, {
                "model_id": sale_model.id,
                "read_right": True,
                "create_right": False,
                "write_right": False,
                "delete_right": False,
                "apply_filter": True,
                "domain": '[("user_id", "=", "__uid__")]',
            }))
        vals.update({
            "hide_export": True,
            "hide_import": True,
            "hide_spreadsheet": True,
            "disable_debug_mode": True,
            "hidden_menu_ids": [(6, 0, settings_menu.ids if settings_menu else [])],
            "domain_line_ids": line_specs,
        })
        return vals

    def _build_auditor(self):
        vals = self._base_vals()
        if self.duration_days < 1:
            raise UserError(_("Choose a duration of at least one day."))
        days = self.duration_days
        today = fields.Date.context_today(self)
        vals.update({
            "readonly": True,
            "disable_debug_mode": True,
            "date_from": today,
            "date_until": today + timedelta(days=days),
        })
        return vals
