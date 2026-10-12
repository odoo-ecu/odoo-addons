# -*- coding: utf-8 -*-

import logging
from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    """Add new fields to configuration settings to Restrict stock warehouse
    for users."""
    _inherit = 'res.config.settings'

    group_user_warehouse_restriction = fields.Boolean(
        string="Restrict Stock Warehouse",
        implied_group='ea_user_warehouse_restriction.'
                      'user_warehouse_restriction_group_user',
        help="Check if you want to restrict warehouse for users.")

    @api.onchange('group_user_warehouse_restriction')
    def _onchange_group_user_warehouse_restriction(self):
        """This method is triggered when the 'group_user_warehouse_restriction'
        field is changed. if it's true, assigns the current user as the
        allowed user of all existing warehouses."""
        rule = self.env.ref('ea_user_warehouse_restriction.operation_type_rule_users', raise_if_not_found=False)
        if rule:
            rule.active = False
        try:
            warehouses = self.env['stock.warehouse'].search([])
            for warehouse in warehouses:
                if self.group_user_warehouse_restriction:
                    # Assign the current user to each warehouse
                    if not warehouse.user_ids:
                        warehouse.user_ids = [(6, 0, [self.env.user.id])]
                else:
                    # Clear the allowed users for each warehouse
                    warehouse.user_ids = [(5, 0, 0)]
        except AccessError as e:
            _logger.warning(f"Access error occurred: {e}")
        finally:
            if rule:
                rule.active = True
