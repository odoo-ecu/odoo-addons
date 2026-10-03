# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: res.partner's superuser pre-write.

Core res.partner.write takes `is_company` out of the payload and writes
it as SUPERUSER before the rest (addons/base/models/res_partner.py, "To
write in SUPERUSER on field is_company"). Every gate in this module
stands aside for the superuser, so a field lock on is_company, and any
conditional lock that depends on it, never saw that write.

This override sits above core in the method resolution order, so it
sees the whole payload before the split and checks it as the real user,
conditions included.
"""
from odoo import models

from .base_model import _eh_rpc_entry


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _eh_access_studio_closure(self, hidden):
        result = set(super()._eh_access_studio_closure(hidden))
        # Complete Address is a known formatter of several independent
        # values. The generic all-inputs rule deliberately cannot infer
        # this relationship: a street remains confidential even if city
        # and country are visible. Only withhold readonly, non-inverse
        # formatter outputs, never a writable address source.
        address_inputs = {
            "street", "street2", "zip", "city", "state_id", "country_id",
            "parent_id", "commercial_company_name",
        }
        if set(hidden) & address_inputs:
            field = self._fields.get("contact_address")
            if field and field.compute and field.readonly and not field.inverse:
                result.add("contact_address")
        return frozenset(result)

    def write(self, vals):
        if vals and "is_company" in vals and not self.env.su:
            # The WHOLE payload, before the split. Checking is_company on
            # its own let a conditional lock be walked past in a single
            # write: core commits is_company first, so by the time the
            # rest is checked the condition already reads the new value.
            self._eh_access_studio_check_field_writes(dict(vals), "write")
        if (
            vals and "tz" in vals and not self.env.su
            and _eh_rpc_entry() is not None
            and any(partner.tz != vals["tz"] for partner in self.sudo())
        ):
            self._eh_access_studio_check_tz_change(self.sudo().user_ids.ids)
        return super().write(vals)
