# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: talking about a record is not editing it.

Odoo derives message access from WRITE access on the document, so a
profile that makes a model read only also silently stops the user
posting a message, logging a note or scheduling an activity on it.
That is almost never what an administrator means by read only: they
mean "do not change the data", not "do not communicate".

These entry points run under the module's internal marker, a
thread-local rather than a context key, so no caller can set it. The
marker waives only the write ACCESS CHECK core performs on the
document. Any real create, write or unlink that runs while the message
is posted is still gated (models/base_model.py, _eh_data_change), and
the field gate stays on throughout. So the message, the note and the
activity are recorded, and nothing a post hook tries to write on this
or any other record gets through.

Hide Send Message and Hide Log Note are enforced here, in message_post,
not only on the buttons. From Odoo 18 the chatter's own route
(/mail/message/post) checks the caller's access to the thread and then
posts as SUPERUSER, which every gate in this module stands aside for.
On those routes the person who made the request is the one judged.
"""
import logging

from odoo import SUPERUSER_ID, api, models, _
from odoo.exceptions import AccessError

from .base_model import (
    _eh_access_studio_internal, _eh_internal_state, _eh_request_info,
    _eh_rpc_entry,
)

_logger = logging.getLogger(__name__)

# Routes that post the CALLER's message, as superuser from Odoo 18, once
# core has checked the caller may post on the thread. The portal chatter
# routes are included for customers under a profile.
_EH_POST_ROUTES = frozenset((
    "/mail/message/post", "/mail/chatter_post", "/portal/chatter_post",
))


# Discuss channels are conversations, not the chatter of a record: the
# chatter toggles never apply to them, or Hide Chatter (everywhere)
# would silence direct messages and channels too.
_EH_CHATTER_EXEMPT_MODELS = frozenset(("discuss.channel", "mail.channel"))

# Models a person uses to put their own words into a chatter: the full
# composer (comment and mass mail modes) and "send later". A post made
# while one of these is the outermost call is the person's post.
_EH_POSTING_MODELS = frozenset((
    "mail.compose.message", "mail.scheduled.message", "sms.composer",
    # A template holds a body the user wrote; send_mail posts it.
    "mail.template",
))

# Message kinds a person writes. A framework flow posting one of these as
# the person is still the person's post; 'notification' and the like are
# the framework's own and only judged on a direct call.
_EH_USER_KINDS = frozenset(("comment", "sms", "email", "email_outgoing"))

# mail.mail carries a mail.message by delegation (_inherits), so creating
# one with a model and res_id writes into that record's chatter too.
_EH_MESSAGE_CARRIERS = frozenset(("mail.message", "mail.mail"))

# Method-name marks of a call that is ABOUT messaging rather than about
# the business operation: activity_send_mail, message_mail_with_source,
# message_notify, action_send_mail and their kin.
_EH_POSTING_METHOD_HINTS = ("send_mail", "message_mail", "notify", "send_message")

# A model whose name carries one of these words is a dialog for sending
# something: account.move.send, account.invoice.send, sms.composer,
# mail.compose.message.
_EH_SENDING_WORDS = frozenset((
    "send", "composer", "compose",
    # Sharing a record and inviting a follower both post the caller's
    # own words into the chatter and mail them out.
    "share", "invite",
))

# mail.thread's own public entry points that put a message into a
# chatter from material the caller supplies.
_EH_THREAD_ENTRY_METHODS = frozenset((
    "message_process", "message_route", "message_new", "message_update",
))

# The routes that rewrite a posted message's body.
_EH_EDIT_ROUTES = frozenset(("/mail/message/update_content",))


def _eh_chatter_flags(env, model_name):
    """(hide_send, hide_log, hide_activity) for env's user on model_name.

    A lookup failure answers "hidden" for a user who is named on a
    profile, and "not hidden" for anyone else, the same posture as the
    data gates.
    """
    if env.su or env.uid == SUPERUSER_ID or "eh.access.profile" not in env:
        return (False, False, False)
    if model_name in _EH_CHATTER_EXEMPT_MODELS:
        return (False, False, False)
    profile_model = env["eh.access.profile"].sudo()
    try:
        return profile_model._chatter_visibility_for(
            env.uid,
            profile_model._eh_safe_company_id(),
            profile_model._today_for_cache_key(),
            model_name,
        )
    except Exception:
        _logger.exception("Access Studio: chatter flag lookup failed")
        try:
            closed = env[model_name]._eh_access_studio_fail_closed()
        except Exception:
            closed = False
        return (closed, closed, closed)


def _eh_is_posting_call(entry, model_name=None):
    """Is the outermost RPC call one that puts the caller's own words
    into a chatter?

    Naming the entry points one by one kept missing public methods that
    take a template or a body from the caller: message_post and its
    variants, the composers, "send later", mail.template.send_mail,
    activity_send_mail. They have one thing in common: the call is about
    mail or messaging, not about the business operation the record is
    for. A business flow that happens to post (confirming an order,
    validating a picking) is not judged, which is the documented line.
    """
    if not entry:
        return False
    _uid, entry_model, method = entry
    if entry_model in _EH_POSTING_MODELS or entry_model in _EH_MESSAGE_CARRIERS:
        return True
    if not isinstance(method, str) or not isinstance(entry_model, str):
        return False
    # A WIZARD whose whole job is sending: the invoice "Send & Print"
    # dialog, "Send by email", the SMS dialog. It is opened, filled in
    # and confirmed by the person, so what it posts is theirs. A wizard
    # that merely happens to post (a backorder, a refusal reason) is not
    # matched, because its name is about the operation, not the message.
    if _eh_is_sending_wizard(entry_model):
        return True
    if method in _EH_THREAD_ENTRY_METHODS:
        # The mail gateway entry points are public on every thread
        # model, so the call can name any of them, not only mail.thread.
        return True
    if model_name is not None and entry_model != model_name:
        return False
    if method.startswith("message_post"):
        return True
    return any(hint in method for hint in _EH_POSTING_METHOD_HINTS)


def _eh_is_sending_wizard(model_name):
    parts = model_name.split(".")
    return bool(_EH_SENDING_WORDS & set(parts))


def _eh_entry_env(env, uid):
    """env for the person who made the outermost RPC call, or None."""
    if not uid or uid == SUPERUSER_ID:
        return None
    if not env.su and env.uid == uid:
        return env
    return env(user=uid, su=False)


def _eh_is_note(env, subtype_xmlid=None, subtype_id=None, default=True):
    """Would core file this post as an internal note?

    A note is a post on an INTERNAL subtype: mail.mt_note, and any other
    subtype flagged internal, which shows to employees only exactly like
    a note. message_post files a post that names no subtype as a note, so
    an omitted subtype answers `default`.
    """
    subtype = env["mail.message.subtype"]
    if subtype_xmlid:
        xmlid = subtype_xmlid if "." in subtype_xmlid else "mail." + subtype_xmlid
        subtype = env.ref(xmlid, raise_if_not_found=False) or subtype
    elif subtype_id:
        subtype = subtype.browse(subtype_id).exists()
    else:
        return default
    if not subtype:
        return default
    return bool(subtype.sudo().internal)


def _eh_refuse_post(env, model_name, is_note):
    """Raise when env's user may not post this kind of entry here."""
    hide_send, hide_log, _hide_activity = _eh_chatter_flags(env, model_name)
    description = model_name
    if model_name in env:
        description = env[model_name]._description or model_name
    if is_note and hide_log:
        raise AccessError(_(
            "Your access profile does not allow you to log notes"
            " on %(model)s.", model=description,
        ))
    if not is_note and hide_send:
        raise AccessError(_(
            "Your access profile does not allow you to send"
            " messages on %(model)s.", model=description,
        ))


