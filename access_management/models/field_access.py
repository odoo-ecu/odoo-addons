from odoo import api, fields, models


class FieldAccess(models.Model):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _name = "field.access"
    _description = "Field access"

    # ------------------------------------------------------------------
    # 2. DEFAULT METHODS AND default_get
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. FIELD DECLARATIONS
    # ------------------------------------------------------------------

    rule_id = fields.Many2one("access.rule", "Rule", ondelete="cascade", required=True)
    model_id = fields.Many2one("ir.model", "Model", required=True, ondelete="cascade")

    used_field_ids = fields.Many2many(
        "ir.model.fields", compute="_compute_used_field_ids"
    )
    field_id = fields.Many2one("ir.model.fields", "Field")
    access = fields.Selection(
        [
            ("readonly", "Readonly"),
            ("required", "Required"),
            ("invisible", "Hide"),
        ],
        string="Access",
    )

    # ------------------------------------------------------------------
    # 4. COMPUTE, INVERSE AND SEARCH METHODS
    # ------------------------------------------------------------------

    @api.depends("rule_id", "rule_id.field_access_ids")
    def _compute_used_field_ids(self):
        for access in self:
            access.used_field_ids = access.rule_id.field_access_ids.field_id.ids

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
