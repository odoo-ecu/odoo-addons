# -*- coding: utf-8 -*-

from odoo import api, models


class StockPicking(models.Model):
    """Extends stock picking to apply domain restrictions based on user's
    assigned warehouses."""
    _inherit = 'stock.picking'

    @api.onchange('location_id', 'location_dest_id')
    def _onchange_location_id(self):
        """Domain for location_id and location_dest_id."""
        if self.env['ir.config_parameter'].sudo().get_param('ea_user_warehouse_restriction.group_user_warehouse_restriction'):
            return {
            'domain': {'location_id': [
                ('warehouse_id.user_ids', 'in', self.env.user.id)],
                'location_dest_id': [
                    ('warehouse_id.user_ids', 'in', self.env.user.id)]}}
