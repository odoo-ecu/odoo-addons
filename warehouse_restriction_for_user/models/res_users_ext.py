from odoo import fields, api, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    restrict_operation = fields.Boolean('Restrict Operations')

    restrict_location = fields.Boolean('Restrict Location')

    restrict_ware_house = fields.Boolean('Restrict warehouse')

    allow_location_ids = fields.Many2many('stock.location' , string='Allowed Location')
    allowed_ware_house_ids = fields.Many2many('stock.warehouse', string='Allowed WareHouse')
    ware_house_picking_type_ids = fields.Many2many('stock.picking.type' , string='Warehouse Operation Picking Type')

    @api.onchange('restrict_operation', 'restrict_location', 'restrict_ware_house')
    def ware_get(self):
        if not self.restrict_ware_house:
            self.update({
                'allowed_ware_house_ids': None
            })
        if not self.restrict_operation:
            self.update({
                'ware_house_picking_type_ids': None
            })
        if not self.restrict_location:
            self.update({
                'allow_location_ids': None
            })

    def hide_menu_item(self):
        menu_config = [
            {'xml_id': 'stock.in_picking', 'code': 'incoming'},
            {'xml_id': 'stock.out_picking', 'code': 'outgoing'},
            {'xml_id': 'stock.int_picking', 'code': 'internal'},
            {'xml_id': 'mrp.mrp_operation_picking', 'code': 'mrp_operation', 'module': 'mrp'},
            {'xml_id': 'stock_picking_batch.stock_picking_batch_menu', 'code': 'Batch Transfers',
             'module': 'stock_picking_batch'},
            {'xml_id': 'stock_dropshipping.dropship_picking', 'code': 'dropship', 'module': 'stock_dropshipping'},
        ]

        # Get list of installed modules only once
        installed_modules = self.env['ir.module.module'].search([('state', '=', 'installed')]).mapped('name')


        picking_codes = self.ware_house_picking_type_ids.mapped('code')

        for item in menu_config:
            module_required = item.get('module')
            if module_required and module_required not in installed_modules:
                continue  # Skip if required module isn't installed

            menu = self.env.ref(item['xml_id'], raise_if_not_found=False)
            if menu:
                if self.restrict_operation:
                    menu.active = item['code'] in picking_codes
                else:
                    menu.active = True

    @api.onchange('ware_house_picking_type_ids', 'allow_location_ids', 'allowed_ware_house_ids')
    def upgrade_module(self):
        rule = self.env.ref('warehouse_restriction_for_user.restrict_stock_picking')
        picking_ids = [rec.id.origin for rec in self.ware_house_picking_type_ids]
        location_ids = [loc.id.origin for loc in self.allow_location_ids]
        rule.domain_force = ['|', ('picking_type_id', 'in', picking_ids),
                             ('location_id', 'in', location_ids)]
        self.hide_menu_item()

    # def hide_menu_item(self):
    #     if self.restrict_operation:
    #         menu_id = self.env.ref('stock.in_picking')
    #         if menu_id:
    #             if 'incoming' not in self.ware_house_picking_type_ids.mapped('code'):
    #                 menu_id.active = False
    #             else:
    #                 menu_id.active = True
    #         menu_id = self.env.ref('stock.out_picking')
    #         if menu_id:
    #             if 'outgoing' not in self.ware_house_picking_type_ids.mapped('code'):
    #                 menu_id.active = False
    #             else:
    #                 menu_id.active = True
    #         menu_id = self.env.ref('stock.int_picking')
    #
    #         if menu_id:
    #             if 'internal' not in self.ware_house_picking_type_ids.mapped('code'):
    #                 menu_id.active = False
    #             else:
    #                 menu_id.active = True
    #         mrp_module = self.env['ir.module.module'].search(
    #             [('state', '=', 'installed'), ('name', '=', 'mrp')])
    #         if mrp_module:
    #             menu_id = self.env.ref('mrp.mrp_operation_picking')
    #             if menu_id:
    #                 if 'mrp_operation' not in self.ware_house_picking_type_ids.mapped('code'):
    #                     menu_id.active = False
    #                 else:
    #                     menu_id.active = True
    #         stock_picking_batch_module = self.env['ir.module.module'].search(
    #             [('state', '=', 'installed'), ('name', '=', 'stock_picking_batch')])
    #         if stock_picking_batch_module:
    #             menu_id = self.env.ref('stock_picking_batch.stock_picking_batch_menu')
    #             if menu_id:
    #                 if 'Batch Transfers' not in self.ware_house_picking_type_ids.mapped('code'):
    #                     menu_id.active = False
    #                 else:
    #                     menu_id.active = True
    #         stock_dropshipping_module = self.env['ir.module.module'].search(
    #             [('state', '=', 'installed'), ('name', '=', 'stock_dropshipping')])
    #         if stock_dropshipping_module:
    #             menu_id = self.env.ref('stock_dropshipping.dropship_picking')
    #             if menu_id:
    #                 if 'dropship' not in self.ware_house_picking_type_ids.mapped('code'):
    #                     menu_id.active = False
    #                 else:
    #                     menu_id.active = True
    #     else:
    #         in_menu_id = self.env.ref('stock.in_picking')
    #         if in_menu_id:
    #             in_menu_id.active = True
    #         int_menu_id = self.env.ref('stock.int_picking')
    #         if int_menu_id:
    #             int_menu_id.active = True
    #         out_menu_id = self.env.ref('stock.out_picking')
    #         if out_menu_id:
    #             out_menu_id.active = True
    #         stock_dropshipping_module = self.env['ir.module.module'].search(
    #             [('state', '=', 'installed'), ('name', '=', 'stock_dropshipping')])
    #         if stock_dropshipping_module:
    #             drop_menu_id = self.env.ref('stock_dropshipping.dropship_picking')
    #             if drop_menu_id:
    #                 drop_menu_id.active = True
    #         mrp_module = self.env['ir.module.module'].search(
    #             [('state', '=', 'installed'), ('name', '=', 'mrp')])
    #         if mrp_module:
    #             mrp_menu_id = self.env.ref('mrp.mrp_operation_picking')
    #             if mrp_menu_id:
    #                 mrp_menu_id.active = True
