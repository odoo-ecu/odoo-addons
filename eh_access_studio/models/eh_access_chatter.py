# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: per-model chatter override.

Removes the chatter widget from a specific model's form view. Pair with
the profile-level `hide_chatter` boolean to remove chatter system-wide.
"""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class EhAccessChatter(models.Model):
    _name = "eh.access.chatter"
    _inherit = ["eh.access.line.mixin"]
    _description = "User Access Studio Chatter Rule"
    _order = "model_name"

    profile_id = fields.Many2one(
        "eh.access.profile",
        string="Profile",
        required=True,
        ondelete="cascade",
        index=True,
    )
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        required=True,
        ondelete="cascade",
        index=True,
    )
    model_name = fields.Char(
        string="Model Technical Name",
        related="model_id.model",
        store=True,
        readonly=True,
        index=True,
    )

    @api.constrains("profile_id", "model_id")
    def _check_unique_per_profile_model(self):
        for line in self:
            twin = self.search([
                ("profile_id", "=", line.profile_id.id),
                ("model_id", "=", line.model_id.id),
                ("id", "!=", line.id),
            ], limit=1)
            if twin:
                raise ValidationError(_(
                    "Access Studio: model %(model)s already has a"
                    " chatter rule on this profile.",
                    model=line.model_id.display_name,
                ))
