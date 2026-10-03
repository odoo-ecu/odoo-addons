# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: web-client gates.

When a user lands on the web client and the URL carries `?debug=...`,
we redirect to the same URL with `debug=0` if any active profile sets
disable_debug_mode. Implementation strips the debug parameter cleanly
and preserves every other query parameter.

This is the cosmetic half only: it keeps the address bar honest. The
gate that actually holds is models/ir_http.py, which clears the debug
flag on the SESSION for every route, because that is where developer
mode really lives.
"""
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from odoo import http
from odoo.addons.web.controllers.home import Home
from odoo.http import request


class HeritageHome(Home):

    @http.route()
    def web_client(self, s_action=None, **kw):
        if request.session.uid:
            try:
                redirect = self._eh_access_studio_debug_redirect()
                if redirect:
                    return redirect
            except Exception:
                # Logging is acceptable; we never want a debug-strip
                # error to lock users out of the web client.
                import logging
                logging.getLogger(__name__).exception(
                    "Access Studio: web-client debug strip failed"
                )
        return super().web_client(s_action=s_action, **kw)

    def _eh_access_studio_debug_redirect(self):
        if "eh.access.profile" not in request.env:
            return None
        url = urlparse(request.httprequest.url)
        params = parse_qs(url.query)
        debug = params.get("debug", ["0"])[0]
        if debug in ("0", ""):
            return None
        Profile = request.env["eh.access.profile"].sudo()
        active_ids = Profile._get_active_profile_ids(
            request.env.user.id,
            Profile._eh_safe_company_id(),
            Profile._today_for_cache_key(),
        )
        if not active_ids:
            return None
        if not any(Profile.browse(active_ids).mapped("disable_debug_mode")):
            return None
        params["debug"] = ["0"]
        new_query = urlencode(params, doseq=True)
        new_url = urlunparse(url._replace(query=new_query))
        return request.redirect(new_url)
