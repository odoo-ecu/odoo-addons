# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: scheduling an activity is not editing the record,
and Hide Schedule Activity is enforced where activities are made.

Every way a person schedules an activity ends in mail.activity.create:
the chatter's schedule wizard, activity_schedule() over RPC, and the
activity form itself. The gate therefore sits on create (and on a write
that moves an activity onto another record), not on activity_schedule,
which mail.activity.mixin defines without calling super and which core
often runs as superuser.

A person is judged only on a call they made directly: the activity
models themselves, the schedule wizard, or activity_schedule. Business
flows that create activities as a side effect (a confirmation that
assigns a follow-up, a done activity that chains the next one) are the
framework's, not the person scheduling, and are left alone. With no
HTTP request at all the call is treated as the person's own.

Scheduling also makes core check WRITE access on the document, so the
same narrow waiver as the chatter applies: the write half of the
operation gate only, for the duration of the activity write.
"""
from odoo import SUPERUSER_ID, api, models, _
from odoo.exceptions import AccessError

from .base_model import (
    _eh_access_studio_internal, _eh_request_info, _eh_rpc_entry,
)
from .mail_thread import _eh_chatter_flags, _eh_entry_env, _eh_refuse_post

# Calls on the activity model that make or move an activity. Marking one
# done (action_feedback and friends) can chain the next activity by
# configuration; that is the activity type's doing, not the person's.
_EH_ACTIVITY_METHODS = frozenset((
    "create", "web_save", "web_save_multi", "write", "copy", "name_create",
    "load",
))
# Calls that schedule on a person's behalf by their nature: the schedule
# wizard, and a meeting linked to a record, which Odoo files as an
# activity on that record.
_EH_SCHEDULE_WIZARDS = frozenset(("mail.activity.schedule", "calendar.event"))
_EH_ACTIVITY_TARGET_KEYS = frozenset(("res_model", "res_model_id", "res_id"))

# The routes that drop a file onto a record's chatter.
_EH_UPLOAD_ROUTES = frozenset((
    "/mail/attachment/upload", "/portal/attachment/add",
))


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def _eh_access_studio_scheduler_env(self):
        """Environment of the person scheduling, or None if nobody is.

        Judged on the outermost RPC call of this thread, whatever
        transport carried it: the web client, XML-RPC, /jsonrpc or
        /json/2 (see base_model._eh_rpc_entry).
        """
        entry = _eh_rpc_entry()
        if entry is None:
            if _eh_request_info() is not None:
                # An HTTP route that is not an RPC call made this
                # activity: the route's own logic, not a person's call.
                return None
            if self.env.su or self.env.uid == SUPERUSER_ID:
                return None
            # A shell, a test, a server action: the conservative reading.
            return self.env
        uid, rpc_model, rpc_method = entry
        direct = (
            rpc_method == "activity_schedule"
            or rpc_model in _EH_SCHEDULE_WIZARDS
            or (rpc_model == self._name and rpc_method in _EH_ACTIVITY_METHODS)
        )
        if not direct:
            return None
        return _eh_entry_env(self.env, uid)

    def _eh_access_studio_check_schedule(self):
        """Refuse these activities if their person may not schedule."""
        env = self._eh_access_studio_scheduler_env()
        if env is None:
            return
        for model_name in set(self.sudo().mapped("res_model")):
            if not model_name or model_name not in env:
                continue
            if _eh_chatter_flags(env, model_name)[2]:
                raise AccessError(_(
                    "Your access profile does not allow you to schedule"
                    " activities on %(model)s.",
                    model=env[model_name]._description or model_name,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        with _eh_access_studio_internal(fields=False, write_op=True):
            activities = super().create(vals_list)
        activities._eh_access_studio_check_schedule()
        return activities

    def write(self, vals):
        with _eh_access_studio_internal(fields=False, write_op=True):
            result = super().write(vals)
        if _EH_ACTIVITY_TARGET_KEYS & set(vals or ()):
            # Moving an activity onto another record schedules it there.
            self._eh_access_studio_check_schedule()
        return result

    def unlink(self):
        with _eh_access_studio_internal(fields=False, write_op=True):
            return super().unlink()

    def action_feedback(self, *args, **kwargs):
        self._eh_access_studio_check_feedback(
            kwargs.get("feedback", args[0] if args else False),
        )
        with _eh_access_studio_internal(fields=False, write_op=True):
            return super().action_feedback(*args, **kwargs)

    def action_feedback_schedule_next(self, *args, **kwargs):
        self._eh_access_studio_check_feedback(
            kwargs.get("feedback", args[0] if args else False),
        )
        with _eh_access_studio_internal(fields=False, write_op=True):
            return super().action_feedback_schedule_next(*args, **kwargs)

    def _eh_access_studio_check_feedback(self, feedback):
        """Marking an activity done is allowed; the free text the person
        adds lands in the record's chatter as a note, so it answers to
        Hide Log Note."""
        if not feedback:
            return
        entry = _eh_rpc_entry()
        # activity_feedback is a public method of the mixin, so the call
        # can arrive on the DOCUMENT's model just as well as on the
        # activity's.
        if entry is None or (
            entry[1] != self._name
            and not (isinstance(entry[2], str) and "feedback" in entry[2])
        ):
            return
        env = _eh_entry_env(self.env, entry[0])
        if env is None:
            return
        for model_name in set(self.sudo().mapped("res_model")):
            if model_name and model_name in env:
                _eh_refuse_post(env, model_name, is_note=True)


class IrAttachment(models.Model):
    """Attaching a file makes core check WRITE access on the record it
    is attached to, so under Read-Only Mode attachments failed even
    though ir.attachment itself is exempt. Same narrow waiver as the
    chatter: the access check on the document only, never a real write
    to its model."""

    _inherit = "ir.attachment"

    @api.model_create_multi
    def create(self, vals_list):
        # An attachment with res_field set is not a document attached to
        # a record: it IS the storage of a binary field on that record,
        # an image or a file. Creating one is writing that field, so it
        # gets no waiver and answers to the field lock.
        if any((vals or {}).get("res_field") for vals in vals_list):
            for vals in vals_list:
                self._eh_access_studio_check_binary_field(
                    vals.get("res_model"), vals.get("res_field"),
                    vals.get("res_id"),
                )
            return super().create(vals_list)
        self._eh_access_studio_check_chatter_upload(vals_list)
        with _eh_access_studio_internal(fields=False, write_op=True):
            return super().create(vals_list)

    def _eh_access_studio_check_chatter_upload(self, vals_list):
        """A file dropped on a chatter that is hidden entirely.

        The upload route posts the attachment as superuser, so the
        person on the route is the one judged, and only when BOTH
        message kinds are hidden: that is the whole chatter, where no
        attachment belongs either. With the chatter present, attaching
        files is part of talking about a record.
        """
        info = _eh_request_info()
        entry = _eh_rpc_entry()
        on_route = bool(info) and info[1] in _EH_UPLOAD_ROUTES
        direct = bool(entry) and entry[1] == "ir.attachment"
        if not on_route and not direct:
            return
        env = _eh_entry_env(self.env, info[0] if on_route else entry[0])
        if env is None:
            return
        for vals in vals_list:
            model_name = (vals or {}).get("res_model")
            if not model_name or not vals.get("res_id"):
                continue
            hide_send, hide_log, _activity = _eh_chatter_flags(env, model_name)
            if hide_send and hide_log:
                raise AccessError(_(
                    "Your access profile does not allow you to post on"
                    " %(model)s, so files cannot be attached there"
                    " either.",
                    model=env[model_name]._description or model_name
                    if model_name in env else model_name,
                ))

    def write(self, vals):
        for attachment in self:
            if attachment.res_field:
                self._eh_access_studio_check_binary_field(
                    attachment.res_model, attachment.res_field,
                    attachment.res_id,
                )
        return super().write(vals)

    def _eh_access_studio_check_binary_field(self, res_model, res_field, res_id):
        """A binary field stored as an attachment is still that field."""
        if not res_model or not res_field or self.env.su:
            return
        if res_model not in self.env:
            return
        target = self.env[res_model].browse(res_id or [])
        if not hasattr(target, "_eh_access_studio_check_field_writes"):
            return
        # -1 never matches a stored value, so a lock reads it as a change.
        target._eh_access_studio_check_field_writes({res_field: -1}, "write")
        reason = target._eh_access_studio_operation_blocked("write")
        if reason:
            raise target._eh_access_studio_make_access_error("write", reason)
