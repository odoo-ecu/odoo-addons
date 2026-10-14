from collections import defaultdict

from odoo import models, tools
from odoo.tools import _, frozendict
from odoo.exceptions import MissingError


class IrActions(models.Model):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _inherit = "ir.actions.actions"

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

    @tools.ormcache(
        "model_name", "self.env.lang", "self.env.user.access_rule_update_at"
    )
    def _get_bindings(self, model_name):
        """
        Overwrite to exclude hidden report actions
        """

        cr = self.env.cr

        # discard unauthorized actions, and read action definitions
        result = defaultdict(list)

        # Exclude hidden reports
        access_rules = self.env["access.rule"].get_model_rules(model_name)
        hide_report_btn = False
        where_caluse = f"WHERE m.model = '{model_name}'"
        if access_rules:
            hidden_report_lines = access_rules.hidden_report_ids.filtered(
                lambda r: r.model_id.model == model_name
            )
            hidden_report_ids = hidden_report_lines.mapped("report_id").ids
            if hidden_report_ids:
                where_caluse += " AND a.id NOT IN ({})".format(
                    str(hidden_report_ids)[1:-1]
                )

            hide_report_btn = any(access_rules.mapped("hide_report_btn")) or any(
                hidden_report_lines.mapped("hide_report_btn")
            )

        self.env.flush_all()
        cr.execute(f"""
            SELECT a.id, a.type, a.binding_type
            FROM ir_actions a
            JOIN ir_model m ON a.binding_model_id = m.id
            {where_caluse}
            ORDER BY a.id;""")
        for action_id, action_model, binding_type in cr.fetchall():
            try:
                action = self.env[action_model].sudo().browse(action_id)
                fields = ["name", "binding_view_types"]
                for field in ("group_ids", "res_model", "sequence", "domain"):
                    if field in action._fields:
                        fields.append(field)
                action = action.read(fields)[0]
                if action.get("group_ids"):
                    # transform the list of ids into a list of xml ids
                    groups = self.env["res.groups"].browse(action["group_ids"])
                    action["group_ids"] = list(groups._ensure_xml_id().values())
                if "domain" in action and not action.get("domain"):
                    action.pop("domain")
                result[binding_type].append(frozendict(action))
            except MissingError:
                continue

        # sort actions by their sequence if sequence available
        if result.get("action"):
            result["action"] = tuple(
                sorted(result["action"], key=lambda vals: vals.get("sequence", 0))
            )

        if hide_report_btn:
            result.pop("report", [])

        return frozendict(result)
