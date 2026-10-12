# -*- coding: utf-8 -*-
from odoo import api, fields, models


class StockWarehouse(models.Model):
    """Extends the 'stock.warehouse' model to add functionality for restricting
     stock location access to specific users within the system."""
    _inherit = "stock.warehouse"

    user_ids = fields.Many2many(
        comodel_name='res.users', string='Allowed Users',
        domain=lambda self: [
            ('group_ids', 'in', [
                self.env.ref('stock.group_stock_user').id,
                self.env.ref('stock.group_stock_manager').id
            ])],
        # default=lambda self: self.env.user,
        help='Users who are allowed access to this warehouse.')
    restrict_location = fields.Boolean(
        string='Restrict Stock Location for this Warehouse',
        help='Restrict stock location of this warehouse to the selected '
             'users.')

    @api.onchange('restrict_location', 'user_ids')
    def _onchange_restrict_location(self):
        """Triggered when the 'restrict_location' or 'user_ids' fields
        are modified. It updates the 'restrict_location' field for selected
        users when restricting stock location access."""
        warehouse_id = self.id if isinstance(self.id, int) else (self._origin.id if isinstance(self._origin.id, int) else False)
        if not warehouse_id:
            return
        for rec in self.user_ids:
            user_id = rec.id if isinstance(rec.id, int) else (rec._origin.id if isinstance(rec._origin.id, int) else False)
            if not user_id:
                continue
            user = self.env['res.users'].browse(user_id)
            if self.restrict_location:
                user.write({'restrict_location': True,
                            'allowed_warehouse_ids': [
                                (4, warehouse_id)]})
            elif not self.restrict_location:
                user.write({'restrict_location': True,
                            'location_ids': False
                            })

    def action_open_users_view(self):
        """Return user basic form view to give restricted location for users"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Users',
            'view_mode': 'list,form',
            'res_model': 'res.users',
            'domain': [('id', 'in', [user.id for user in self.user_ids]),
                       ('group_ids', 'not in',
                        [self.env.ref('base.group_system').id])]}
