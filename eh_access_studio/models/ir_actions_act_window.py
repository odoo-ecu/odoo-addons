# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: hidden view types on window actions.

Removing a view type from the get_views response is not enough on its
own. The web client builds its view switcher from the action it loaded
(addons/web/static/src/webclient/actions/action_service.js,
_executeActWindowAction), so an action that still advertises a mode the
server then refuses to return leaves the client asking for a view it
never receives.

The filter therefore hooks _get_action_dict, which is what
/web/action/load actually calls, and it reads the profile of the
REQUEST user: that controller browses the action as SUPERUSER
(addons/web/controllers/action.py), so anything keyed on the
environment user or guarded on env.su would never run in production.

Safety rules: 'search' is never filtered, because it is not a
switchable mode and dropping it breaks the first load of the action;
and the list is never emptied, because the client raises when an
act_window resolves to zero views. Hiding every way of opening a model
is a job for Domain Access, not for a cosmetic view filter.
"""
import logging

from odoo import models, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# View modes that show one record. An action reduced to these alone opens
# a blank new record from the menu, so the filter never reduces to them.
_EH_SINGLE_RECORD_MODES = frozenset(("form",))


class IrActionsActWindow(models.Model):
    _inherit = "ir.actions.act_window"

    def _get_action_dict(self):
        """Filter hidden view types out of the action the client reads.

        This has to happen here, not in _compute_views. The web client
        loads an action through /web/action/load, which reads it as
        SUPERUSER (addons/web/controllers/action.py: `request.env[
        action_type].sudo().browse([action_id])._get_action_dict()`), so
        a guard on env.su never runs and a compute-time filter would
        also be writing a stored field (view_mode) from inside a
        compute. We therefore filter the returned dictionary, and we
        resolve the profile against the REQUEST user rather than the
        environment user, because the environment here is the superuser.
        """
        result = super()._get_action_dict()
        try:
            return self._eh_access_studio_filter_action_dict(result)
        except Exception:
            # A cosmetic filter must never break an action.
            _logger.exception(
                "Access Studio: failed to filter view types on action %s",
                self.ids,
            )
            return result

    def read(self, fields=None, load="_classic_read"):
        """Odoo 16 and 17 load an action through read().

        Their /web/action/load reads the action and hands the raw
        dictionary to the client, so a filter that only hooks
        _get_action_dict (the 18 and 19 path) never ran for a menu, a
        breadcrumb or an action opened by id on those series.
        """
        rows = super().read(fields=fields, load=load)
        try:
            for row in rows:
                if isinstance(row, dict) and row.get("views") and row.get("res_model"):
                    self._eh_access_studio_filter_action_dict(row)
        except Exception:
            _logger.exception(
                "Access Studio: failed to filter view types on read of %s",
                self.ids,
            )
        return rows

    def _eh_access_studio_request_env(self):
        """The environment of the person making the request, if any."""
        try:
            from odoo.http import request
        except ImportError:
            return None
        if not request:
            return None
        try:
            env = request.env
        except Exception:
            return None
        if not env or not env.uid or env.uid == SUPERUSER_ID:
            return None
        return env

    def _eh_access_studio_filter_action_dict(self, result):
        if not isinstance(result, dict):
            return result
        model_name = result.get("res_model")
        if not model_name:
            return result
        env = self._eh_access_studio_request_env()
        if env is None or "eh.access.profile" not in env:
            return result
        if model_name not in env:
            return result
        model = env[model_name]
        if not hasattr(model, "_eh_access_studio_model_lines"):
            return result
        lines = model._eh_access_studio_model_lines()
        if not lines:
            return result
        hidden = set()
        for line in lines:
            hidden.update(line._hidden_view_type_set())
        # 'search' is not a switchable mode and removing it breaks the
        # first load of the action, so it is never filtered out here.
        hidden.discard("search")
        if not hidden:
            return result
        views = result.get("views") or []
        kept = [pair for pair in views if pair[1] not in hidden]
        modes = [mode for _view_id, mode in kept if mode != "search"]
        original_modes = [mode for _view_id, mode in views if mode != "search"]
        if not modes or (
            set(modes) <= _EH_SINGLE_RECORD_MODES
            and not set(original_modes) <= _EH_SINGLE_RECORD_MODES
        ):
            # Refuse to leave the action with nothing to render, or with
            # only a form: the client then drops the search view and
            # opens a blank new record straight from the menu. Hiding
            # every way of listing a model is a job for Domain Access.
            _logger.info(
                "Access Studio: not filtering action %s, every list-type"
                " view on %s is hidden", self.ids, model_name,
            )
            return result
        # The client opens the FIRST view of the action when a menu has
        # no view type of its own, so a filter that leaves the form in
        # front drops the user into a blank new record. Any surviving
        # multi-record view goes first, in its original order.
        if modes and modes[0] in _EH_SINGLE_RECORD_MODES:
            kept = (
                [pair for pair in kept if pair[1] not in _EH_SINGLE_RECORD_MODES]
                + [pair for pair in kept if pair[1] in _EH_SINGLE_RECORD_MODES]
            )
            modes = [mode for _view_id, mode in kept if mode != "search"]
        result["views"] = kept
        result["view_mode"] = ",".join(modes)
        if result.get("mobile_view_mode") in hidden:
            # The phone layout opens on its own mode; never a hidden one.
            result["mobile_view_mode"] = modes[0]
        return result
