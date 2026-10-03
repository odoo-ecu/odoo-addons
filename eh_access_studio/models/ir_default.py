# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: a personal default is a way of setting a field.

Any internal user may keep personal defaults (ir.default rows with their
own user_id), and ir.default.set is callable over RPC. Creating a record
then fills the field from that default through default_get, so a user
could choose the value of a field their profile locks simply by making
it their default and leaving it out of the payload. A default for a
field the user may not set is refused when it is saved.
"""
from odoo import api, models, _
from odoo.exceptions import AccessError


class IrDefault(models.Model):
    _inherit = "ir.default"

    @api.model_create_multi
    def create(self, vals_list):
        self._eh_access_studio_check_defaults(
            [(vals or {}).get("field_id") for vals in vals_list],
        )
        return super().create(vals_list)

    def write(self, vals):
        if vals and {"field_id", "json_value"} & set(vals):
            field_ids = (
                [vals["field_id"]] if vals.get("field_id")
                else self.sudo().mapped("field_id").ids
            )
            self._eh_access_studio_check_defaults(field_ids)
        return super().write(vals)

    def _eh_access_studio_check_defaults(self, field_ids):
        if self.env.su or self.env.uid == 1:
            return
        field_ids = [field_id for field_id in field_ids if field_id]
        if not field_ids:
            return
        for field in self.env["ir.model.fields"].sudo().browse(field_ids):
            if field.model not in self.env:
                continue
            target = self.env[field.model]
            rules = target._eh_access_studio_enforced_rules()
            bucket = rules.get(field.name) or {}
            if bucket.get("readonly") or bucket.get("invisible"):
                raise AccessError(_(
                    "Your access profile does not allow you to set"
                    " %(field)s on %(model)s, so it cannot be your default"
                    " either.",
                    field=field.field_description or field.name,
                    model=target._description or field.model,
                ))
