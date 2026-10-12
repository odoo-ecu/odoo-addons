from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    allowed_location_ids = fields.Many2many(
        "stock.location",
        "res_users_stock_location_rel",
        "user_id",
        "location_id",
        string="Allowed Locations",
        domain="[('usage', 'in', ('internal', 'transit'))]",
        help="Internal/Transit locations this user is allowed to work on.",
    )

    not_allowed_transfer_location_ids = fields.Many2many(
        "stock.location",
        "res_users_not_allowed_transfer_location_rel",
        "user_id",
        "location_id",
        string="Not Allowed to Transfer",
        domain="[('usage', 'in', ('internal', 'transit'))]",
        help="Locations that this user is not allowed to transfer to/from.",
    )

    allowed_warehouse_ids = fields.Many2many(
        "stock.warehouse",
        compute="_compute_allowed_warehouse_ids",
        string="Allowed Warehouses",
        help="Computed from allowed locations.",
    )

    not_allowed_transfer_warehouse_ids = fields.Many2many(
        "stock.warehouse",
        compute="_compute_not_allowed_transfer_warehouse_ids",
        string="Not Allowed Transfer Warehouses",
        help="Computed from not allowed transfer locations.",
    )

    def _compute_allowed_warehouse_ids(self):
        Warehouse = self.env["stock.warehouse"]
        for user in self:
            warehouses = Warehouse.browse()
            for location in user.allowed_location_ids:
                warehouses |= Warehouse.search([
                    ("view_location_id", "parent_of", location.id)
                ])
            user.allowed_warehouse_ids = warehouses

    def _compute_not_allowed_transfer_warehouse_ids(self):
        Warehouse = self.env["stock.warehouse"]
        for user in self:
            warehouses = Warehouse.browse()
            for location in user.not_allowed_transfer_location_ids:
                warehouses |= Warehouse.search([
                    ("view_location_id", "parent_of", location.id)
                ])
            user.not_allowed_transfer_warehouse_ids = warehouses