def _eh_refuse_any_post(env, model_name):
    """Raise when env's user may post NEITHER messages nor notes here."""
    hide_send, hide_log, _hide_activity = _eh_chatter_flags(env, model_name)
    if hide_send and hide_log:
        _eh_refuse_post(env, model_name, is_note=False)


def _eh_refuse_activity(env, model_name):
    if _eh_chatter_flags(env, model_name)[2]:
        description = model_name
        if model_name in env:
            description = env[model_name]._description or model_name
        raise AccessError(_(
            "Your access profile does not allow you to schedule"
            " activities on %(model)s.", model=description,
        ))


def _eh_actor_env(env, routes=None):
    """The environment of the person behind this call.

    env itself unless it is sudo. Under sudo, the requester's own
    environment when the request came in on one of `routes` (any route
    when `routes` is None); otherwise None, meaning genuine server-side
    work with no person to judge.
    """
    if not env.su and env.uid != SUPERUSER_ID:
        return env
    info = _eh_request_info()
    if not info:
        return None
    uid, path, _model, _method = info
    if not uid or uid == SUPERUSER_ID:
        return None
    if routes is not None and path not in routes:
        return None
    return env(user=uid, su=False)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _eh_access_studio_poster_env(self):
        """(env, any_type) for the person authoring this post, or None.

        Only the OUTERMOST post of a call is the person's own. Posts
        made from inside it (post hooks, subscriptions, other records a
        flow touches) are the framework's, and are left alone.

        any_type is True when the post comes straight from the person:
        the chatter's post routes, a direct call to message_post or any
        of its public variants (message_post_with_source and the like)
        over ANY transport, and the full composer. There the
        message_type the caller chose does not matter, because a
        'notification' shows in the chatter just like a message.
        Otherwise only a 'comment' is treated as user-authored.
        """
        info = _eh_request_info()
        if info and info[1] in _EH_POST_ROUTES:
            return _eh_actor_env(self.env), True
        entry = _eh_rpc_entry()
        if entry:
            if _eh_is_posting_call(entry, self._name):
                return _eh_entry_env(self.env, entry[0]), True
            # A business call that happens to post: validating a
            # picking sends its SMS confirmation, a stage change records
            # what happened, a document logs why it was refused. Those
            # words are the flow's, not the person's, and refusing them
            # stopped the operation itself. They are not judged.
            return None, False
        if self.env.su or self.env.uid == SUPERUSER_ID:
            return None, False
        # No RPC call at all: a shell, a test, a server action. The
        # conservative reading, as for Duplicate.
        return self.env, False

    def _eh_access_studio_chatter_flags(self):
        """(hide_send, hide_log, hide_activity) for the person posting."""
        env, _any_type = self._eh_access_studio_poster_env()
        if env is None:
            return (False, False, False)
        return _eh_chatter_flags(env, self._name)

    def _eh_access_studio_check_post(self, kwargs):
        env, any_type = self._eh_access_studio_poster_env()
        if env is None:
            return
        if not any_type and kwargs.get("message_type", "notification") not in _EH_USER_KINDS:
            return
        _eh_refuse_post(env, self._name, _eh_is_note(
            self.env, kwargs.get("subtype_xmlid"), kwargs.get("subtype_id"),
        ))

    def message_post(self, **kwargs):
        # Hide Send Message and Hide Log Note used to remove the buttons
        # and nothing else, so any RPC caller could still post.
        depth = getattr(_eh_internal_state, "post_depth", 0)
        if not depth:
            self._eh_access_studio_check_post(kwargs)
        _eh_internal_state.post_depth = depth + 1
        try:
            with _eh_access_studio_internal(fields=False, write_op=True):
                return super().message_post(**kwargs)
        finally:
            _eh_internal_state.post_depth = depth

    def _eh_access_studio_check_chatter_command(self, comodel_name, command):
        """message_ids / activity_ids commands in the record's own payload.

        write({'message_ids': [(0, 0, {...})]}) creates a message in the
        chatter without message_post, and activity_ids does the same for
        activities. The payload came from the caller, so it is the
        caller's post, whatever method carried it.
        """
        if self.env.su or self.env.uid == SUPERUSER_ID:
            return
        if not isinstance(command, (list, tuple)) or not command:
            return
        code = command[0]
        if comodel_name == "mail.message":
            if code == 0 and len(command) > 2 and isinstance(command[2], dict):
                values = command[2]
                _eh_refuse_post(self.env, self._name, _eh_is_note(
                    self.env, None, values.get("subtype_id"),
                    default=values.get("message_type") not in (
                        "comment", "email", "email_outgoing",
                    ),
                ))
            elif code in (1, 4, 6):
                # Editing an existing message, or moving one into this
                # chatter, puts words there just as posting does. It is
                # judged as the KIND of message it is or becomes, not
                # only when both kinds are hidden: a Hide Log Note
                # profile let a restricted user rewrite anyone's note.
                values = command[2] if len(command) > 2 and isinstance(command[2], dict) else {}
                if code == 1:
                    targets = [command[1]] if len(command) > 1 else []
                elif code == 4:
                    targets = [command[1]] if len(command) > 1 else []
                else:
                    targets = list(command[2] or []) if len(command) > 2 else []
                messages = self.env["mail.message"].sudo().browse(
                    [target for target in targets if isinstance(target, int)]
                ).exists()
                if not messages:
                    _eh_refuse_any_post(self.env, self._name)
                for message in messages:
                    subtype_id = values.get("subtype_id") or message.subtype_id.id
                    message_type = values.get("message_type") or message.message_type
                    _eh_refuse_post(self.env, self._name, _eh_is_note(
                        self.env, None, subtype_id,
                        default=message_type not in (
                            "comment", "email", "email_outgoing",
                        ),
                    ))
        elif comodel_name == "mail.activity" and code in (0, 1, 4, 6):
            # A command can carry the activity to ANOTHER record: judge
            # the model it lands on, as well as this one.
            target = self._name
            values = command[2] if len(command) > 2 and isinstance(command[2], dict) else {}
            if values.get("res_model"):
                target = values["res_model"]
            elif values.get("res_model_id"):
                target = self.env["ir.model"].sudo().browse(values["res_model_id"]).model or target
            _eh_refuse_activity(self.env, self._name)
            if target != self._name:
                _eh_refuse_activity(self.env, target)

    def message_notify(self, **kwargs):
        # A public method: over RPC it sends the caller's own text to
        # anyone they name, from this record. The caller can also name
        # the record it is sent FROM, so that model is judged too.
        entry = _eh_rpc_entry()
        if entry and entry[2] == "message_notify":
            env = _eh_entry_env(self.env, entry[0])
            if env is not None:
                targets = {self._name}
                named = kwargs.get("model")
                if named:
                    targets.add(named)
                for model_name in targets:
                    if model_name in env:
                        _eh_refuse_post(env, model_name, is_note=False)
        return super().message_notify(**kwargs)

    def _get_thread_with_access(self, *args, **kwargs):
        """The chatter's own route asks for WRITE access before posting.

        /mail/message/post resolves the record with mode 'write' (the
        model's _mail_post_access) before it posts, outside the waiver
        this module puts around posting itself. On a read-only model, or
        a record a read-only domain rule covers, the route therefore
        answered 404 and the Send and Log buttons failed, while the same
        post over RPC worked. Talking about a record is not editing it,
        so the resolution runs under the same narrow waiver.
        """
        parent = getattr(super(), "_get_thread_with_access", None)
        if parent is None:
            # Odoo 16 and 17 resolve the thread in the controller instead.
            return self.browse()
        with _eh_access_studio_internal(fields=False, write_op=True):
            return parent(*args, **kwargs)

    def _message_update_content(self, message, *args, **kwargs):
        # Editing a message you posted (the pencil in the chatter) puts
        # new words into the chatter just as posting does. The route
        # writes as superuser, so the person on the route is judged.
        env = _eh_actor_env(self.env, routes=_EH_EDIT_ROUTES)
        if env is not None and message:
            is_note = _eh_is_note(
                self.env, None, message.sudo().subtype_id.id, default=True,
            )
            _eh_refuse_post(env, self._name, is_note)
        return super()._message_update_content(message, *args, **kwargs)

    def _message_log(self, **kwargs):
        with _eh_access_studio_internal(fields=False, write_op=True):
            return super()._message_log(**kwargs)

    def _message_log_batch(self, *args, **kwargs):
        # The mass modes of the mail and SMS composers keep a copy of what
        # they sent in each record's chatter through this batch log. It
        # is the person's text, so it answers to the same rules as a post
        # whenever a composer is the outermost call.
        entry = _eh_rpc_entry()
        if _eh_is_posting_call(entry) and self:
            env = _eh_entry_env(self.env, entry[0])
            if env is not None:
                _eh_refuse_post(env, self._name, _eh_is_note(
                    self.env, None, kwargs.get("subtype_id"), default=True,
                ))
        with _eh_access_studio_internal(fields=False, write_op=True):
            return super()._message_log_batch(*args, **kwargs)

    def message_subscribe(self, *args, **kwargs):
        with _eh_access_studio_internal(fields=False, write_op=True):
            return super().message_subscribe(*args, **kwargs)


