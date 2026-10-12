from odoo import models, api, tools


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _visible_menu_ids(self, debug=False):
        """
        Override to dynamically hide menus based on custom rules.
        Checks `menu.access.rule` records for the user's groups and
        removes the corresponding menus.
        """
        self.env.registry.clear_cache()
        visible_menu_ids = super()._visible_menu_ids(debug=debug)

        # Fetch all rules where the current user belongs to the restricted group
        rules = self.env['menu.access.rule'].sudo().search([
            ('based_option', '=', 'groups'),
            ('group_id', 'in', self.env.user.group_ids.ids),
            ('allow_group_id', 'not in', self.env.user.group_ids.ids)
        ])
        if rules:
            restricted_menu_ids = set(rules.mapped('menu_ids').ids)
            visible_menu_ids -= restricted_menu_ids

        user_rules = self.env['menu.access.rule'].sudo().search([
            ('based_option', '=', 'users'),
            ('user_ids', 'in', self.env.user.ids)
        ])

        if user_rules:
            restricted_menu_ids = set(user_rules.mapped('menu_ids').ids)
            visible_menu_ids -= restricted_menu_ids
        self.env.registry.clear_cache()
        return visible_menu_ids
