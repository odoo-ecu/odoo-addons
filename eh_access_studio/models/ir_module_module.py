# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: blocking module management.

Core already restricts ir.module.module to the system administrator
group, so this exists for the case that group is wider than the people
who should be installing things: a power user who genuinely needs
administrator rights for something else, and must not be able to change
what the database runs.

The gate sits on the buttons the Apps screen calls rather than on the
menu, so hiding the menu is not a prerequisite and the block holds over
RPC.
"""
import logging

from odoo import models, _
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    def _eh_access_studio_check_module_management(self):
        if self.env.su or self.env.uid == 1:
            return
        if "eh.access.profile" not in self.env:
            return
        profile_model = self.env["eh.access.profile"].sudo()
        try:
            profile_ids = profile_model._get_active_profile_ids(
                self.env.user.id,
                profile_model._eh_safe_company_id(),
                profile_model._today_for_cache_key(),
            )
        except Exception:
            _logger.exception(
                "Access Studio: module management gate could not resolve"
                " the profile set; blocking",
            )
            raise AccessError(_(
                "Your access profile could not be read, so this module"
                " operation was refused."
            ))
        if not profile_ids:
            return
        profiles = profile_model.browse(profile_ids)
        if any(profiles.mapped("restrict_module_management")):
            raise AccessError(_(
                "Your access profile does not allow installing,"
                " upgrading or uninstalling apps."
            ))

    def button_immediate_install(self):
        self._eh_access_studio_check_module_management()
        return super().button_immediate_install()

    def button_install(self):
        self._eh_access_studio_check_module_management()
        return super().button_install()

    def button_immediate_upgrade(self):
        self._eh_access_studio_check_module_management()
        return super().button_immediate_upgrade()

    def button_upgrade(self):
        self._eh_access_studio_check_module_management()
        return super().button_upgrade()

    def button_immediate_uninstall(self):
        self._eh_access_studio_check_module_management()
        return super().button_immediate_uninstall()

    def button_uninstall(self):
        self._eh_access_studio_check_module_management()
        return super().button_uninstall()
