from odoo import api, fields, models
from odoo.osv import expression


class EasyAccessRole(models.Model):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _name = "access.rule"
    _description = "Easy Access Rules"

    # ------------------------------------------------------------------
    # 2. DEFAULT METHODS AND default_get
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. FIELD DECLARATIONS
    # ------------------------------------------------------------------

    name = fields.Char("Name", required=True)
    active = fields.Boolean("Active", default=True)
    company_id = fields.Many2one(
        "res.company", "Company", default=lambda self: self.env.company.id
    )

    readonly = fields.Boolean("Read-only")

    hide_menu_domain = fields.Binary(
        string="Hide Menu Domain", compute="_compute_hide_menu_domain"
    )
    hide_menu_ids = fields.Many2many(
        "ir.ui.menu",
        "ear_ui_menu_rel",
        "ear_id",
        "menu_id",
        string="Hide Menus",
    )
    user_ids = fields.Many2many(
        "res.users", "ear_user_rel", "ear_id", "user_id", string="Users"
    )

    hide_report_btn = fields.Boolean("Hide Reports Button?")
    hidden_report_ids = fields.One2many(
        "access.rule.hidden.report", "rule_id", "Hidden Reports"
    )

    field_access_ids = fields.One2many("field.access", "rule_id", "Field Access")

    # Chatter
    hide_chatter = fields.Boolean("Hide Chatter?")
    hide_send_message = fields.Boolean("Hide Send Message?")
    hide_search_message = fields.Boolean("Hide Search Message?")
    hide_lognote = fields.Boolean("Hide Log Note?")
    hide_activity = fields.Boolean("Hide Activity")
    hide_attachments = fields.Boolean("Hide Attachments")
    hide_followers = fields.Boolean("Hide Followers?")

    restrict_import_records = fields.Boolean("Restrict Import Records?")

    restrict_debug_mode = fields.Boolean("Restrict Debug Mode?")
    hide_link_ids = fields.One2many(
        "access.hide.view.node", "access_rule_id", "Hide Links"
    )
    hide_button_ids = fields.One2many(
        "access.hide.view.node", "access_rule_btn_id", "Hide Buttons"
    )
    hide_page_ids = fields.One2many(
        "access.hide.view.node", "access_rule_page_id", "Hide Pages"
    )

    restrict_export = fields.Boolean("Restrict Export?")

    record_rule_ids = fields.One2many("ir.rule", "rule_id", "Model Rules")

    restricted_view_ids = fields.One2many(
        "access.rule.restricted.view", "rule_id", "Restricted Views"
    )

    chatter_setting_ids = fields.One2many(
        "access.rule.chatter.setting", "rule_id", "Chatter Settings"
    )

    # ------------------------------------------------------------------
    # 4. COMPUTE, INVERSE AND SEARCH METHODS
    # ------------------------------------------------------------------

    @api.depends("hide_menu_ids")
    def _compute_hide_menu_domain(self):
        for access in self:
            domain = []

            # Remove the hidden menus and it's childs
            if access.hide_menu_ids:
                menu_ids = access.hide_menu_ids.ids
                if access.hide_menu_ids.child_id:
                    menu_ids.extend(access.hide_menu_ids.child_id.ids)

                domain = [("id", "not in", menu_ids)]

            access.hide_menu_domain = domain

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

    def action_toggle_active(self):
        self.ensure_one()
        self.write({"active": not self.active})

    # ------------------------------------------------------------------
    # 9. BUSINESS METHODS
    # ------------------------------------------------------------------

    def get_model_rules(self, model):
        domain = [("user_ids", "in", self.env.user.ids), ("active", "=", True)]

        model_rec = self.env["ir.model"].sudo().search([("model", "=", model)], limit=1)

        conditions = []
        if model_rec:
            conditions = [
                [("record_rule_ids.model_id", "=", model_rec.id)],
                [("field_access_ids.model_id", "=", model_rec.id)],
                [("hide_link_ids.model_id", "=", model_rec.id)],
                [("hide_button_ids.model_id", "=", model_rec.id)],
                [("hide_page_ids.model_id", "=", model_rec.id)],
                [("restricted_view_ids.model_id", "=", model_rec.id)],
                [("hidden_report_ids.model_id", "=", model_rec.id)],
                [("chatter_setting_ids.model_id", "=", model_rec.id)],
            ]

        # Global conditions — these booleans apply to all models
        conditions += [
            [("readonly", "=", True)],
            [("restrict_debug_mode", "=", True)],
            [("restrict_export", "=", True)],
            [("restrict_import_records", "=", True)],
            [("hide_report_btn", "=", True)],
            [("hide_chatter", "=", True)],
            [("hide_send_message", "=", True)],
            [("hide_search_message", "=", True)],
            [("hide_lognote", "=", True)],
            [("hide_activity", "=", True)],
            [("hide_attachments", "=", True)],
            [("hide_followers", "=", True)],
        ]

        if conditions:
            domain = expression.AND([domain, expression.OR(conditions)])

        return self.search(domain)
