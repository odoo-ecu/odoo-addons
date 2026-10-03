# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: hidden reports, at the render call.

Taking a report out of the cog menu is presentation. The document is
still produced by /report/pdf/<name>/<ids> and /report/html/..., which
go straight to the report record, so a hidden report stayed one URL
away from anybody who had ever seen it.

The gate sits on _render, the single dispatcher every report type goes
through (addons/base/models/ir_actions_report.py), so it covers PDF,
HTML and text alike, and on report_action so the button answers the
same rule.

_get_report is used to resolve whatever the caller passed, since the
render entry points accept an id, an xml id, a record or a report name.
"""
import logging

from odoo import models, SUPERUSER_ID, _
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def _eh_access_studio_effective_env(self):
        """The environment of the person the report is for.

        The portal renders documents as superuser (it has already
        checked the customer's own access to the record), so gating on
        env.su skipped every hidden-report rule on that route. When the
        environment is sudo but a real, non-superuser person made the
        request, their environment is the one that decides.
        """
        if not self.env.su and self.env.uid != SUPERUSER_ID:
            return self.env
        if self.env.su and self.env.uid and self.env.uid != SUPERUSER_ID:
            # A sudo environment that still carries a real person's uid
            # is that person's, request or no request: a scheduled job
            # running as them renders their documents.
            return self.env(su=False)
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
        return env(su=False)

    def _eh_access_studio_check_report(self, report_ref):
        env = self._eh_access_studio_effective_env()
        if env is None:
            # Genuine server-side rendering with no person behind it.
            return
        if "eh.access.profile" not in env:
            return
        try:
            report = self.sudo()._get_report(report_ref)
        except Exception:
            # An unresolvable reference is core's problem to report.
            return
        if not report:
            return
        # The configuration screen offers reports by the model they are
        # BOUND to (binding_model_id), while a report's own `model` can
        # differ. Checking only one of them let a report configured as
        # hidden still render, so both are consulted.
        candidates = []
        if report.model:
            candidates.append(report.model)
        binding = getattr(report, "binding_model_id", False)
        if binding and binding.model and binding.model not in candidates:
            candidates.append(binding.model)
        for model_name in candidates:
            self._eh_access_studio_check_report_for_model(report, model_name, env)

    def _eh_access_studio_check_report_for_model(self, report, model_name, env=None):
        env = env or self.env
        if not model_name or model_name not in env:
            return
        model = env[model_name]
        if not hasattr(model, "_eh_access_studio_model_lines"):
            return
        try:
            lines = model._eh_access_studio_model_lines_strict()
        except Exception:
            _logger.exception(
                "Access Studio: model-line lookup failed for report %s",
                report.id,
            )
            # FAIL CLOSED for a restricted person: an error here must not
            # turn a hidden report back into a visible one.
            if model._eh_access_studio_fail_closed():
                raise AccessError(_(
                    "Your access profile could not be read, so this report"
                    " was not produced."
                ))
            return
        if not lines:
            return
        hidden = lines.mapped("hidden_report_action_ids")
        # Match on the template as well as the action record: several
        # report actions can render the same report_name, and hiding one
        # of them should not leave its siblings printing the same page.
        hidden_names = set(hidden.mapped("report_name"))
        if report.id in set(hidden.ids) or report.report_name in hidden_names:
            raise AccessError(_(
                "Your access profile does not allow you to run the"
                " %(report)s report.",
                report=report.name or report.report_name,
            ))

    def _render(self, report_ref, res_ids, data=None):
        self._eh_access_studio_check_report(report_ref)
        return super()._render(report_ref, res_ids, data=data)

    # The /report/ controllers do not always go through _render: they
    # call the per-type renderer directly, so each one is gated too.

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        self._eh_access_studio_check_report(report_ref)
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

    def _render_qweb_html(self, report_ref, docids, data=None):
        self._eh_access_studio_check_report(report_ref)
        return super()._render_qweb_html(report_ref, docids, data=data)

    def _render_qweb_text(self, report_ref, docids, data=None):
        self._eh_access_studio_check_report(report_ref)
        return super()._render_qweb_text(report_ref, docids, data=data)

    def report_action(self, docids, data=None, config=True):
        self._eh_access_studio_check_report(self)
        return super().report_action(docids, data=data, config=config)
