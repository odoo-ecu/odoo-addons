# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: rule-line cache invalidation mixin.

Each child rule line (eh.access.field, eh.access.model, eh.access.node,
eh.access.chatter, eh.access.domain) inherits this mixin so that direct
edits (not going through eh.access.profile.write()) still bump the
ormcaches that depend on the rule set.

Without this mixin, the YAML wizard's [(0, 0, ...)] commands are fine
(they fire profile.write), but a manual line edit through a m2o /
inline view, or an ad-hoc RPC, would bypass invalidation.
"""
from odoo import api, models


class EhAccessLineMixin(models.AbstractModel):
    _name = "eh.access.line.mixin"
    _description = "User Access Studio rule-line cache hook"

    def _eh_access_studio_should_invalidate(self):
        return (
            "eh.access.profile" in self.env
            and not self.env.context.get("eh_access_studio_skip_invalidate")
        )

    @api.model_create_multi
    def create(self, vals_list):
        profiles = self.env["eh.access.profile"].browse([
            vals.get("profile_id") or self.env.context.get("default_profile_id")
            for vals in vals_list
            if vals.get("profile_id") or self.env.context.get("default_profile_id")
        ])
        profiles._eh_lock_policy()
        records = super().create(vals_list)
        if self._eh_access_studio_should_invalidate():
            self.env["eh.access.profile"]._invalidate_active_cache()
        return records

    def write(self, vals):
        profiles = self.profile_id
        if vals.get("profile_id"):
            profiles |= self.env["eh.access.profile"].browse(vals["profile_id"])
        profiles._eh_lock_policy()
        result = super().write(vals)
        if self._eh_access_studio_should_invalidate():
            self.env["eh.access.profile"]._invalidate_active_cache()
        return result

    def unlink(self):
        self.profile_id._eh_lock_policy()
        result = super().unlink()
        if self._eh_access_studio_should_invalidate():
            self.env["eh.access.profile"]._invalidate_active_cache()
        return result
