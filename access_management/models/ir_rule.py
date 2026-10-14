from odoo import api, fields, models, tools
from odoo.tools import config
from odoo.tools.sql import create_column, table_columns

IGNORED_MODELS = ["access.rule"]


class IrRule(models.Model):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _inherit = "ir.rule"

    # ------------------------------------------------------------------
    # 2. DEFAULT METHODS AND default_get
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. FIELD DECLARATIONS
    # ------------------------------------------------------------------

    rule_id = fields.Many2one("access.rule", "Rule", ondelete="cascade")

    # ------------------------------------------------------------------
    # 4. COMPUTE, INVERSE AND SEARCH METHODS
    # ------------------------------------------------------------------

    @api.model
    @tools.conditional(
        "xml" not in config["dev_mode"],
        tools.ormcache(
            "self.env.uid",
            "self.env.su",
            "model_name",
            "mode",
            "tuple(self._compute_domain_context_values())",
            "self.env.user.restricted_model_ids",
        ),
    )
    def _compute_domain(self, model_name, mode="read"):
        """
        Override to recompute the domain for restricted models of the current user
        """
        return super()._compute_domain(model_name, mode=mode)

    # ------------------------------------------------------------------
    # 5. SELECTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 6. CONSTRAINS METHODS AND ONCHANGE METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 7. CRUD METHODS
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if any(records.mapped("rule_id")):
            records.clear_caches()
        return records

    def write(self, vals):
        result = super().write(vals)
        if any(self.mapped("rule_id")):
            self.clear_caches()
        return result

    def unlink(self):
        has_rule_id = any(self.mapped("rule_id"))
        result = super().unlink()
        if has_rule_id:
            self.clear_caches()
        return result

    # ------------------------------------------------------------------
    # 8. ACTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 9. BUSINESS METHODS
    # ------------------------------------------------------------------

    def _register_hook(self):
        columns = table_columns(self.env.cr, self._table)
        if "rule_id" not in columns:
            create_column(self.env.cr, self._table, "rule_id", "integer")

        return super()._register_hook()

    def _get_rules(self, model_name, mode="read"):
        rules = super()._get_rules(model_name, mode)

        self.env.cr.execute(
            """
            SELECT
                ARRAY_AGG(rule.id)
            FROM ir_rule rule
            JOIN access_rule access ON rule.rule_id = access.id
            WHERE access.active = TRUE
              AND EXISTS(
                    SELECT 1
                    FROM ear_user_rel ear
                    WHERE ear.ear_id = access.id
                )
              AND NOT EXISTS(
                    SELECT 1
                    FROM ear_user_rel ear
                    WHERE ear.ear_id = access.id
                      AND ear.user_id = %s
                )
            """,
            (self.env.user.id,),
        )
        res = self.env.cr.fetchone()
        if res and res[0]:
            rules -= self.sudo().browse(res[0])

        return rules
