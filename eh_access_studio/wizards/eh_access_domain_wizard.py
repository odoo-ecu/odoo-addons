# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""A standard domain editor plus typed runtime choices, never fake record IDs."""
import ast

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class EhAccessDomainWizard(models.TransientModel):
    _name = "eh.access.domain.wizard"
    _description = "Access Studio Guided Record Filter"

    profile_id = fields.Many2one("eh.access.profile", required=True, ondelete="cascade")
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade", string="Document type")
    model_name = fields.Char(related="model_id.model", readonly=True)
    scope = fields.Selection([
        ("all", "All records matching the additional filter"),
        ("own", "Current user's records"),
        ("team", "Current user and their reporting team"),
        ("subordinates", "Reporting team, excluding current user"),
    ], default="own", required=True, string="Whose records?")
    owner_field_id = fields.Many2one("ir.model.fields", ondelete="cascade", string="Responsible user field",
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one'), ('relation', '=', 'res.users')]")
    rights = fields.Selection([("read_only", "Read only"), ("read_write", "Read and edit"),
                              ("read_create_write", "Read, create and edit"), ("full", "Read, create, edit and delete")],
                             required=True, default="read_write", string="Allowed operations")
    additional_domain = fields.Char(default="[]", string="Additional filter")
    date_field_id = fields.Many2one("ir.model.fields", ondelete="cascade", string="Date field",
        domain="[('model_id', '=', model_id), ('ttype', '=', 'date')]",
        help="Calendar presets use Date fields. Use the additional filter for explicit date-time boundaries.")
    period = fields.Selection([("none", "No date filter"), ("month", "This month"),
                               ("year", "This year"), ("last30", "Last 30 days")],
                              default="none", required=True, string="Date range")
    soft_restrict = fields.Boolean(string="Read outside this filter; limit changes to matching records")
    preview = fields.Text(compute="_compute_preview", string="Generated filter", readonly=True)

    def _require_manager(self):
        if not self.env.su and not self.env.user.has_group("eh_access_studio.group_eh_access_manager"):
            raise AccessError(_("Only Access Studio managers may create record filters."))

    @api.onchange("model_id")
    def _onchange_model(self):
        self.owner_field_id = self.env["ir.model.fields"].search([
            ("model_id", "=", self.model_id.id), ("name", "=", "user_id"),
            ("ttype", "=", "many2one"), ("relation", "=", "res.users"),
        ], limit=1)
        self.date_field_id = False
        self.additional_domain = "[]"

    def _build_domain(self):
        self.ensure_one()
        if not self.model_id or self.model_name not in self.env:
            raise UserError(_("Choose an installed document type."))
        domain = []
        if self.scope != "all":
            owner = self.owner_field_id
            if not owner or owner.model_id != self.model_id or owner.ttype != "many2one" or owner.relation != "res.users":
                raise UserError(_("Choose a responsible-user field on this document type."))
            if self.scope in ("team", "subordinates") and "hr.employee" not in self.env:
                raise UserError(_("Reporting-team filters need the Employees app and its manager hierarchy. Choose current user's records until that is configured."))
            token = {"own": "__uid__", "team": "__team_uids__", "subordinates": "__subordinate_uids__"}[self.scope]
            domain.append((owner.name, "=" if self.scope == "own" else "in", token))
        if self.period != "none":
            date_field = self.date_field_id
            if not date_field or date_field.model_id != self.model_id or date_field.ttype != "date":
                raise UserError(_("Choose a Date field on this document type. Use the additional filter for date-time boundaries."))
            start, end = {"month": ("__month_start__", "__month_end__"), "year": ("__year_start__", "__year_end__"),
                          "last30": ("__last_30_days__", "__today__")}[self.period]
            # DateTime end boundaries need an exclusive next-day value. These
            # presets only select Date fields; the domain widget remains
            # available for explicit DateTime conditions.
            domain.extend([(date_field.name, ">=", start), (date_field.name, "<=", end)])
        try:
            extra = ast.literal_eval(self.additional_domain or "[]")
        except (SyntaxError, ValueError) as error:
            raise UserError(_("The additional filter is not a valid domain.")) from error
        if not isinstance(extra, list):
            raise UserError(_("The additional filter must be a domain list."))
        # Keep runtime tokens outside the visual widget. Ordinary domain
        # editing remains a native Odoo feature and never resolves sentinel
        # strings as relation IDs.
        if extra:
            domain = ["&"] + domain + extra if domain else extra
        return domain

    @api.depends("model_id", "scope", "owner_field_id", "additional_domain", "date_field_id", "period")
    def _compute_preview(self):
        for wizard in self:
            try:
                wizard.preview = repr(wizard._build_domain())
            except UserError as error:
                wizard.preview = str(error)

    def action_apply(self):
        self.ensure_one()
        self._require_manager()
        if self.profile_id.active:
            raise UserError(_("Use an inactive draft copy and keep the live profile active while reviewing this change. This builder does not edit an active policy."))
        if self.profile_id.domain_line_ids.filtered(lambda line: line.model_id == self.model_id):
            raise UserError(_("This profile already has a filter for this document type. Edit that rule instead of adding a second rule that could widen access."))
        domain = self._build_domain()
        self.env["eh.access.domain"].create({
            "profile_id": self.profile_id.id, "model_id": self.model_id.id,
            "read_right": True, "write_right": self.rights != "read_only",
            "create_right": self.rights in ("read_create_write", "full"), "delete_right": self.rights == "full",
            "apply_filter": bool(domain), "domain": repr(domain), "soft_restrict": self.soft_restrict,
        })
        return {"type": "ir.actions.act_window", "res_model": "eh.access.profile", "res_id": self.profile_id.id,
                "view_mode": "form", "views": [(False, "form")], "target": "current"}
