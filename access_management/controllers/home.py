import json

from odoo import http
from odoo.http import request, route
from odoo.addons.web.controllers.home import Home


class HomeAccessManagement(Home):

    @route()
    def web_load_menus(self, unique, lang=None):
        if lang:
            request.update_context(lang=lang)

        menus = request.env["ir.ui.menu"].load_web_menus(request.session.debug)
        body = json.dumps(menus)
        response = request.make_response(
            body,
            [
                # this method must specify a content-type application/json instead of using the default text/html set because
                # the type of the route is set to HTTP, but the rpc is made with a get and expects JSON
                ("Content-Type", "application/json")
            ],
        )
        return response
