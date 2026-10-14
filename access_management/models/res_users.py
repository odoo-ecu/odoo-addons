from odoo import api, fields, models
from odoo.tools import ormcache
from odoo.tools.sql import table_columns, create_column


class ResUser(models.Model):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _inherit = "res.users"

    # ------------------------------------------------------------------
    # 2. DEFAULT METHODS AND default_get
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. FIELD DECLARATIONS
    # ------------------------------------------------------------------

    access_rule_ids = fields.Many2many(
        "access.rule",
        "ear_user_rel",
        "user_id",
        "ear_id",
        string="Easy Access Rules",
    )
    access_rule_update_at = fields.Date(
        "Access Rule Updated",
        compute="_compute_access_rule_update_at",
        store=True,
    )
    restricted_model_ids = fields.Many2many(
        "ir.model",
        string="Restricted Models",
        compute="_compute_restricted_model_ids",
    )

    # ------------------------------------------------------------------
    # 4. COMPUTE, INVERSE AND SEARCH METHODS
    # ------------------------------------------------------------------

    @api.depends(
        "access_rule_ids", "access_rule_ids.write_date", "access_rule_ids.active"
    )
    def _compute_access_rule_update_at(self):
        for user in self:
            if user.access_rule_ids:
                user.access_rule_update_at = max(
                    user.access_rule_ids.mapped("write_date")
                )
                self.env.registry.clear_cache()
            else:
                user.access_rule_update_at = False

    @api.depends(
        "access_rule_ids",
        "access_rule_ids.record_rule_ids",
        "access_rule_ids.field_access_ids",
        "access_rule_ids.restricted_view_ids",
        "access_rule_ids.hidden_report_ids",
        "access_rule_ids.chatter_setting_ids",
    )
    def _compute_restricted_model_ids(self):
        for user in self:
            models = self.env["ir.model"]
            for rule in user.access_rule_ids:
                models |= rule.record_rule_ids.model_id
                models |= rule.field_access_ids.model_id
                models |= rule.restricted_view_ids.model_id
                models |= rule.hidden_report_ids.model_id
                models |= rule.chatter_setting_ids.model_id
            user.restricted_model_ids = models

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

    def _get_hidden_menus(self):
        if not self:
            self = self.env.user

        return self.access_rule_ids.filtered("active").hide_menu_ids

    def _get_hidden_reports(self):
        if not self:
            self = self.env.user

        return self.access_rule_ids.filtered("active").hidden_report_ids.mapped(
            "report_id"
        )

    @api.model
    def check_export_enable(self, model):
        enabled = self.env.user.has_group("base.group_allow_export")

        model_rules = self.env["access.rule"].get_model_rules(model)
        if any(model_rules.mapped("restrict_export")):
            enabled = False

        return enabled

    @api.model
    def check_import_enabled(self, model):
        model_rules = self.env["access.rule"].get_model_rules(model)
        return any(model_rules.mapped("restrict_import_records"))

    def _register_hook(self):
        columns = table_columns(self.env.cr, self._table)
        if "access_rule_update_at" not in columns:
            create_column(
                self.env.cr, self._table, "access_rule_update_at", "timestamp"
            )
        return super()._register_hook()
