from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    inter_wh_final_dest_id = fields.Many2one(
        "stock.location",
        string="Inter WH Final Destination",
        copy=False,
        readonly=True,
    )
    inter_wh_receipt_created = fields.Boolean(
        string="Inter WH Receipt Created",
        default=False,
        copy=False,
        readonly=True,
    )

    def _is_admin_bypass(self):
        return self.env.user.id == 2

    def _is_restricted_user(self):
        if self._is_admin_bypass():
            return False
        return self.env.user.has_group(
            "stock_location_access_control.group_restricted_warehouse_user"
        )

    def _location_in_user_tree(self, location, locations):
        if not location or not locations:
            return False
        return bool(
            self.env["stock.location"].search_count([
                ("id", "child_of", locations.ids),
                ("id", "=", location.id),
            ])
        )

    def _user_has_access_to_location(self, location):
        if self._is_admin_bypass():
            return True
        return self._location_in_user_tree(
            location, self.env.user.allowed_location_ids
        )

    def _user_is_blocked_from_location(self, location):
        if self._is_admin_bypass():
            return False
        return self._location_in_user_tree(
            location, self.env.user.not_allowed_transfer_location_ids
        )

    def _is_same_location(self, source, dest):
        return bool(source and dest and source.id == dest.id)

    def _locations_belong_to_same_allowed_scope(self, source, dest):
        allowed_locations = self.env.user.allowed_location_ids
        if not source or not dest or not allowed_locations:
            return False

        source_in_allowed = self._location_in_user_tree(source, allowed_locations)
        dest_in_allowed = self._location_in_user_tree(dest, allowed_locations)
        return source_in_allowed and dest_in_allowed

    def _get_transit_location(self):
        transit_location = self.env.ref("stock.stock_location_transit", raise_if_not_found=False)
        if not transit_location:
            transit_location = self.env["stock.location"].search([
                ("usage", "=", "transit")
            ], limit=1)

        if not transit_location:
            raise UserError(_(
                "No transit location was found. Please create or configure a Transit Location first."
            ))
        return transit_location

    def _get_location_warehouse(self, location):
        if not location:
            return False
        if hasattr(location, "warehouse_id") and location.warehouse_id:
            return location.warehouse_id
        if hasattr(location, "get_warehouse"):
            return location.get_warehouse()
        return False

    def _get_receipt_picking_type(self, warehouse):
        if not warehouse or not warehouse.in_type_id:
            raise UserError(_(
                "No receipt operation type found for destination warehouse %s."
            ) % (warehouse.display_name or warehouse.name))
        return warehouse.in_type_id

    def _prepare_inter_wh_before_validate(self):
        transit_location = self._get_transit_location()

        for picking in self:
            if picking.picking_type_id.code != "internal":
                continue
            if picking.state in ("done", "cancel"):
                continue

            source_wh = self._get_location_warehouse(picking.location_id)
            dest_wh = self._get_location_warehouse(picking.location_dest_id)

            if not source_wh or not dest_wh:
                continue
            if source_wh.id == dest_wh.id:
                continue

            if not picking.inter_wh_final_dest_id:
                original_dest = picking.location_dest_id
                picking.with_context(skip_location_access_check=True).write({
                    "inter_wh_final_dest_id": original_dest.id,
                    "location_dest_id": transit_location.id,
                })

                for move in picking.move_ids:
                    move.write({
                        "location_dest_id": transit_location.id,
                    })

    def _create_inter_wh_receipt_after_done(self):
        StockPicking = self.env["stock.picking"].sudo()
        StockMove = self.env["stock.move"].sudo()

        transit_location = self._get_transit_location()

        for picking in self:
            if picking.picking_type_id.code != "internal":
                continue
            if picking.state != "done":
                continue
            if not picking.inter_wh_final_dest_id:
                continue
            if picking.inter_wh_receipt_created:
                continue

            final_dest = picking.inter_wh_final_dest_id
            dest_wh = self._get_location_warehouse(final_dest)
            if not dest_wh:
                continue

            existing_receipt = StockPicking.search([
                ("origin", "=", picking.name),
                ("picking_type_id", "=", dest_wh.in_type_id.id),
                ("state", "!=", "cancel"),
            ], limit=1)
            if existing_receipt:
                picking.sudo().write({"inter_wh_receipt_created": True})
                continue

            receipt_type = self._get_receipt_picking_type(dest_wh)

            receipt = StockPicking.with_context(
                skip_location_access_check=True
            ).create({
                "picking_type_id": receipt_type.id,
                "location_id": transit_location.id,
                "location_dest_id": final_dest.id,
                "origin": picking.name,
                "move_type": picking.move_type,
                "company_id": picking.company_id.id,
            })

            for move in picking.move_ids:
                qty = getattr(move, "quantity", 0.0) or move.product_uom_qty
                StockMove.with_context(
                    skip_location_access_check=True
                ).create({
                    "product_id": move.product_id.id,
                    "product_uom_qty": qty,
                    "product_uom": move.product_uom.id,
                    "picking_id": receipt.id,
                    "location_id": transit_location.id,
                    "location_dest_id": final_dest.id,
                    "company_id": move.company_id.id,
                    "origin": picking.name,
                })

            receipt.action_confirm()
            picking.sudo().write({"inter_wh_receipt_created": True})

    def _check_create_edit_permission(self):
        if self.env.context.get("skip_location_access_check"):
            return

        if not self._is_restricted_user():
            return

        for picking in self:
            code = picking.picking_type_id.code

            source_allowed = picking._user_has_access_to_location(picking.location_id)
            dest_allowed = picking._user_has_access_to_location(picking.location_dest_id)

            source_blocked = picking.location_id and picking._user_is_blocked_from_location(picking.location_id)
            dest_blocked = picking.location_dest_id and picking._user_is_blocked_from_location(picking.location_dest_id)

            if source_blocked or dest_blocked:
                raise UserError(_(
                    "You cannot create or edit this operation because one of the locations is blocked in Not Allowed to Transfer."
                ))

            if code == "incoming":
                if not dest_allowed:
                    raise UserError(_(
                        "You can only create or edit receipts for your allowed destination locations."
                    ))

            elif code == "outgoing":
                if not source_allowed:
                    raise UserError(_(
                        "You can only create or edit deliveries from your allowed source locations."
                    ))

            elif code == "internal":
                if self._is_same_location(picking.location_id, picking.location_dest_id):
                    raise UserError(_(
                        "You cannot create an internal transfer to the same location."
                    ))

                if not source_allowed and not dest_allowed:
                    raise UserError(_(
                        "You can only create or edit internal transfers related to your allowed locations."
                    ))

                source_wh = self._get_location_warehouse(picking.location_id)
                dest_wh = self._get_location_warehouse(picking.location_dest_id)

                if (
                    (source_wh and dest_wh and source_wh.id == dest_wh.id)
                    or self._locations_belong_to_same_allowed_scope(
                        picking.location_id, picking.location_dest_id
                    )
                ):
                    raise UserError(_(
                        "You cannot create an internal transfer within your own warehouse/location scope."
                    ))

    def _check_validate_permission(self):
        if not self._is_restricted_user():
            return

        for picking in self:
            code = picking.picking_type_id.code

            source_allowed = picking._user_has_access_to_location(picking.location_id)
            dest_allowed = picking._user_has_access_to_location(picking.location_dest_id)

            source_blocked = picking.location_id and picking._user_is_blocked_from_location(picking.location_id)
            dest_blocked = picking.location_dest_id and picking._user_is_blocked_from_location(picking.location_dest_id)

            if source_blocked or dest_blocked:
                raise UserError(_(
                    "You cannot validate this operation because one of the locations is blocked in Not Allowed to Transfer."
                ))

            if code == "incoming":
                if not dest_allowed:
                    raise UserError(_(
                        "You can only validate receipts for your allowed destination locations."
                    ))

            elif code == "outgoing":
                if not source_allowed:
                    raise UserError(_(
                        "You can only validate deliveries from your allowed source locations."
                    ))

            elif code == "internal":
                if self._is_same_location(picking.location_id, picking.location_dest_id):
                    raise UserError(_(
                        "You cannot validate an internal transfer to the same location."
                    ))

                source_wh = self._get_location_warehouse(picking.location_id)
                dest_wh = self._get_location_warehouse(picking.location_dest_id)

                if source_wh and dest_wh and source_wh.id != dest_wh.id:
                    if not source_allowed:
                        raise UserError(_(
                            "You can only validate this inter-warehouse transfer from your allowed source location."
                        ))
                else:
                    # Stock Issue:
                    # User Allowed Location -> Inventory Adjustment
                    if picking.location_dest_id.usage == "inventory":
                        if not source_allowed:
                            raise UserError(_(
                                "You can only validate stock issue from your allowed source location."
                            ))

                    # Normal Internal / Stock Addition:
                    # Inventory Adjustment -> User Allowed Location
                    else:
                        if not dest_allowed:
                            raise UserError(_(
                                "You cannot validate this internal transfer. Only the destination warehouse user can validate it."
                            ))

                    if self._locations_belong_to_same_allowed_scope(
                        picking.location_id, picking.location_dest_id
                    ):
                        raise UserError(_(
                            "You cannot validate this internal transfer because it is within your own warehouse/location scope."
                        ))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_create_edit_permission()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._check_create_edit_permission()
        return res

    def button_validate(self):
        self._check_validate_permission()
        self._prepare_inter_wh_before_validate()
        return super().button_validate()

    def _action_done(self):
        res = super()._action_done()
        self._create_inter_wh_receipt_after_done()
        return res