class MailMessage(models.Model):
    """A message can be created without message_post at all: mail.message
    create is a public method, and a message with model and res_id set
    shows in that record's chatter like any other. A direct create, over
    any transport, answers to the same Send Message and Log Note rules
    on the record it lands on."""

    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, vals_list):
        entry = _eh_rpc_entry()
        if entry:
            env = _eh_entry_env(self.env, entry[0])
            if env is not None:
                for vals in vals_list:
                    model_name = (vals or {}).get("model")
                    if not model_name or not (vals or {}).get("res_id"):
                        continue
                    # Judged per message, against the model it lands on:
                    # a flow that notifies people about ANOTHER record
                    # (a lunch order telling employees about a supplier)
                    # is not the caller writing in that chatter.
                    if not _eh_is_posting_call(entry, model_name):
                        continue
                    # An email or a comment that names no subtype shows
                    # as a message; anything else as a note.
                    _eh_refuse_post(env, model_name, _eh_is_note(
                        self.env, None, vals.get("subtype_id"),
                        default=vals.get("message_type") not in (
                            "comment", "email", "email_outgoing",
                        ),
                    ))
        return super().create(vals_list)

    def write(self, vals):
        entry = _eh_rpc_entry()
        if (
            _eh_is_posting_call(entry)
            # A subtype or type change turns a message the profile allows
            # into one it does not: a comment flipped to an internal note.
            and {"body", "model", "res_id", "subtype_id", "message_type",
                 "is_internal"} & set(vals or ())
        ):
            env = _eh_entry_env(self.env, entry[0])
            if env is not None:
                for message in self.sudo():
                    model_name = vals.get("model") or message.model
                    if not model_name:
                        continue
                    _eh_refuse_post(env, model_name, _eh_is_note(
                        self.env, None,
                        vals.get("subtype_id") or message.subtype_id.id,
                    ))
        return super().write(vals)
