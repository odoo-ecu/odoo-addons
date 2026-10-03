# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Employee requests with server-checked ownership and approval transitions."""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from .eh_access_role import (
    _GOVERNANCE_TOKEN, _assert_company, _assert_manager, _manager, _record_access,
)


class EhAccessRequest(models.Model):
    _name = "eh.access.request"
    _description = "Access Studio Role Request"
    _order = "id desc"
    _rec_name = "role_id"

    user_id = fields.Many2one("res.users", required=True, readonly=True, default=lambda self: self.env.user, ondelete="restrict", index=True)
    role_id = fields.Many2one("eh.access.role", required=True, ondelete="restrict", domain=[("requestable", "=", True), ("active", "=", True)])
    company_id = fields.Many2one(related="role_id.company_id", store=True, index=True)
    reason = fields.Text(required=True)
    starts_at = fields.Datetime(required=True, default=fields.Datetime.now)
    ends_at = fields.Datetime(required=True)
    state = fields.Selection([("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved"),
                              ("rejected", "Rejected"), ("cancelled", "Cancelled"), ("revoked", "Revoked")],
                             required=True, default="draft", readonly=True, index=True)
    submitted_at = fields.Datetime(readonly=True)
    decided_at = fields.Datetime(readonly=True)
    approver_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    decision_reason = fields.Text(string="Decision Reason")
    assignment_ids = fields.One2many("eh.access.assignment", "request_id", readonly=True, string="Granted Assignments")

    @api.constrains("starts_at", "ends_at", "reason", "role_id", "user_id")
    def _check_request(self):
        for request in self:
            if request.ends_at <= request.starts_at:
                raise ValidationError(_("Expiry must be later than the start time."))
            if not (request.reason or "").strip():
                raise ValidationError(_("Explain why this access is needed."))
            if request.user_id.share or request.company_id not in request.user_id.company_ids:
                raise ValidationError(_("Requesters must be internal users with access to the role's company."))

    def _assert_owner(self):
        _record_access(self, "read")
        if any(record.user_id.id != self.env.uid for record in self):
            raise AccessError(_("You can only submit or edit your own requests."))
        for record in self:
            _assert_company(self.env, record.company_id)

    def _assert_requested_role(self):
        for request in self:
            role = request.role_id
            _record_access(role, "read")
            _assert_company(self.env, role.company_id)
            if not role.active or not role.requestable:
                raise UserError(_("This role is not available for employee requests."))

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.user.share:
            raise AccessError(_("Only internal users may request roles."))
        allowed = {"role_id", "reason", "starts_at", "ends_at"}
        safe_vals = []
        for vals in vals_list:
            vals = dict(vals)
            role = self.env["eh.access.role"].browse(vals.get("role_id"))
            _record_access(role, "read")
            defaults = {"user_id": self.env.uid, "state": "draft", "company_id": role.company_id.id,
                        "submitted_at": False, "decided_at": False, "approver_id": False, "decision_reason": False}
            for name, expected in defaults.items():
                if name in vals and vals.pop(name) != expected:
                    raise AccessError(_("Request identity, company and status are assigned by Access Studio."))
            if set(vals) - allowed:
                raise AccessError(_("Request identity, company and status are assigned by Access Studio."))
            safe_vals.append(dict(vals, user_id=self.env.uid))
        records = super().create(safe_vals)
        records._assert_requested_role()
        for request in records:
            self.env["eh.access.audit"]._record("request_created", request, request.user_id,
                                               {"role_id": request.role_id.id, "reason": request.reason})
        return records

    def write(self, vals):
        if self.env.context.get("_eh_governance_token") is _GOVERNANCE_TOKEN:
            return super().write(vals)
        _record_access(self, "write")
        if set(vals) == {"decision_reason"}:
            _assert_manager(self.env)
            for request in self:
                _assert_company(self.env, request.company_id)
                if request.state != "submitted" or request.user_id.id == self.env.uid:
                    raise AccessError(_("Only another manager reviewing a submitted request can enter a decision reason."))
            return super().write(vals)
        self._assert_owner()
        if set(vals) - {"role_id", "reason", "starts_at", "ends_at"} or any(record.state != "draft" for record in self):
            raise AccessError(_("Only draft request details can be edited. Use the workflow buttons to change status."))
        result = super().write(vals)
        self._assert_requested_role()
        return result

    def unlink(self):
        raise AccessError(_("Request history cannot be deleted. Cancel draft or submitted requests instead."))

    def _lock(self):
        self.env.cr.execute("SELECT id FROM eh_access_request WHERE id = ANY(%s) ORDER BY id FOR UPDATE", [self.ids])
        self.invalidate_recordset() if hasattr(self, "invalidate_recordset") else self.invalidate_cache()

    def _transition(self, values):
        return self.with_context(_eh_governance_token=_GOVERNANCE_TOKEN).write(values)

    def action_submit(self):
        self._assert_owner()
        self._lock()
        self._assert_requested_role()
        if any(record.state != "draft" for record in self):
            raise UserError(_("Only draft requests can be submitted."))
        if any(record.ends_at <= fields.Datetime.now() for record in self):
            raise UserError(_("Request expiry must be in the future."))
        self._transition({"state": "submitted", "submitted_at": fields.Datetime.now()})
        for request in self:
            self.env["eh.access.audit"]._record("request_submitted", request, request.user_id)
        return True

    def _assert_decision(self):
        _assert_manager(self.env)
        _record_access(self, "write")
        self._lock()
        for request in self:
            _assert_company(self.env, request.company_id)
            if request.user_id.id == self.env.uid:
                raise AccessError(_("You cannot approve or reject your own access request. Another manager must review it."))
            if request.state != "submitted":
                raise UserError(_("Only submitted requests can be decided."))

    def action_approve(self):
        self._assert_decision()
        self._assert_requested_role()
        for request in self:
            if request.ends_at <= fields.Datetime.now():
                raise UserError(_("This request has expired. Ask the employee to submit a new request."))
            request.role_id._assert_grantable()
            self.env["eh.access.assignment"].with_context(_eh_governance_token=_GOVERNANCE_TOKEN).create({
                "role_id": request.role_id.id, "user_id": request.user_id.id,
                "starts_at": request.starts_at, "ends_at": request.ends_at,
                "reason": request.reason, "request_id": request.id,
            })
            request._transition({"state": "approved", "decided_at": fields.Datetime.now(), "approver_id": self.env.uid})
            self.env["eh.access.audit"]._record("request_approved", request, request.user_id,
                                               {"decision_reason": request.decision_reason})
        return True

    def action_reject(self):
        self._assert_decision()
        if any(not (record.decision_reason or "").strip() for record in self):
            raise UserError(_("Enter a decision reason before rejecting a request."))
        self._transition({"state": "rejected", "decided_at": fields.Datetime.now(), "approver_id": self.env.uid})
        for request in self:
            self.env["eh.access.audit"]._record("request_rejected", request, request.user_id,
                                               {"decision_reason": request.decision_reason})
        return True

    def action_cancel(self):
        self._assert_owner()
        self._lock()
        if any(record.state not in ("draft", "submitted") for record in self):
            raise UserError(_("Only draft or submitted requests can be cancelled."))
        self._transition({"state": "cancelled"})
        for request in self:
            self.env["eh.access.audit"]._record("request_cancelled", request, request.user_id)
        return True

    def action_revoke(self):
        _assert_manager(self.env)
        _record_access(self, "write")
        self._lock()
        for request in self:
            _assert_company(self.env, request.company_id)
            if request.state != "approved":
                raise UserError(_("Only approved requests can be revoked."))
            request.assignment_ids.action_revoke()
        self._transition({"state": "revoked"})
        for request in self:
            self.env["eh.access.audit"]._record("request_revoked", request, request.user_id)
        return True
