# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: developer mode gate.

Developer mode does not live on the URL, it lives on the session.
addons/web/models/ir_http.py::_handle_debug reads ?debug= in
_pre_dispatch for EVERY route and writes request.session.debug, and
session_info then feeds that value to the client. Stripping the query
parameter on the web-client route alone is therefore not a gate: one
request to any other route with ?debug=1 turns developer mode on for
the whole session.

We override _handle_debug itself, after core has processed the
parameter, and clear the session flag for any user under an active
profile with disable_debug_mode. That covers the query parameter, an
already-set session, and every route in the application.
"""
import hashlib
import logging

from odoo import models
from odoo.http import request
try:
    from odoo.http import SessionExpiredException
except ImportError:  # Odoo 20 split HTTP into packages.
    from odoo.http.session import SessionExpiredException

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        """Turn off 'Insert in spreadsheet' for a profile that hides it.

        can_insert_in_spreadsheet is the flag the web client reads to
        decide whether to offer the spreadsheet entries in the cog menu
        (addons/spreadsheet/models/ir_http.py), so this is the gate that
        actually removes them rather than a cosmetic arch tweak.
        """
        info = super().session_info()
        try:
            # Set unconditionally rather than only when the key is
            # already True: whichever module writes it, ours is the
            # answer for a profile that switches the capability off.
            if self._eh_access_studio_hides_spreadsheet():
                info["can_insert_in_spreadsheet"] = False
        except Exception:
            _logger.exception("Access Studio: spreadsheet gate failed")
        try:
            self._eh_access_studio_add_interface_flags(info)
        except Exception:
            _logger.exception("Access Studio: interface flags failed")
        try:
            self._eh_access_studio_fold_cache_hash(info)
        except Exception:
            _logger.exception("Access Studio: browser cache key update failed")
        try:
            # The sign-in response is built after the per-request strip
            # ran for an anonymous session, so ?debug=1 on the sign-in
            # request itself is cleared here, for the user now known.
            if request and getattr(request, "session", None) is not None and request.session.debug:
                self._eh_access_studio_strip_debug()
                if not request.session.debug:
                    if "debug" in info:
                        info["debug"] = ""
                    # Odoo 19 hands the flag to the client inside
                    # bundle_params, which is what the asset loader reads.
                    bundle = info.get("bundle_params")
                    if isinstance(bundle, dict) and bundle.get("debug"):
                        bundle["debug"] = ""
        except Exception:
            _logger.exception("Access Studio: debug-mode gate failed")
        return info

    def _eh_access_studio_add_interface_flags(self, info):
        """Publish the chatter and search switches with the session.

        The chatter patch used to ask the server on every form view that
        has one, for every user in the database, and the chatter waited
        for that answer before it mounted. The answer changes only when
        a profile changes, so it travels with the session instead, and
        the per-model exceptions come with it.
        """
        if self.env.su or "eh.access.profile" not in self.env:
            return
        profile_model = self.env["eh.access.profile"].sudo()
        profile_ids = profile_model._eh_active_profile_ids_any_company()
        if not profile_ids:
            # Published even when empty: the client reads this instead of
            # asking, so leaving it out made every unrestricted user pay
            # a round trip to be told there is nothing to hide.
            info["eh_access_studio"] = {"global": {}, "models": {}}
            return
        flags = profile_model.get_interface_flags()
        per_model = {}
        for line in self.env["eh.access.chatter"].sudo().search([
            ("profile_id", "in", list(profile_ids)),
        ]):
            if line.model_name:
                per_model[line.model_name] = {
                    "hide_send_message": True,
                    "hide_log_note": True,
                    "hide_schedule_activity": True,
                }
        info["eh_access_studio"] = {"global": flags, "models": per_model}

    def _eh_access_studio_fold_cache_hash(self, info):
        """Make the browser's cached screens follow the profile.

        From Odoo 19 the web client stores views, actions and menus in
        the browser and shows that copy BEFORE asking the server, keyed
        on registry_hash. A profile edit changes what the server sends
        but not that key, so a restricted user saw the previous screens
        on their next visit. For a user named on a profile, the key also
        carries the configuration stamp and the date (profiles can have
        a time window), so any change retires the stored copy. Users
        under no profile keep Odoo's key untouched.
        """
        registry_hash = info.get("registry_hash")
        if not registry_hash or self.env.su or "eh.access.profile" not in self.env:
            return
        profile_model = self.env["eh.access.profile"].sudo()
        if not profile_model._eh_listed_in_any_profile(self.env.uid):
            return
        stamp = self.env["ir.config_parameter"].sudo().get_param(
            "eh_access_studio.config_stamp", "",
        )
        # The allowed companies too: a company-scoped profile starts or
        # stops applying when they change, with no profile edit at all.
        company_ids = sorted(self.env.user.sudo().company_ids.ids)
        payload = "%s|%s|%s|%s|%s" % (
            registry_hash, stamp, profile_model._today_for_cache_key(),
            self.env.uid, company_ids,
        )
        info["registry_hash"] = hashlib.sha256(payload.encode()).hexdigest()

    def _eh_access_studio_hides_spreadsheet(self):
        if self.env.su or "eh.access.profile" not in self.env:
            return False
        profile_model = self.env["eh.access.profile"].sudo()
        profile_ids = profile_model._get_active_profile_ids(
            self.env.user.id,
            profile_model._eh_safe_company_id(),
            profile_model._today_for_cache_key(),
        )
        if not profile_ids:
            return False
        return any(profile_model.browse(profile_ids).mapped("hide_spreadsheet"))

    @classmethod
    def _handle_debug(cls):
        super()._handle_debug()
        # Runs in _pre_dispatch for every route, which makes it the one
        # place a sign-in block can reach a session that was ALREADY
        # open when the block was switched on. _login only ever sees new
        # sign-ins.
        cls._eh_access_studio_end_blocked_session()
        try:
            cls._eh_access_studio_strip_debug()
        except Exception:
            _logger.exception("Access Studio: debug-mode gate failed")
            # Developer mode off costs a restricted user nothing, so a
            # failure clears it for anyone named on a profile rather
            # than leaving it on.
            if cls._eh_access_studio_session_listed() is not False:
                request.session.debug = ""

    @classmethod
    def _eh_access_studio_session_listed(cls):
        """Is the session's user named on a profile? True/False/None."""
        try:
            uid = request.session.uid
            if not uid:
                return False
            return request.env["eh.access.profile"].sudo()._eh_listed_in_any_profile(uid)
        except Exception:
            return None

    @classmethod
    def _eh_access_studio_session_is_admin(cls, uid):
        try:
            env = request.env
            for xmlid in ("base.group_system", "base.group_erp_manager"):
                group = env.ref(xmlid, raise_if_not_found=False)
                if not group:
                    continue
                for candidate in ("all_user_ids", "users", "user_ids"):
                    if candidate in group._fields:
                        if uid in group.sudo()[candidate].ids:
                            return True
                        break
        except Exception:
            _logger.exception("Access Studio: administrator check failed")
        return False

    @classmethod
    def _eh_access_studio_end_blocked_session(cls):
        if not request or getattr(request, "session", None) is None:
            return
        uid = request.session.uid
        if not uid:
            return
        try:
            env = request.env
            if "eh.access.profile" not in env:
                return
            profile_model = env["eh.access.profile"].sudo()
            blocked = profile_model._eh_login_disabled_for(
                uid, profile_model._today_for_cache_key(),
            )
            if not blocked:
                return
        except Exception:
            _logger.exception("Access Studio: session sign-in check failed")
            # The same posture as sign-in: a user named on no profile,
            # and an administrator, are never shut out by a failure here.
            if cls._eh_access_studio_session_listed() is False:
                return
        if cls._eh_access_studio_session_is_admin(uid):
            return
        _logger.info(
            "Access Studio: ending the open session of user %s, sign-in is"
            " disabled by a profile", uid,
        )
        logout = getattr(request.session, "logout", None)
        if logout is not None:
            logout(keep_db=True)
        else:
            # Odoo 20 moved session operations to module-level functions.
            from odoo.http.session import logout as logout_session
            logout_session(request.session, keep_db=True)
        raise SessionExpiredException(
            "Sign-in is disabled for this account by an Access Studio profile."
        )

    @classmethod
    def _eh_access_studio_strip_debug(cls):
        if not request or getattr(request, "session", None) is None:
            return
        uid = request.session.uid
        if not uid or not request.session.debug:
            return
        env = request.env
        if "eh.access.profile" not in env:
            return
        profile_model = env["eh.access.profile"].sudo()
        # The SESSION's user, not request.env's: on an auth='none' route
        # request.env has no user, and the ?debug= written to the
        # session there would otherwise survive the request.
        profile_ids = profile_model._get_active_profile_ids(
            uid,
            profile_model._eh_safe_company_id(),
            profile_model._today_for_cache_key(),
        )
        if not profile_ids:
            return
        if any(profile_model.browse(profile_ids).mapped("disable_debug_mode")):
            request.session.debug = ""
