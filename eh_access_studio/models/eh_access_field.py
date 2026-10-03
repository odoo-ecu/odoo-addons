# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: per-model field rule line.

A field line attaches to one access profile and one model, and lists
zero or more fields to mark invisible, readonly or required for users
inside that profile.
"""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class EhAccessField(models.Model):
    _name = "eh.access.field"
    _inherit = ["eh.access.line.mixin"]
    _description = "User Access Studio Field Rule"
    _order = "model_id, id"

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
    field_ids = fields.Many2many(
        "ir.model.fields",
        "eh_access_field_field_rel",
        "line_id",
        "field_id",
        string="Fields",
        domain="[('model_id', '=', model_id)]",
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

    invisible = fields.Boolean(
        string="Hidden",
        help="The selected fields are removed from views and masked in supported client/API reads. Hidden values cannot be changed by the affected user; supported exports of hidden fields are refused.",
    )
    readonly = fields.Boolean(
        string="Read-Only",
        help=(
            "The selected fields are shown but cannot be edited. The"
            " framework's force_save flag is set so existing values"
            " survive saves on records the user can otherwise modify."
        ),
    )
    required = fields.Boolean(
        string="Required",
        help=(
            "The selected fields become mandatory. Use sparingly: a"
            " required overlay on a field that the user cannot fill in"
            " (because it lives on a hidden tab) blocks all saves."
        ),
    )
    hide_external_link = fields.Boolean(
        string="Hide External Link",
        help=(
            "For relational fields (many2one / many2many / one2many),"
            " strip the external open / create / edit affordances by"
            " injecting widget options no_open, no_create and no_edit."
            " Has no effect on non-relational fields."
        ),
    )

    @api.constrains("invisible", "readonly", "required", "hide_external_link")
    def _check_at_least_one_effect(self):
        for line in self:
            if not (
                line.invisible
                or line.readonly
                or line.required
                or line.hide_external_link
            ):
                raise ValidationError(_(
                    "Access Studio: a field rule must mark its fields"
                    " hidden, read-only, required or hide the external"
                    " link."
                ))

    @api.constrains("invisible", "readonly", "required")
    def _check_not_invisible_and_required(self):
        for line in self:
            if line.invisible and line.required:
                raise ValidationError(_(
                    "Access Studio: a field cannot be both hidden and"
                    " required. Pick one."
                ))
