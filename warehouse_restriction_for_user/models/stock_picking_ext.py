from odoo import fields, api, models


class StockPickingExt(models.Model):
    _inherit = 'stock.picking'

    # picking_type_id = fields.Many2one(
    #     'stock.picking.type', 'Operation Type',
    #     required=True, index=True,
    #     default=False)

    def _compute_location_id(self):
        return None

    picking_type_id = fields.Many2one(
        'stock.picking.type',
        string="Operation Type",
        domain="[('id', 'in', new_picking_type_ids)]",
    )
    new_picking_type_ids = fields.Many2many(
        'stock.picking.type',
        string="Operation Type",
        domain=lambda self: self._get_initial_picking_domain()
    )

    def _get_initial_picking_domain(self):
        user = self.env.user
        if user.has_group('warehouse_restriction_for_user.ware_house_user_restrict'):
            if not user.restrict_ware_house:
                picking_ids = self.env['stock.picking.type'].search([('id', 'in', user.ware_house_picking_type_ids.ids)]).ids
                self.new_picking_type_ids = picking_ids
            else:
                warehouse_ids = user.allowed_ware_house_ids.ids
                picking_ids = self.env['stock.picking.type'].search([('warehouse_id', 'in', warehouse_ids)]).ids
                self.new_picking_type_ids = picking_ids
        else:
            return []

    location_id = fields.Many2one(
        'stock.location',
        string="Source Location",
        domain="[('id', 'in', new_location_ids)]",
    )
    new_location_ids = fields.Many2many(
        'stock.location',
        string="Source Location",
        domain=lambda self: self._get_initial_location_domain()
    )

    def _get_initial_location_domain(self):
        user = self.env.user
        if user.has_group('warehouse_restriction_for_user.ware_house_user_restrict'):
            if not user.restrict_ware_house:
                self.new_location_ids = user.allow_location_ids.ids
            else:
                warehouse_ids = user.allowed_ware_house_ids.ids
                loc_ids = self.env['stock.location'].search([('warehouse_id', 'in', warehouse_ids)]).ids
                self.new_location_ids = loc_ids
        else:
            return []

    location_dest_id = fields.Many2one(
        'stock.location',
        string="Destination Location",
        domain=lambda self: self._get_initial_location_dest_domain()
    )

    def _get_initial_location_dest_domain(self):
        # Agar destination location ka logic same hai source location ke jaisa
        return self._get_initial_location_domain()

    @api.onchange('user_id')
    def get_records(self):
        return {
            'domain': {
                'picking_type_id': self._get_initial_picking_domain(),
                'location_id': self._get_initial_location_domain(),
                'location_dest_id': self._get_initial_location_dest_domain(),
            }
        }
    # @api.onchange('user_id')
    # def get_records(self):
    #     if self.env.user.has_group('warehouse_restriction_for_user.ware_house_user_restrict'):
    #         if not self.env.user.restrict_ware_house:
    #             picking_ids = []
    #             location_ids = []
    #             if self.env.user.restrict_operation:
    #                 for ware in self.env.user.ware_house_picking_type_ids:
    #                     picking_ids.append(ware.id)
    #             if self.env.user.restrict_location:
    #                 for location in self.env.user.allow_location_ids:
    #                     location_ids.append(location.id)
    #             return {
    #                 'domain': {
    #                     'picking_type_id': [('id', 'in', picking_ids)],
    #                     'location_id': [('id', 'in', location_ids)],
    #                     'location_dest_id': [('id', 'in', location_ids)]
    #                 }
    #             }
    #         else:
    #             destination_ids = []
    #             loc = []
    #             pick_id = []
    #             for des in self.env.user.allowed_ware_house_ids:
    #                 destination_ids.append(des.id)
    #             warehouse = self.env['stock.warehouse'].search([('id', 'in', destination_ids)])
    #             for record in warehouse:
    #                 location = self.env['stock.location'].search([('warehouse_id', '=', record.id)])
    #                 picking = self.env['stock.picking.type'].search([('warehouse_id', '=', record.id)],limit=1)
    #                 for rec in location:
    #                     loc.append(rec.id)
    #                 for pick in picking:
    #                     pick_id.append(pick.id)
    #             return {
    #                 'domain': {
    #                     'picking_type_id': [('id', 'in', pick_id)],
    #                     'location_id': [('id', 'in', loc)],
    #                     'location_dest_id': [('id', 'in', loc)]
    #                 }
    #             }
    #     else:
    #         picking = []
    #         picking_ids = self.env['stock.picking.type'].search([])
    #         for pick in picking_ids:
    #             picking.append(pick.id)
    #         locations_ids = []
    #         location = self.env['stock.location'].search([])
    #         for locs in location:
    #             locations_ids.append(locs.id)
    #         return {
    #             'domain': {
    #                 'picking_type_id': [('id', 'in', picking)],
    #                 'location_id': [('id', 'in', locations_ids)],
    #                 'location_dest_id': [('id', 'in', locations_ids)]
    #             }
    #         }
    #
