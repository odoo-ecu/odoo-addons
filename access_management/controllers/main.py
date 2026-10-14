from odoo.http import request, route, Controller


class RestrictDebugController(Controller):
    @route("/restrict_debug/check", type="json", auth="user")
    def check_debug_restriction(self, model_name):
        model_rec = (
            request.env["ir.model"].sudo().search([("model", "=", model_name)], limit=1)
        )
        if not model_rec:
            return False
        rules = request.env["access.rule"].sudo().get_model_rules(model_name)
        return bool(rules.filtered("restrict_debug_mode"))

    @route("/check_restricted_views", type="json", auth="user")
    def _check_restricted_views(self, model):
        model_rules = request.env["access.rule"].sudo().get_model_rules(model)
        restricted_views = model_rules.restricted_view_ids.mapped("view_id.type")

        return restricted_views
