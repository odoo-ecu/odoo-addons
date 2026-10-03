# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: hidden server actions, at the run call.

Taking a server action out of the Actions menu is presentation. The
action still runs through ir.actions.server.run, which the web client
reaches by id over call_kw and which any API caller can reach the same
way, so a hidden action stayed one RPC away from anybody who knew the
id. The gate therefore sits on run(), the single entry point for every
server action, which is also where a stock "Schedule Activity" action
would otherwise create activities on a model whose profile refuses
them: an action a person runs is that person's call, not a flow's.
"""
import logging

from odoo import models, _
from odoo.exceptions import AccessError

from .base_model import _eh_rpc_entry, _eh_rpc_rebase

_logger = logging.getLogger(__name__)


class IrActionsServer(models.Model):
    _inherit = "ir.actions.server"

    def run(self):
        self._eh_access_studio_check_hidden()
        scope = self._eh_access_studio_person_scope()
        if scope is None:
            return super().run()
        with scope:
            return super().run()

    def _eh_access_studio_person_scope(self):
        """Run an activity-scheduling action as the person's own call.

        The activity gate stands aside for business flows, which is what
        a server action looks like from the inside. A person pressing an
        action in the Actions menu is not a flow, so the call is
        re-based onto the model the action works on, and Hide Schedule
        Activity is judged as it would be for the chatter button.
        """
        if self.env.su or _eh_rpc_entry() is None:
            return None
        states = set(self.sudo().mapped("state"))
        if "next_activity" not in states:
            return None
        model_name = self.sudo()[:1].model_id.model or self._name
        return _eh_rpc_rebase(self.env.uid, model_name, "activity_schedule")

    def _eh_access_studio_check_hidden(self):
        if self.env.su or self.env.uid == 1 or "eh.access.profile" not in self.env:
            return
        try:
            hidden = set()
            for action in self.sudo():
                model_name = action.model_id.model
                if not model_name or model_name not in self.env:
                    continue
                lines = self.env[model_name]._eh_access_studio_model_lines()
                if not lines:
                    continue
                if action.id in set(lines.mapped("hidden_server_action_ids.id")):
                    hidden.add(action.display_name or str(action.id))
        except Exception:
            _logger.exception(
                "Access Studio: hidden server action lookup failed for %s",
                self.ids,
            )
            if self._eh_access_studio_fail_closed():
                raise AccessError(_(
                    "Your access profile could not be read, so this action"
                    " was refused. Contact your administrator."
                ))
            return
        if hidden:
            raise AccessError(_(
                "Your access profile does not allow you to run"
                " %(actions)s.", actions=", ".join(sorted(hidden)),
            ))
