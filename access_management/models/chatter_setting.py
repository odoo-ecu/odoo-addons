from odoo import fields, models


class AccessRuleChatterSetting(models.Model):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _name = "access.rule.chatter.setting"
    _description = "Access Rule Chatter Setting"

    # ------------------------------------------------------------------
    # 2. DEFAULT METHODS AND default_get
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. FIELD DECLARATIONS
    # ------------------------------------------------------------------

    rule_id = fields.Many2one("access.rule", "Rule", ondelete="cascade", required=True)
    model_id = fields.Many2one("ir.model", "Model", required=True, ondelete="cascade")
    hide_chatter = fields.Boolean("Hide Chatter?")
    hide_send_message = fields.Boolean("Hide Send Message?")
    hide_search_message = fields.Boolean("Hide Search Message?")
    hide_lognote = fields.Boolean("Hide Log Note?")
    hide_activity = fields.Boolean("Hide Activity")
    hide_attachments = fields.Boolean("Hide Attachments")
    hide_followers = fields.Boolean("Hide Followers?")

    # ------------------------------------------------------------------
    # 4. COMPUTE, INVERSE AND SEARCH METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 5. SELECTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 6. CONSTRAINTS METHODS AND ONCHANGE METHODS
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
