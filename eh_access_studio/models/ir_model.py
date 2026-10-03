# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: display-name overlays.

When the Access Studio configuration form is open, the model and field
selectors render names as 'Human Label (technical.name)' so admins can
quickly find the correct technical entity. Activated by a context flag
('eh_access_studio') that is set on the m2o widgets only.
"""
from odoo import api, models


class IrModel(models.Model):
    _inherit = "ir.model"

    @api.depends_context("eh_access_studio")
    def _compute_display_name(self):
        if not self.env.context.get("eh_access_studio"):
            return super()._compute_display_name()
        for record in self:
            record.display_name = "{0} ({1})".format(record.name, record.model)


class IrModelFields(models.Model):
    _inherit = "ir.model.fields"

    @api.depends_context("eh_access_studio")
    def _compute_display_name(self):
        if not self.env.context.get("eh_access_studio"):
            return super()._compute_display_name()
        for record in self:
            record.display_name = "{0} ({1}.{2})".format(
                record.field_description or record.name,
                record.model_id.model,
                record.name,
            )
