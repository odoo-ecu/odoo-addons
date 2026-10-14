from odoo import api, models, tools


class IrUiMenu(models.Model):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _inherit = "ir.ui.menu"

    # ------------------------------------------------------------------
    # 2. DEFAULT METHODS AND default_get
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. FIELD DECLARATIONS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 4. COMPUTE, INVERSE AND SEARCH METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 5. SELECTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 6. CONSTRAINS METHODS AND ONCHANGE METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 7. CRUD METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 8. ACTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 9. BUSINESS METHODS
    # ------------------------------------------------------------------

    @api.model
    @api.returns("self")
    def get_user_roots(self):
        hidden_menus = self.env.user._get_hidden_menus()
        roots = super().get_user_roots()
        return roots - hidden_menus

    @api.model
    @tools.ormcache_context("self._uid", "debug", keys=("lang",))
    def load_menus(self, debug):
        """
        TODO: Find the solution to recheck menus when restricted menus
            for user's access rules has been updated.
        """
        return super(IrUiMenu, self).load_menus(debug=debug)

    def _load_menus_blacklist(self):
        """
        Override to extend the blacklist menus list with hidden menus of user's access rule.
        """
        blacklist_menus = super(IrUiMenu, self)._load_menus_blacklist()

        hidden_menus = self.env.user._get_hidden_menus()
        if hidden_menus:
            blacklist_menus.extend(hidden_menus.ids)

        return blacklist_menus
