# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: node-level rule line.

Hides specific buttons, notebook pages, kanban links, search filters
or search group-by entries by their technical name. Compared to the
competition we deliberately avoid a mirror table populated by parsing
every model's arch on demand. We hold the technical names directly
and, when the arch is rendered, look them up O(1).

The 'kind' field tells the post-processor which arch tag to match.
"""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


NODE_KINDS = [
    ("button", "Button (object / action)"),
    ("page", "Notebook page / tab"),
    ("link", "Kanban link"),
    ("filter", "Search filter"),
    ("group", "Search group-by"),
]


class EhAccessNode(models.Model):
    _name = "eh.access.node"
    _inherit = ["eh.access.line.mixin"]
    _description = "User Access Studio Node Rule"
    _order = "model_name, kind, id"

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
    kind = fields.Selection(
        NODE_KINDS,
        string="Kind",
        required=True,
        default="button",
    )
    target_name = fields.Char(
        string="Technical Name",
        required=True,
        help=(
            "The 'name' attribute of the target node in XML. For example:"
            " 'action_view_orders' for a smart button."
        ),
    )
    condition = fields.Char(
        string="Only when",
        help=(
            "Optional. Apply this rule only to records that match a"
            " condition, written as a Python expression over the"
            " record's own fields, exactly as Odoo writes view"
            " modifiers. For example: state == 'posted', or"
            " partner_id and amount_total > 1000. Leave empty to apply"
            " the rule to every record of the model."
        ),
    )
    target_label = fields.Char(
        string="Label",
        help="Optional label shown only in this configuration screen.",
    )

    @api.constrains("target_name")
    def _check_target_name(self):
        for record in self:
            if not record.target_name or not record.target_name.strip():
                raise ValidationError(_(
                    "Access Studio: a node rule must have a technical"
                    " name."
                ))
