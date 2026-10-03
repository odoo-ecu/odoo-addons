# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: response-shape overlays on `base`.

The post-processors in models/ir_ui_view.py walk the arch tag-by-tag.
Two operations don't fit that pattern and live here instead:

* _get_view: stamps view-level attributes (create / delete / edit /
  import / export_xlsx) on the root <list>, <form>, <kanban> nodes.
  These attributes are read by the JS layer to render or hide the
  respective buttons.

* get_views: prunes the toolbar dictionary returned to the client so
  that hidden report and server actions do not appear in the cog menu.

The _get_view_cache_key override is also here because cache-keying
applies to the response, not to a per-tag walk. Sharing the key by
profile rather than by uid means two users on the same profile share
the cached arch.
"""
import ast
import contextlib
import functools
import inspect
import logging
import sys
import threading

import psycopg2

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)


_VIEWS_WITH_CRUD = frozenset((
    "form", "list", "kanban", "calendar", "gantt", "pivot", "graph",
    "activity", "cohort", "grid", "map", "hierarchy",
))
_VIEWS_WITH_IMPORT_EXPORT = frozenset(("list", "kanban"))

# Read-Only Mode means "this person does not change business records".
# It does not mean "this person cannot use the software". Wizards are
# transient records, and posting a message, scheduling an activity,
# following a thread, uploading an attachment or saving a filter all
# write rows of their own. Blocking those turns a read-only profile into
# a broken session, so they are exempt by name.
_EH_READONLY_EXEMPT_NAMES = frozenset((
    "mail.message", "mail.followers", "mail.activity", "mail.notification",
    "mail.tracking.value", "mail.link.preview", "mail.message.reaction",
    "ir.attachment", "ir.filters", "ir.ui.view.custom", "ir.logging",
    "bus.presence", "bus.bus", "res.users.settings", "res.device",
    "res.device.log", "web.tour", "res.users.log",
    # Discuss: direct messages and channels. discuss.* from Odoo 17,
    # mail.channel before it.
    "discuss.channel", "discuss.channel.member", "discuss.channel.rtc.session",
    "mail.channel", "mail.channel.member", "mail.channel.partner",
    "mail.channel.rtc.session", "mail.guest",
))
_EH_READONLY_EXEMPT_PREFIXES = ("eh.access.",)

# Set while this module is deliberately driving an ORM operation on the
# user's behalf, so the field gate does not treat the framework's own
# payload as a user edit. A thread-local rather than a context key: the
# context arrives from the RPC payload verbatim, so a context flag would
# hand every caller a switch to turn the gate off.
_eh_internal_state = threading.local()

# Derived-field closure, memoised per (registry, model, hidden set). The
# answer depends only on the registry's field graph, so it is safe to
# keep for the life of the registry.
_EH_DERIVED_CACHE = {}

# many2many fields on the other model that share a relation table,
# memoised per (registry, model, field) for the same reason.
_EH_M2M_INVERSE_CACHE = {}

# Related fields of a model that mirror a field of ANOTHER model, memoised
# per (registry, model).
_EH_RELATED_TARGETS = {}

# {condition expression: frozenset of the names it mentions}
_EH_CONDITION_NAMES = {}

# Public methods that answer with data selected by a caller's domain,
# beyond the obvious search / read_group family (matched by name).
_EH_READ_METHODS = frozenset((
    "read", "web_read", "name_search", "web_name_search", "read_progress_bar",
    "export_data", "load",
    "search_panel_select_range", "search_panel_select_multi_range",
))

# Overrides must carry the same decorators as the method they replace:
# call_kw decides from them whether the first argument is record ids
# (odoo/service/model.py, `_api_model`), and Odoo 18 and 19 route
# @api.readonly methods to the read replica. api.readonly does not exist
# before 18.
_eh_readonly = getattr(api, "readonly", None) or (lambda method: method)

# Odoo 18 and 19 funnel every permission question through _check_access.
_EH_HAS_CHECK_ACCESS = hasattr(models.BaseModel, "_check_access")

# _read_group took (groupby, aggregates) from Odoo 17 on; Odoo 16's has
# the legacy (fields, groupby) shape and is left alone.
try:
    _EH_MODERN_READ_GROUP = "aggregates" in inspect.signature(
        models.BaseModel._read_group,
    ).parameters
except (AttributeError, TypeError, ValueError):
    _EH_MODERN_READ_GROUP = False

# Never hidden by DERIVATION, even when they depend on a hidden field.
# Blanking a record's name because its display string can include an
# email would blank it everywhere in the interface; these stay visible
# and only the fields the administrator named, and fields that carry
# their value, are withheld.
# The fields a record is PRINTED under. They are never pulled in by
# derivation (see _EH_NEVER_DERIVED), because an unrelated hidden field
# must not blank every record's name, but they follow the fields that
# FORM the name: hide the name itself or the parent a contact is filed
# under, and the printed name goes with it.
_EH_DISPLAY_FIELDS = ("display_name", "complete_name")

_EH_NEGATIVE_OPERATORS = frozenset((
    "!=", "not in", "not like", "not ilike", "not =like", "not =ilike",
))

_EH_NEVER_DERIVED = frozenset((
    "id", "display_name", "name", "complete_name", "write_date",
    "create_date", "__last_update",
))


@contextlib.contextmanager
def _eh_data_change(model_name):
    """Mark that an actual create / write / unlink of `model_name` runs.

    The chatter waiver (write_op) exists because posting a message makes
    core CHECK write access on the document. It must never cover a real
    data change OF that model made while the message is being posted:
    crm's post hook, for one, writes partner_id onto every open lead
    sharing an email, and under a blanket waiver a read-only user could
    rewrite leads by posting.

    Keyed by model, because creating the mail.message itself is a data
    change too, and it is exactly what triggers the legitimate write
    check on the document. That check is on a DIFFERENT model, so it
    stays waived; a write to the document's own model does not.
    """
    changing = getattr(_eh_internal_state, "changing", None)
    if changing is None:
        changing = _eh_internal_state.changing = []
    changing.append(model_name)
    try:
        yield
    finally:
        changing.pop()


@contextlib.contextmanager
def _eh_access_studio_internal(fields=True, write_op=False):
    """Stand the gates down for one of THIS module's own operations.

    Two separate switches, because the two callers need different
    things and conflating them opened a hole:

    * fields   - the FIELD gate only. copy() uses this, so a duplicate
                 may carry the source record's locked values. It must
                 NOT relax the operation gate, or Duplicate becomes a
                 way for a read-only user to create records.
    * write_op - the write half of the OPERATION gate. Posting a
                 message makes Odoo check write access on the document,
                 which is not an edit of the data. Create and delete are
                 never relaxed.
    """
    prev_fields = getattr(_eh_internal_state, "fields_off", False)
    prev_write = getattr(_eh_internal_state, "write_op_off", False)
    _eh_internal_state.fields_off = prev_fields or fields
    _eh_internal_state.write_op_off = prev_write or write_op
    try:
        yield
    finally:
        _eh_internal_state.fields_off = prev_fields
        _eh_internal_state.write_op_off = prev_write


def _eh_request_info():
    """(uid, path, rpc_model, rpc_method) of the current HTTP request.

    None outside a request. Core runs several user actions as superuser
    once it has checked the caller's access to the record (posting on a
    thread, rendering a portal report), and every gate here stands aside
    for the superuser, so those gates need to know who is really asking.

    The path is the route that was actually dispatched, so a caller can
    not choose it. rpc_model and rpc_method come from the payload, and
    are used ONLY to decide that a restriction APPLIES to a direct call,
    never to waive one: a caller who changes them calls something else.
    """
    try:
        from odoo.http import request
    except ImportError:
        return None
    if not request:
        return None
    try:
        uid = request.env.uid
        params = request.params or {}
        path = _eh_dispatched_route(request)
    except Exception:
        return None
    if not isinstance(params, dict):
        params = {}
    return uid, path, params.get("model"), params.get("method")


def _eh_dispatched_route(request):
    """The route Odoo matched, not the spelling the caller used.

    Odoo builds its routing map with strict_slashes=False, so
    /mail/message/post and /mail/message/post/ reach the same handler.
    Comparing the RAW path against a route therefore let one extra
    character walk past any gate keyed on it. Werkzeug records the rule
    it matched, which is canonical; when it is not available the path is
    normalised the same way (percent-encoding decoded, repeated slashes
    collapsed, trailing slash dropped).
    """
    rule = getattr(getattr(request.httprequest, "url_rule", None), "rule", None)
    if rule:
        return rule
    path = request.httprequest.path or ""
    try:
        from urllib.parse import unquote
        path = unquote(path)
    except Exception:
        pass
    while "//" in path:
        path = path.replace("//", "/")
    return path.rstrip("/") or "/"


# ---- the call a person made, whatever carried it -----------------------
#
# Several restrictions are about what a PERSON asks for, not about what a
# business flow does on their behalf: Hide Duplicate, Hide Schedule
# Activity, Hide Send Message and Log Note on an API call, copy_data read
# back as data. Telling the two apart used to rely on request.params,
# which only the web client's call_kw fills in: /json/2 names the method
# in the URL, and XML-RPC and /jsonrpc carry no such parameters at all,
# so on those transports every direct call looked like a business flow.
#
# Every transport ends in one of two places. call_kw serves the web
# client, XML-RPC and /jsonrpc on every series; Odoo 19's /json/2 looks
# the method up with get_public_method and calls it itself. Both are
# wrapped, once per process, to record (uid, model, method) of the
# OUTERMOST call on this thread, and the gates read that record.

def _eh_rpc_entry():
    """(uid, model, method) of the outermost RPC call on this thread."""
    return getattr(_eh_internal_state, "rpc_entry", None)


@contextlib.contextmanager
def _eh_rpc_scope(uid, model_name, method_name):
    outer = getattr(_eh_internal_state, "rpc_entry", None)
    if outer is None:
        _eh_internal_state.rpc_entry = (uid, model_name, method_name)
    try:
        yield
    finally:
        if outer is None:
            _eh_internal_state.rpc_entry = None


@contextlib.contextmanager
def _eh_rpc_rebase(uid, model_name, method_name):
    """Take over the RPC entry for the duration of a nested call.

    Importing runs through base_import.import.execute_import, so the
    outermost call is that wizard and everything under it looked like a
    business flow, however the user filled the file in. An import IS the
    person entering data, so load() re-bases the entry onto itself.
    """
    outer = getattr(_eh_internal_state, "rpc_entry", None)
    _eh_internal_state.rpc_entry = (uid, model_name, method_name)
    try:
        yield
    finally:
        _eh_internal_state.rpc_entry = outer


def _eh_wrap_call_kw(original):
    @functools.wraps(original)
    def call_kw(model, name, *args, **kwargs):
        env = getattr(model, "env", None)
        with _eh_rpc_scope(
            getattr(env, "uid", None), getattr(model, "_name", None), name,
        ):
            return original(model, name, *args, **kwargs)
    call_kw._eh_access_studio_original = original
    return call_kw


def _eh_wrap_get_public_method(original):
    @functools.wraps(original)
    def get_public_method(model, name):
        func = original(model, name)

        # functools.wraps keeps the attributes /json/2 inspects
        # (_api_model) and, through __wrapped__, the signature it binds.
        @functools.wraps(func)
        def entry(records, *args, **kwargs):
            with _eh_rpc_scope(records.env.uid, records._name, name):
                return func(records, *args, **kwargs)
        return entry
    get_public_method._eh_access_studio_original = original
    return get_public_method


# Markers the framework reads off a method to dispatch it: whether the
# first RPC argument is record ids (_api / _api_model), whether the call
# may run on the read replica (_readonly), and how the result is
# converted for the caller (_returns).
_EH_API_MARKERS = ("_api", "_api_model", "_readonly", "_returns", "_api_private")


def _eh_reset_derivation_caches():
    """Forget the per-registry field-graph answers.

    They are keyed on id(pool), and a registry rebuilt in place can be
    handed the same id, so they are cleared whenever the registry
    finishes loading rather than left to grow for the life of the
    process.
    """
    _EH_DERIVED_CACHE.clear()
    _EH_M2M_INVERSE_CACHE.clear()
    _EH_RELATED_TARGETS.clear()


def _eh_sync_api_markers(env):
    """Give every override the dispatch markers core gives the method it
    replaces.

    Odoo does not propagate decorators to an override: a method that
    replaces `web_search_read` without @api.model makes call_kw read the
    domain as record ids, and every list view fails. Remembering the
    right decorator on every override, on four series where they differ,
    is not a thing to do by hand, so the markers are copied from the
    method below ours in the resolution order, once per registry load.
    Anything written explicitly in the class wins.
    """
    done = set()
    for model_name in env.registry:
        try:
            cls = type(env[model_name])
        except Exception:
            continue
        mro = cls.__mro__
        for index, klass in enumerate(mro):
            if not klass.__module__.startswith("odoo.addons.eh_access_studio"):
                continue
            for name, func in list(vars(klass).items()):
                if name.startswith("_eh_") or not inspect.isfunction(func):
                    continue
                key = (klass.__module__, klass.__qualname__, name)
                if key in done:
                    continue
                done.add(key)
                core = None
                for other in mro[index + 1:]:
                    if name in vars(other) and not other.__module__.startswith(
                        "odoo.addons.eh_access_studio",
                    ):
                        core = vars(other)[name]
                        break
                if core is None:
                    continue
                core = getattr(core, "__func__", core)
                for marker in _EH_API_MARKERS:
                    if hasattr(core, marker) and not hasattr(func, marker):
                        setattr(func, marker, getattr(core, marker))


def _eh_install_rpc_entry_hooks():
    for module_name, attr, wrapper in (
        ("odoo.api", "call_kw", _eh_wrap_call_kw),                    # 16, 17, 18
        ("odoo.service.model", "call_kw", _eh_wrap_call_kw),          # 19
        ("odoo.addons.web.controllers.dataset", "call_kw", _eh_wrap_call_kw),
        ("odoo.addons.rpc.controllers.json2", "get_public_method",   # 19
         _eh_wrap_get_public_method),
    ):
        module = sys.modules.get(module_name)
        func = getattr(module, attr, None) if module is not None else None
        if not callable(func) or getattr(func, "_eh_access_studio_original", None):
            continue
        setattr(module, attr, wrapper(func))


_eh_install_rpc_entry_hooks()


class BaseAccessStudio(models.AbstractModel):
    _inherit = "base"

    # ---- failure posture ---------------------------------------------
    #
    # One rule, applied by every gate in this file: if the current user
    # is under an active profile, a failure inside a gate must BLOCK,
    # because silently returning "no restriction" is how a security tool
    # turns into a liability. If the user is named on no profile at all,
    # the gate steps aside so a bug here cannot take a database down for
    # people the product is not restricting. When the full lookup fails,
    # a one-query membership check settles which of the two applies.
    #
    # The one deliberate exception is the view layer (_get_view,
    # get_views): a failure there would leave the user staring at a
    # broken screen rather than at a denial, and everything it applies
    # is also enforced on the data path, so it degrades to "unstyled but
    # still gated".

    def _eh_access_studio_is_administrator(self):
        """Users who must always be able to reach and repair the setup."""
        try:
            for xmlid in ("base.group_system", "base.group_erp_manager"):
                group = self.env.ref(xmlid, raise_if_not_found=False)
                if not group:
                    continue
                for candidate in ("all_user_ids", "users", "user_ids"):
                    if candidate in group._fields:
                        if self.env.uid in group.sudo()[candidate].ids:
                            return True
                        break
        except Exception:
            _logger.exception(
                "Access Studio: could not determine administrator status",
            )
        return False

    def _eh_access_studio_fail_closed(self):
        """True when a gate failure must deny rather than allow."""
        try:
            if self.env.su or self.env.uid == 1:
                return False
            if self._name.startswith("eh.access."):
                return False
            if "eh.access.profile" not in self.env:
                return False
            profile_ids = self._eh_access_studio_active_profile_ids()
            if profile_ids is None:
                # The lookup itself failed. Ask the membership table
                # directly: a user named on no profile at all is not
                # restricted by this product and must not be locked out
                # by its failure. Only when even that cannot be answered
                # do we assume restriction, EXCEPT for an administrator:
                # failing closed must never leave the database with no
                # way back through the interface.
                listed = self.env["eh.access.profile"].sudo()._eh_listed_in_any_profile(
                    self.env.uid,
                )
                if listed is False:
                    return False
                return not self._eh_access_studio_is_administrator()
            return bool(profile_ids)
        except Exception:
            _logger.exception(
                "Access Studio: could not determine the failure posture"
                " for %s", self._name,
            )
            return False

    @property
    def _eh_company_id(self):
        """The company to resolve a profile against.

        env.company reads allowed_company_ids out of the context, which
        arrives from the RPC payload, and it RAISES when that list names
        a company the user may not enter. Every lookup in this module
        used to take that exception, which turned a forged context key
        into a way of switching the whole overlay off. The user's own
        company is the fallback, and it cannot be forged.
        """
        try:
            return self.env.company.id
        except Exception:
            company = self.env.user.sudo().company_id
            _logger.warning(
                "Access Studio: env.company is unusable for user %s,"
                " falling back to their own company. This is what a"
                " forged allowed_company_ids looks like.",
                self.env.uid,
            )
            return company.id if company else False

    def _eh_access_studio_active_profile_ids(self):
        if "eh.access.profile" not in self.env:
            return ()
        # Same reason as in models/ir_rule.py: during registry setup the
        # lookup can re-enter through the model being loaded.
        if not getattr(self.env.registry, "ready", True):
            return ()
        Profile = self.env["eh.access.profile"].sudo()
        try:
            return Profile._get_active_profile_ids(
                self.env.user.id,
                self._eh_company_id,
                Profile._today_for_cache_key(),
            )
        except Exception:
            # Defensive: a missing table during install or any other
            # transient issue must not break the view-render path.
            # Returning None rather than () lets the ENFORCEMENT paths
            # tell "this user has no profile" apart from "we could not
            # find out", which is the difference between failing open
            # correctly and failing open by accident.
            _logger.exception(
                "Access Studio: active-profile lookup failed for user %s",
                self.env.uid,
            )
            return None

    def _eh_access_studio_model_lines_strict(self):
        """Like _eh_access_studio_model_lines, but a lookup failure RAISES.

        The lenient version returns an empty recordset on error, which is
        right for rendering a view and wrong for a gate: it made the
        report gate's fail-closed branch unreachable, so a lookup error
        quietly turned a hidden report back on.
        """
        Profile = self.env["eh.access.profile"].sudo()
        line_ids = Profile._model_line_ids_for(
            self.env.user.id,
            Profile._eh_safe_company_id(),
            Profile._today_for_cache_key(),
            self._name,
        )
        return self.env["eh.access.model"].sudo().browse(line_ids)

    def _eh_access_studio_model_lines(self):
        """Return eh.access.model lines for self / current user.

        Uses the cached eh.access.profile._model_line_ids_for so a
        burst of view renders does one DB lookup per (user, company,
        day, model).
        """
        if "eh.access.profile" not in self.env:
            return self.env["eh.access.model"].sudo().browse()
        Profile = self.env["eh.access.profile"].sudo()
        try:
            line_ids = Profile._model_line_ids_for(
                self.env.user.id,
                self._eh_company_id,
                Profile._today_for_cache_key(),
                self._name,
            )
        except Exception:
            _logger.exception(
                "Access Studio: model-lines lookup failed for %s",
                self._name,
            )
            return self.env["eh.access.model"].sudo().browse()
        return self.env["eh.access.model"].sudo().browse(line_ids)

    def _eh_access_studio_global_profile(self):
        """Return the most permissive readonly / global-toggle profile in
        the active set, or browse() of nothing.

        Used to short-circuit when the user is in a read-only profile or
        has any of the global hide_* toggles enabled.
        """
        profile_ids = self._eh_access_studio_active_profile_ids()
        if not profile_ids:
            return self.env["eh.access.profile"].sudo().browse()
        return self.env["eh.access.profile"].sudo().browse(profile_ids)

    @api.model
    def _get_view_cache_key(self, view_id=None, view_type="form", **options):
        key = super()._get_view_cache_key(view_id, view_type, **options)
        profile_ids = self._eh_access_studio_active_profile_ids()
        if profile_ids is None:
            # The lookup FAILED. Falling back to the plain key would file
            # this arch under the one served to users with no profile, so
            # a restricted arch could be handed to everybody, or the
            # reverse. Key it to this user instead, which can never
            # collide with the profile-free key.
            return key + (("eh_access_studio", "unknown", self.env.uid),)
        if not profile_ids:
            return key
        return key + (("eh_access_studio", profile_ids, self._eh_company_id),)

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        try:
            self._eh_access_studio_apply_view_attrs(arch, view_type)
        except Exception:
            _logger.exception(
                "Access Studio: failed to apply view attrs for %s/%s",
                self._name, view_type,
            )
        return arch, view

    def _eh_access_studio_apply_view_attrs(self, arch, view_type):
        profiles = self._eh_access_studio_global_profile()
        if not profiles:
            return

        readonly_active = any(profiles.mapped("readonly"))
        model_lines = self._eh_access_studio_model_lines()

        if view_type == "form":
            self._eh_access_studio_strip_chatter(arch, profiles)

        self._eh_access_studio_strip_properties(arch, profiles, model_lines)
        self._eh_access_studio_strip_search_panel(arch, view_type, model_lines)

        if view_type not in _VIEWS_WITH_CRUD:
            return

        if readonly_active:
            arch.attrib["create"] = "false"
            arch.attrib["delete"] = "false"
            arch.attrib["edit"] = "false"

        if model_lines:
            for attr, flag in (
                ("create", "restrict_create"),
                ("delete", "restrict_delete"),
                ("edit", "restrict_edit"),
            ):
                if any(model_lines.mapped(flag)):
                    arch.attrib[attr] = "false"

            if view_type in _VIEWS_WITH_IMPORT_EXPORT:
                if any(model_lines.mapped("restrict_import")):
                    arch.attrib["import"] = "false"
                if any(model_lines.mapped("restrict_export")):
                    arch.attrib["export_xlsx"] = "false"

        if view_type in _VIEWS_WITH_IMPORT_EXPORT:
            if any(profiles.mapped("hide_import")):
                arch.attrib["import"] = "false"
            if any(profiles.mapped("hide_export")):
                arch.attrib["export_xlsx"] = "false"

        # Duplicate is controlled by the `duplicate` attribute on the
        # root form node. The JS form controller reads it via
        # exprToBoolean(rootNode.getAttribute("duplicate"), true).
        if view_type == "form" and model_lines:
            if any(model_lines.mapped("restrict_duplicate")):
                arch.attrib["duplicate"] = "false"

    def _eh_access_studio_strip_properties(self, arch, profiles, model_lines):
        """Remove custom-properties fields from the arch.

        The Add Property control belongs to the properties widget: there
        is no separate attribute that hides the button on its own, so
        the honest implementation of the toggle is to take the
        properties field out of the view for this profile.
        """
        hide = any(profiles.mapped("hide_add_property"))
        if not hide and model_lines:
            hide = any(model_lines.mapped("restrict_add_property"))
        if not hide:
            return
        for node in arch.xpath(
            "//field[@widget='properties' or @widget='properties_definition']"
        ):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    def _eh_access_studio_strip_search_panel(self, arch, view_type, model_lines):
        """Remove the search panel when a model line asks for it."""
        if view_type != "search" or not model_lines:
            return
        if not any(model_lines.mapped("hide_search_panel")):
            return
        for node in arch.xpath("//searchpanel"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    def _eh_access_studio_strip_chatter(self, arch, profiles):
        """Remove chatter widget when profile rules say so."""
        global_hide = any(profiles.mapped("hide_chatter"))
        per_model_hide = False
        if not global_hide:
            chatter_lines = self.env["eh.access.chatter"].sudo().search([
                ("profile_id", "in", profiles.ids),
                ("model_name", "=", self._name),
            ], limit=1)
            per_model_hide = bool(chatter_lines)
        if not (global_hide or per_model_hide):
            return
        # The chatter is declared as <chatter/> from Odoo 18 onwards and
        # as <div class="oe_chatter"> before that. Matching only the
        # first shape makes this a silent no-op on the older series, so
        # both are handled here and the same source backports cleanly.
        targets = arch.xpath(
            "//chatter"
            " | //div[contains(concat(' ', normalize-space(@class), ' '),"
            " ' oe_chatter ')]"
        )
        for chatter in targets:
            parent = chatter.getparent()
            if parent is not None:
                parent.remove(chatter)

    @api.model
    @_eh_readonly
    def get_views(self, views, options=None):
        result = super().get_views(views, options=options)
        try:
            self._eh_access_studio_prune_toolbars(result)
        except Exception:
            _logger.exception(
                "Access Studio: failed to prune toolbars for %s",
                self._name,
            )
        return result

    def _eh_access_studio_prune_toolbars(self, view_response):
        model_lines = self._eh_access_studio_model_lines()
        if not model_lines:
            return
        hidden_print = set(model_lines.mapped("hidden_report_action_ids.id"))
        hidden_action = set(model_lines.mapped("hidden_server_action_ids.id"))
        if not (hidden_print or hidden_action):
            return

        views_dict = view_response.get("views") or {}
        # View types are not pruned here. An action returned as a Python
        # dict never passes through _get_action_dict, so it still
        # advertises the mode, and removing that mode from the get_views
        # answer left the client asking for a view the server would not
        # return. A hidden view type is removed from the action itself
        # (models/ir_actions_act_window.py); an action a module builds in
        # Python keeps it, which the listing states as an interface
        # limit.
        for view_type in list(views_dict.keys()):
            view_data = views_dict.get(view_type) or {}
            toolbar = view_data.get("toolbar")
            if not toolbar:
                continue
            if hidden_print and toolbar.get("print"):
                toolbar["print"] = [
                    entry for entry in toolbar["print"]
                    if entry.get("id") not in hidden_print
                ]
            if hidden_action and toolbar.get("action"):
                toolbar["action"] = [
                    entry for entry in toolbar["action"]
                    if entry.get("id") not in hidden_action
                ]

    # ---- archive / duplicate enforcement at the ORM gate --------------
    #
    # The form-controller duplicate button reads the view's `duplicate`
    # attribute (set above) so users in a restrict_duplicate profile do
    # not see the menu entry. We still guard at copy() in case a custom
    # button or RPC call bypasses the front-end. Archive is the same
    # story: the cog menu item is gated client-side by the absence of
    # an `active` field write right, but we re-check in toggle_active
    # to defend against direct calls.

    def _eh_access_studio_check_op(self, attr, allow_empty=False):
        """Raise AccessError if any active model line has the named
        restrict flag set for self's model.

        attr is one of restrict_archive / restrict_duplicate. Skips
        SUPERUSER_ID and admins so recovery paths stay open."""
        if self.env.su or self.env.uid == 1:
            return
        if not self and not allow_empty:
            # Archiving or duplicating nothing is a no-op. A create is
            # different: the decision is about the model, and there is
            # no record yet to carry it.
            return
        if "eh.access.profile" not in self.env:
            return
        model_lines = self._eh_access_studio_model_lines()
        if not model_lines:
            return
        if any(model_lines.mapped(attr)):
            label = {
                "restrict_archive": "archive",
                "restrict_duplicate": "duplicate",
                "restrict_delete": "delete",
            }.get(attr, attr)
            raise AccessError(_(
                "Access Studio: %(action)s is disabled for this model"
                " by your access profile.",
                action=label,
            ))

    # ---- server-side gate for the model and read-only switches -------
    #
    # _check_access is the framework's own override point for adding a
    # restriction ("The method may be overridden in order to restrict
    # the access to self", odoo/orm/models.py). Routing through it means
    # create, write, unlink and read are all covered, RPC and server
    # code included, and the user sees Odoo's standard access error
    # rather than a custom dialog.
    #
    # Read is deliberately NOT gated here: narrowing what a user may see
    # is the job of Domain Access, which expresses it as a filter.

    _EH_OPERATION_FLAGS = {
        "create": "restrict_create",
        "write": "restrict_edit",
        "unlink": "restrict_delete",
    }

    if hasattr(models.BaseModel, "_access_domain"):
        @api.model
        def _access_domain(self, operation):
            """Odoo 20 native enforcement: intersect every native grant."""
            from odoo.fields import Domain
            native = super()._access_domain(operation)
            if self.env.su or self._name.startswith("eh.access.") or not getattr(self.env.registry, "ready", True):
                return native
            try:
                reason = self._eh_access_studio_operation_blocked(operation)
            except Exception:
                _logger.exception("Access Studio: native access operation failed for %s/%s", self._name, operation)
                if self._eh_access_studio_fail_closed():
                    return Domain.FALSE
                reason = None
            if reason:
                return Domain.FALSE
            extra = self.env["ir.access"]._compute_domain(self._name, operation)
            return native & Domain(extra)

    def _check_access(self, operation):
        parent = getattr(super(), "_check_access", None)
        if parent is None:
            # Odoo 16 and 17 ask through check_access_rights instead.
            return None
        result = parent(operation)
        if result is not None:
            return result
        try:
            blocked = self._eh_access_studio_operation_blocked(operation)
        except Exception:
            _logger.exception(
                "Access Studio: operation gate failed for %s/%s",
                self._name, operation,
            )
            if self._eh_access_studio_fail_closed():
                return self, functools.partial(
                    self._eh_access_studio_make_access_error, operation,
                    _("the access rules for this model could not be read"),
                )
            return None
        if not blocked:
            return None
        return self, functools.partial(
            self._eh_access_studio_make_access_error, operation, blocked,
        )

    # ---- one gate, two framework contracts ---------------------------
    #
    # Odoo 18 and 19 route every permission question through
    # _check_access. Odoo 16 and 17 have no such method and call
    # check_access_rights (model level) and check_access_rule (record
    # level) instead. Both entry points are implemented here so the same
    # source enforces on all four series, which keeps the backport a
    # matter of view tags and field names rather than a second
    # implementation of the gate.

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        parent = getattr(super(), "check_access_rights", None)
        if parent is None:
            # Odoo 20 removed the legacy API. Keep compatibility for
            # callers while using the native access domain gate.
            result = self.browse().has_access(operation)
            if not result and raise_exception:
                self.browse().check_access(operation)
        else:
            result = parent(operation, raise_exception=raise_exception)
        if _EH_HAS_CHECK_ACCESS:
            # Odoo 18 and 19 answer this through check_access, which
            # reaches _check_access above: judging it again here only
            # repeated the work.
            return result
        if not result:
            return result
        try:
            blocked = self._eh_access_studio_operation_blocked(operation)
        except Exception:
            _logger.exception(
                "Access Studio: operation gate failed for %s/%s",
                self._name, operation,
            )
            blocked = (
                _("the access rules for this model could not be read")
                if self._eh_access_studio_fail_closed() else None
            )
        if not blocked:
            return result
        if not raise_exception:
            return False
        raise self._eh_access_studio_make_access_error(operation, blocked)

    def _eh_access_studio_operation_blocked(self, operation):
        """Return a reason string when the profile blocks `operation`."""
        if operation not in self._EH_OPERATION_FLAGS:
            return None
        if (
            operation == "write"
            and self._eh_access_studio_write_is_internal()
            and self._name not in (getattr(_eh_internal_state, "changing", None) or ())
        ):
            # Posting to the chatter makes core CHECK write access on the
            # document. That check is waived; an actual write() running
            # underneath is not (see _eh_data_change). Creating and
            # deleting are never waived here.
            return None
        if self.env.su or self.env.uid == 1:
            return None
        if self._name.startswith("eh.access."):
            return None
        if "eh.access.profile" not in self.env:
            return None
        profile_ids = self._eh_access_studio_active_profile_ids()
        if profile_ids is None:
            # The lookup FAILED. That is not the same as "this user has
            # no profile", and reading it as such is how the gate failed
            # open on the most likely failure of all. A user named on no
            # profile at all is not ours to block, and administrators are
            # the exception, so a transient failure cannot write-lock the
            # whole database with no recovery short of a shell.
            listed = self.env["eh.access.profile"].sudo()._eh_listed_in_any_profile(
                self.env.uid,
            )
            if listed is False or self._eh_access_studio_is_administrator():
                return None
            return _("the access rules for this model could not be read")
        if not profile_ids:
            return None
        profiles = self.env["eh.access.profile"].sudo().browse(profile_ids)
        if any(profiles.mapped("readonly")) and self._eh_readonly_applies():
            return _("this profile is read only")
        model_lines = self._eh_access_studio_model_lines()
        if model_lines and any(
            model_lines.mapped(self._EH_OPERATION_FLAGS[operation])
        ):
            return _("your access profile restricts this model")
        return None

    def _eh_readonly_applies(self):
        """True when Read-Only Mode should cover this model."""
        if getattr(self, "_transient", False):
            return False
        if self._name in _EH_READONLY_EXEMPT_NAMES:
            return False
        return not self._name.startswith(_EH_READONLY_EXEMPT_PREFIXES)

    def _eh_access_studio_make_access_error(self, operation, reason):
        labels = {
            "create": _("create"),
            "write": _("edit"),
            "unlink": _("delete"),
        }
        return AccessError(_(
            "You are not allowed to %(operation)s records of"
            " %(model)s, because %(reason)s.",
            operation=labels.get(operation, operation),
            model=self._description or self._name,
            reason=reason,
        ))

    # ---- server-side half of the field rules -------------------------
    #
    # The arch overlay in ir_ui_view.py hides a field, greys it out or
    # marks it required in the rendered view. On its own that is a
    # client-side convention: a write over RPC, an import or a server
    # action ignores it entirely. These two overrides make the same
    # three rules hold on the data path as well.

    def _eh_access_studio_field_rules(self):
        if "eh.access.profile" not in self.env:
            return {}
        if self.env.su or self.env.uid == 1:
            return {}
        if self._name.startswith("eh.access."):
            return {}
        profile_ids = self._eh_access_studio_active_profile_ids()
        if profile_ids is None:
            # The lookup FAILED. Returning {} here read the failure as "no
            # profile", so every field lock and every hidden field quietly
            # switched off. Raise, and let the caller apply the posture.
            raise RuntimeError("Access Studio: the active profile lookup failed")
        if not profile_ids:
            return {}
        rules = self.env["ir.ui.view"].sudo()._eh_access_field_rules(
            self.env.user.id,
            self._eh_company_id,
            self.env["eh.access.profile"].sudo()._today_for_cache_key(),
            self._name,
        ) or {}
        if not self._inherits:
            return rules
        # Delegation (_inherits) makes the parent's fields readable and
        # writable on the child: res.users carries every res.partner
        # field. A rule on the parent field therefore also governs the
        # child's delegated copy, or hiding a contact's email left it in
        # full on every user. The cached answer is shared, so it is
        # copied before anything is added.
        merged = None
        for parent_model in self._inherits:
            if parent_model not in self.env:
                continue
            parent_rules = self.env[parent_model]._eh_access_studio_field_rules()
            for name, bucket in parent_rules.items():
                field = self._fields.get(name)
                if (
                    field is None or name in rules
                    or not getattr(field, "inherited", False)
                ):
                    continue
                if merged is None:
                    merged = dict(rules)
                merged[name] = bucket
        return merged if merged is not None else rules

    def _eh_access_studio_enforced_rules(self):
        """Field rules for an ENFORCEMENT path, with the failure posture.

        A lookup failure denies for a user who is named on a profile and
        steps aside for anyone else, the same rule every other gate in
        this file applies. Read paths used to return the unfiltered
        result and write paths the unchecked payload.
        """
        try:
            return self._eh_access_studio_field_rules()
        except Exception:
            _logger.exception(
                "Access Studio: field rule lookup failed for %s", self._name,
            )
            if self._eh_access_studio_fail_closed():
                raise AccessError(_(
                    "Your access profile could not be read, so %(model)s is"
                    " not available right now. Contact your administrator.",
                    model=self._description or self._name,
                ))
            return {}

    # A field lock is about VALUES, not about which RPC method was
    # called. An earlier version decided by reading request.params
    # ["method"], which is caller-controlled and therefore not a gate at
    # all, and which also missed first-party paths such as
    # web_save_multi. The rule now is simply:
    #
    #   create : locked fields are STRIPPED from the payload. A new
    #            record has no previous value to protect, and the client
    #            posts defaults, onchange results and computed values for
    #            hidden fields, so raising here made a model with one
    #            hidden field impossible to create.
    #   write  : a locked field raises only when the value would
    #            actually change. That holds for every caller, including
    #            server code running as the user. It is a lock.
    #
    # Our own multi-step operations (copy) run inside
    # _eh_access_studio_internal() so the framework's own payload is not
    # mistaken for an edit.

    def _eh_access_studio_is_internal(self):
        """True while the FIELD gate is deliberately stood down."""
        return bool(getattr(_eh_internal_state, "fields_off", False))

    def _eh_access_studio_write_is_internal(self):
        """True while a write is this module's own, not a user edit."""
        return bool(getattr(_eh_internal_state, "write_op_off", False))

    @staticmethod
    def _eh_access_studio_same_value(field, current, proposed):
        """True when writing `proposed` would not change `current`.

        The client posts every field in the form on save, including the
        ones this profile locked, so comparing values is what separates
        "the user edited a locked field" from "the payload carried the
        value it already had".
        """
        if field.type == "many2one":
            current_id = current.id if hasattr(current, "id") else current
            proposed_id = proposed.id if hasattr(proposed, "id") else proposed
            return (current_id or False) == (proposed_id or False)
        if field.type == "many2many":
            # Resolve (6, 0, ids) / (4, id) / plain id lists to the id set
            # they produce and compare it with what the record holds, so
            # re-posting the same tags is not an edit. Anything that
            # creates a new record (0, ...) or cannot be resolved is a
            # change.
            current_ids = set(getattr(current, "ids", None) or [])
            if not proposed:
                return not current_ids
            resolved = set()
            for command in proposed:
                if isinstance(command, int):
                    resolved.add(command)
                    continue
                if not isinstance(command, (list, tuple)) or not command:
                    return False
                code = command[0]
                if code == 6 and len(command) > 2:
                    resolved = set(command[2] or [])
                elif code == 4 and len(command) > 1:
                    resolved.add(command[1])
                elif code == 5:
                    resolved = set()
                else:
                    return False
            return resolved == current_ids
        if field.type == "one2many":
            # A one2many command list edits OTHER records, so any of them
            # is a change; an empty list is none.
            return not proposed
        if field.type in ("date", "datetime"):
            return str(current or "") == str(proposed or "")
        if isinstance(current, float) or isinstance(proposed, float):
            try:
                return float(current or 0.0) == float(proposed or 0.0)
            except (TypeError, ValueError):
                return current == proposed
        # '' and False mean the same absence for a Char, and the web
        # client posts '' where the database holds NULL, so treating
        # them as different raised on a save that changed nothing.
        left = current if current not in (None, "") else False
        right = proposed if proposed not in (None, "") else False
        return left == right

    @staticmethod
    def _eh_access_studio_is_empty(field, value):
        """Emptiness that respects the field type.

        0, 0.0 and False are legitimate values for a number or a
        checkbox, so only genuinely unset values count as missing.
        """
        if field.type in ("integer", "float", "monetary", "boolean"):
            return value is None
        if field.type in ("one2many", "many2many"):
            return not value
        return value in (False, None, "")

    def _eh_access_studio_condition_matches(self, condition, record, overlay=None):
        """Evaluate a rule condition against one record.

        The same expression the client evaluates for a view modifier,
        evaluated here against the record's own values so a conditional
        lock is enforced on the data path too and not only on screen.
        Anything that will not evaluate is treated as MATCHING, so a
        broken condition locks the field rather than quietly unlocking
        it.
        """
        if not condition:
            return True
        try:
            from odoo.tools.safe_eval import safe_eval
        except ImportError:  # pragma: no cover
            return True
        values = {}
        wanted = self._eh_access_studio_condition_names(condition)
        for name, field in record._fields.items():
            if field.type in ("one2many", "many2many"):
                continue
            if wanted is not None and name not in wanted:
                # Reading every field of the record, per record, to
                # evaluate a two-name condition was the largest cost a
                # conditional rule added.
                continue
            try:
                value = record[name]
            except Exception:
                continue
            if field.type == "many2one":
                value = value.id if value else False
            values[name] = value
        values["id"] = record.id
        for name, value in (overlay or {}).items():
            field = record._fields.get(name)
            if field is None or field.type in ("one2many", "many2many"):
                continue
            if field.type == "many2one" and hasattr(value, "id"):
                value = value.id
            values[name] = value
        try:
            return bool(safe_eval(condition, values))
        except Exception:
            _logger.warning(
                "Access Studio: could not evaluate the condition %r on"
                " %s, treating the rule as applying.",
                condition, record,
            )
            return True

    @staticmethod
    def _eh_access_studio_lock_condition(bucket):
        """When the lock on this field applies: None means always.

        A read-only rule with no condition locks the field on every
        record, even when a hidden rule on the same field has a
        condition. Reading "no readonly condition" as "take the hidden
        one" narrowed an unconditional lock to the records the hidden
        rule covers. A field only hidden locks where it is hidden; locked
        both ways, it locks where either applies.
        """
        conditions = bucket.get("conditions") or {}
        readonly = bool(bucket.get("readonly"))
        invisible = bool(bucket.get("invisible"))
        readonly_condition = conditions.get("readonly") if readonly else None
        invisible_condition = conditions.get("invisible") if invisible else None
        if readonly and not readonly_condition:
            return None
        if invisible and not invisible_condition:
            return None
        if readonly_condition and invisible_condition:
            return "(%s) or (%s)" % (readonly_condition, invisible_condition)
        return readonly_condition or invisible_condition

    def _eh_access_studio_create_condition_matches(self, bucket, vals):
        """Does this rule's condition hold for the record being created?

        There is no record yet, so the payload is the record: the
        condition is evaluated against the values supplied, with the
        model's defaults underneath for everything the payload leaves
        out. Anything unreadable counts as matching, so a broken
        condition locks rather than unlocks.
        """
        condition = self._eh_access_studio_lock_condition(bucket)
        if not condition:
            return True
        names = self._eh_access_studio_condition_names(condition)
        wanted = [
            name for name in (names or ())
            if name in self._fields and name not in (vals or {})
        ]
        defaults = self.default_get(wanted) if wanted else {}
        record = dict(defaults)
        record.update(vals or {})
        return self._eh_access_studio_condition_matches_values(condition, record)

    def _eh_access_studio_condition_matches_values(self, condition, values):
        try:
            from odoo.tools.safe_eval import safe_eval
        except ImportError:  # pragma: no cover
            return True
        namespace = {"id": False}
        for name, value in (values or {}).items():
            field = self._fields.get(name)
            if field is None or field.type in ("one2many", "many2many"):
                continue
            if field.type == "many2one" and hasattr(value, "id"):
                value = value.id
            namespace[name] = value
        for name in self._eh_access_studio_condition_names(condition) or ():
            namespace.setdefault(name, False)
        try:
            return bool(safe_eval(condition, namespace))
        except Exception:
            _logger.warning(
                "Access Studio: could not evaluate the condition %r on a"
                " new %s, treating the rule as applying.",
                condition, self._name,
            )
            return True

    @staticmethod
    def _eh_access_studio_condition_names(condition):
        """The names a condition mentions, or None when unreadable."""
        cached = _EH_CONDITION_NAMES.get(condition)
        if cached is not None:
            return cached if cached != () else None
        try:
            tree = ast.parse(condition, mode="eval")
        except Exception:
            _EH_CONDITION_NAMES[condition] = ()
            return None
        names = frozenset(
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        )
        _EH_CONDITION_NAMES[condition] = names
        return names

    def _eh_access_studio_locked_changes(self, rules, vals, operation):
        """Names of locked fields this payload would actually change."""
        locked = []
        defaults = None
        for name, bucket in rules.items():
            if not (bucket.get("readonly") or bucket.get("invisible")):
                continue
            if name not in (vals or {}) or name not in self._fields:
                continue
            field = self._fields[name]
            proposed = vals[name]
            if operation == "create":
                if defaults is None:
                    defaults = self.default_get([
                        n for n in rules if n in self._fields
                    ])
                if self._eh_access_studio_same_value(
                    field, defaults.get(name, False), proposed,
                ):
                    # The form posted the default it was given. Nobody
                    # edited anything, so creating must stay possible.
                    continue
                locked.append(name)
                continue
            conditions = bucket.get("conditions") or {}
            condition = self._eh_access_studio_lock_condition(bucket)
            blocked = False
            for record in self:
                if self._eh_access_studio_same_value(
                    field, record[name], proposed,
                ):
                    continue
                if not (
                    self._eh_access_studio_condition_matches(condition, record)
                    or self._eh_access_studio_condition_matches(
                        condition, record, overlay=vals,
                    )
                ):
                    # The lock applies to neither the record as it is nor
                    # the record as this write would leave it. Checking
                    # only the BEFORE state let a user unlock a field with
                    # two writes: change the condition field first, then
                    # the locked one.
                    continue
                blocked = True
                break
            if blocked:
                locked.append(name)
        return locked

    def _eh_access_studio_check_field_writes(self, vals, operation):
        """Refuse edits to fields the profile locked (write only)."""
        if not vals:
            return
        if self._eh_access_studio_is_internal():
            return
        try:
            rules = self._eh_access_studio_field_rules()
        except Exception:
            _logger.exception(
                "Access Studio: field rule lookup failed for %s", self._name,
            )
            if self._eh_access_studio_fail_closed():
                raise AccessError(_(
                    "Your access profile could not be read, so this change"
                    " to %(model)s was refused. Contact your"
                    " administrator.",
                    model=self._description or self._name,
                ))
            return
        if not rules:
            return
        locked = self._eh_access_studio_locked_changes(rules, vals, operation)
        if locked:
            labels = [
                self._fields[name].string or name for name in sorted(locked)
            ]
            raise AccessError(_(
                "Your access profile does not allow you to change"
                " %(fields)s on %(model)s.",
                fields=", ".join(labels),
                model=self._description or self._name,
            ))

    def _eh_access_studio_locked_field_names(self):
        rules = self._eh_access_studio_enforced_rules()
        return sorted(
            self._fields[name].string or name
            for name, bucket in rules.items()
            if name in self._fields
            and (bucket.get("readonly") or bucket.get("invisible"))
        )

    def _eh_access_studio_check_x2many_commands(self, vals):
        """Gate nested one2many / many2many commands as the REAL user.

        Core creates and writes x2many children in a later flush, and on
        some models (res.partner child_ids is the proven case) that flush
        runs as superuser. By then every gate here steps aside for su, so
        a child created through its parent skipped restrict_create and
        the field locks entirely. The commands are therefore checked up
        front, while the caller is still the user, and locked child
        fields are stripped from the command payloads in place.
        """
        if not vals or self.env.su or self._eh_access_studio_is_internal():
            return vals
        if self._eh_access_studio_active_profile_ids() == ():
            # Nobody restricts this user: no reads, no extra SQL.
            return vals
        cleaned = dict(vals)
        for name, commands in vals.items():
            field = self._fields.get(name)
            if field is None or field.type not in ("one2many", "many2many"):
                continue
            if not isinstance(commands, (list, tuple)):
                continue
            comodel = self.env[getattr(field, "comodel_name", None)]
            if not hasattr(comodel, "_eh_access_studio_operation_blocked"):
                continue
            rebuilt = []
            chatter_line = getattr(field, "comodel_name", None) in ("mail.message", "mail.activity")
            for command in commands:
                if not isinstance(command, (list, tuple)) or not command:
                    rebuilt.append(command)
                    continue
                if chatter_line:
                    self._eh_access_studio_check_chatter_command(
                        getattr(field, "comodel_name", None), command,
                    )
                code = command[0]
                if code == 0 and len(command) > 2 and isinstance(command[2], dict):
                    reason = comodel._eh_access_studio_operation_blocked("create")
                    if reason:
                        raise comodel._eh_access_studio_make_access_error(
                            "create", reason,
                        )
                    child_active = getattr(comodel, "_active_name", "active")
                    if child_active in command[2] and not command[2][child_active]:
                        # Creating a child already archived, through its
                        # parent, is still an archive.
                        comodel._eh_access_studio_check_op(
                            "restrict_archive", allow_empty=True,
                        )
                    sub_vals = comodel._eh_access_studio_strip_locked_on_create(
                        command[2],
                    )
                    rebuilt.append((0, command[1], sub_vals))
                    continue
                if code == 1 and len(command) > 2 and isinstance(command[2], dict):
                    child = comodel.browse(command[1])
                    reason = child._eh_access_studio_operation_blocked("write")
                    if reason:
                        raise child._eh_access_studio_make_access_error(
                            "write", reason,
                        )
                    child._eh_access_studio_check_field_writes(command[2], "write")
                    child_active = getattr(comodel, "_active_name", "active")
                    if child_active in command[2]:
                        # Archiving or unarchiving a child through its
                        # parent: core flushes it as superuser, so this is
                        # the only point the archive rule can see it.
                        child._eh_access_studio_check_op("restrict_archive")
                elif code == 2:
                    child = comodel.browse(command[1])
                    reason = child._eh_access_studio_operation_blocked("unlink")
                    if reason:
                        raise child._eh_access_studio_make_access_error(
                            "unlink", reason,
                        )
                elif code in (3, 5, 6) and field.type == "one2many" and self:
                    # Taking records OUT of a one2many clears their
                    # inverse field, and core does it without calling
                    # their write(), so the lock on that field has to be
                    # asked here. (6, 0, ids) drops every current child
                    # not listed.
                    current = self.sudo()[name]
                    if code == 3:
                        dropped = current.browse(
                            [command[1]] if len(command) > 1 else []
                        )
                    elif code == 5:
                        dropped = current
                    else:
                        kept = set(command[2] or []) if len(command) > 2 else set()
                        dropped = current.filtered(lambda rec: rec.id not in kept)
                    inverse = getattr(field, "inverse_name", None)
                    if dropped and inverse:
                        comodel.browse(dropped.ids)._eh_access_studio_check_field_writes(
                            {inverse: False}, "write",
                        )
                if field.type == "many2many" and code in (3, 4, 5, 6):
                    self._eh_access_studio_check_m2m_other_side(field, command)
                if code in (4, 6) and field.type == "one2many":
                    # Linking EXISTING records into a one2many moves them:
                    # core rewrites their inverse field to point at this
                    # record. That is an edit of those records, and of
                    # the inverse field in particular.
                    ids = (
                        [command[1]] if code == 4
                        else list(command[2] or []) if len(command) > 2 else []
                    )
                    children = comodel.browse([i for i in ids if isinstance(i, int)])
                    if children:
                        reason = children._eh_access_studio_operation_blocked("write")
                        if reason:
                            raise children._eh_access_studio_make_access_error(
                                "write", reason,
                            )
                        inverse = getattr(field, "inverse_name", None)
                        if inverse:
                            # -1 never equals a real id, so any lock on the
                            # inverse field reads this as the change it is.
                            children._eh_access_studio_check_field_writes(
                                {inverse: -1}, "write",
                            )
                rebuilt.append(command)
            cleaned[name] = rebuilt
        return cleaned

    def _eh_access_studio_check_tz_change(self, user_ids):
        """A dated profile starts and ends on the user's own calendar day,
        read from the timezone stored on the user. Letting the user edit
        that timezone let them move "today" by up to a day, and so step
        out of a profile early or delay its start. While a dated profile
        applies, only an administrator changes it."""
        if self.env.su or self.env.uid == 1 or not user_ids:
            return
        if "eh.access.profile" not in self.env:
            return
        bound = self.env["eh.access.profile"].sudo()._eh_time_bound_user_ids(
            tuple(user_ids),
        )
        if not bound or self._eh_access_studio_is_administrator():
            return
        raise AccessError(_(
            "The timezone decides when a dated access profile starts and"
            " ends, so it cannot be changed while one applies. Ask an"
            " administrator to change it."
        ))

    def _eh_access_studio_check_scheduled(self, vals_list):
        """"Send later" queues the caller's message and a job posts it,
        as superuser, when it is due, so the rule is applied when the
        message is scheduled or changed."""
        if self.env.su or self.env.uid == 1:
            return
        from .mail_thread import _eh_is_note, _eh_refuse_post
        for vals in vals_list:
            model_name = (vals or {}).get("model")
            if not model_name or not vals.get("res_id"):
                continue
            _eh_refuse_post(self.env, model_name, bool(vals.get("is_note")))

    def _eh_access_studio_check_chatter_command(self, comodel_name, command):
        """Overridden on mail.thread: chatter entries in a record's own
        payload. Models without a chatter have nothing to check."""
        return

    def _eh_access_studio_m2m_inverses(self, field):
        """Fields on the other model that show the same many2many table."""
        key = (id(self.pool), self._name, field.name)
        cached = _EH_M2M_INVERSE_CACHE.get(key)
        if cached is not None:
            return cached
        found = []
        relation = getattr(field, "relation", None)
        if relation and getattr(field, "comodel_name", None) in self.env:
            for other in self.env[getattr(field, "comodel_name", None)]._fields.values():
                if (
                    other.type == "many2many"
                    and getattr(other, "relation", None) == relation
                    and getattr(other, "comodel_name", None) == self._name
                ):
                    found.append(other.name)
        _EH_M2M_INVERSE_CACHE[key] = tuple(found)
        return tuple(found)

    def _eh_access_studio_check_m2m_other_side(self, field, command):
        """A many2many change is an edit of BOTH ends.

        Adding a tag from the tag's own form (category.partner_ids) or
        from the contact (partner.category_id) writes the same row, so a
        lock on either field, and an edit restriction on either model,
        has to hold whichever side the write comes from.
        """
        inverses = self._eh_access_studio_m2m_inverses(field)
        if not inverses:
            return
        code = command[0]
        current = set(self.sudo()[field.name].ids) if self else set()
        if code in (3, 4):
            touched = {command[1]} if len(command) > 1 else set()
        elif code == 5:
            touched = current
        else:
            wanted = set(command[2] or []) if len(command) > 2 else set()
            touched = wanted ^ current
        touched = {i for i in touched if isinstance(i, int)}
        if not touched:
            return
        others = self.env[getattr(field, "comodel_name", None)].browse(sorted(touched))
        reason = others._eh_access_studio_operation_blocked("write")
        if reason:
            raise others._eh_access_studio_make_access_error("write", reason)
        for inverse in inverses:
            # (6, 0, [-1]) never matches what a record holds, so any lock
            # on the inverse field reads this as the change it is.
            others._eh_access_studio_check_field_writes(
                {inverse: [(6, 0, [-1])]}, "write",
            )

    def _eh_access_studio_strip_locked_on_create(self, vals):
        """Neutralise locked fields in a create payload.

        Only two kinds of locked field are touched, because touching the
        rest is how this used to break things:

        * SUPPLIED - named in the payload. Its value is replaced by the
          model's own default, so the caller cannot choose it.
        * LAUNDERED - absent from the payload but carried by a
          default_<field> context key. default_get would apply it, so the
          model's own default is written in explicitly instead.

        Every other locked field is left alone and the model's normal
        default applies, exactly as if the user had never touched the
        form. An earlier version reset every locked field, which made a
        locked many2many with a default uncreatable and made copy() with
        any default wipe the source record's locked values.

        A one2many / many2many SUPPLIED with records of its own, beyond
        what the default gives, is refused rather than dropped, because
        dropping it would lose a subtree the caller thinks it created.
        """
        if not vals or self._eh_access_studio_is_internal():
            return vals
        rules = self._eh_access_studio_enforced_rules()
        if not rules:
            return vals
        locked = [
            name for name, bucket in rules.items()
            if (bucket.get("readonly") or bucket.get("invisible"))
            and name in self._fields
            # A conditional rule is judged on the record the payload
            # describes: stripping the field from every create made an
            # "Only when" rule behave as if it had no condition at all.
            and self._eh_access_studio_create_condition_matches(bucket, vals)
        ]
        if not locked:
            return vals

        context = self.env.context
        supplied = [name for name in locked if name in vals]
        laundered = [
            name for name in locked
            if name not in vals and ("default_%s" % name) in context
        ]
        touched = supplied + laundered
        if not touched:
            return vals

        # The model's defaults with every default_* context key removed,
        # so a context default can never become "the default".
        clean_env = self.with_context(**{
            key: False for key in context if key.startswith("default_")
        })
        defaults = clean_env.default_get(touched)

        cleaned = dict(vals)
        reset = []
        for name in touched:
            field = self._fields[name]
            default_value = defaults.get(name, False)
            proposed = vals[name] if name in vals else context.get("default_%s" % name)

            if field.type in ("one2many", "many2many") and name in supplied:
                if proposed and not self._eh_access_studio_same_x2many(
                    default_value, proposed,
                ):
                    raise AccessError(_(
                        "Your access profile does not allow you to set"
                        " %(field)s on %(model)s, and it cannot be dropped"
                        " silently because it carries records of its own.",
                        field=field.string or name,
                        model=self._description or self._name,
                    ))

            if field.required and not default_value:
                # The model insists on a value, this user may not supply
                # one, and there is no default to fall back on.
                raise AccessError(_(
                    "%(field)s is required on %(model)s but your access"
                    " profile does not allow you to set it. Ask an"
                    " administrator to remove the field rule or to"
                    " create this record for you.",
                    field=field.string or name,
                    model=self._description or self._name,
                ))

            if not self._eh_access_studio_same_value(
                field, default_value, proposed,
            ):
                reset.append(name)
            if name in defaults:
                cleaned[name] = default_value
            elif name in cleaned:
                cleaned.pop(name, None)
            else:
                # A laundered field with no model default: write its
                # empty value explicitly so default_get cannot fill it.
                cleaned[name] = [] if field.type in ("one2many", "many2many") else False
        if reset:
            # Silence here would be data loss, so the reset is recorded
            # where an administrator can find it.
            _logger.warning(
                "Access Studio: reset locked field(s) %s to their default"
                " on a create of %s by %s; the profile does not allow"
                " setting them.", ", ".join(sorted(reset)), self._name,
                self.env.user.login,
            )
        return cleaned

    @staticmethod
    def _eh_access_studio_same_x2many(default_value, proposed):
        """True when an x2many payload adds nothing beyond the default."""
        def ids_of(value):
            found = set()
            for command in value or []:
                if isinstance(command, (list, tuple)) and command:
                    if command[0] == 6 and len(command) > 2:
                        found.update(command[2] or [])
                    elif command[0] == 4 and len(command) > 1:
                        found.add(command[1])
                    elif command[0] == 0:
                        return None
                elif isinstance(command, int):
                    found.add(command)
            return found
        proposed_ids = ids_of(proposed)
        if proposed_ids is None:
            return False
        default_ids = ids_of(default_value) or set()
        return proposed_ids <= default_ids

    def _eh_access_studio_check_required(self, written_fields=None):
        """Refuse to leave a profile-required field empty.

        Runs on the saved records rather than on the payload, so ORM
        defaults, context defaults and computed values have all been
        applied before anything is judged missing.
        """
        if not self or self._eh_access_studio_is_internal():
            return
        rules = self._eh_access_studio_enforced_rules()
        required = [
            name for name, bucket in rules.items()
            if bucket.get("required") and name in self._fields
            # A field the same profile set also locks cannot be filled
            # by this user, so demanding it would make the model
            # permanently uncreatable. The health check reports the
            # contradiction; the runtime refuses to act on it.
            and not (bucket.get("readonly") or bucket.get("invisible"))
        ]
        if written_fields is not None:
            # On a write, only judge the fields this write touched.
            # Judging every required field would make any unrelated edit
            # to a record that predates the rule impossible to save.
            required = [name for name in required if name in written_fields]
        if not required:
            return
        conditions = {
            name: (rules[name].get("conditions") or {}).get("required")
            for name in required
        }
        missing = set()
        for record in self:
            for name in required:
                if not self._eh_access_studio_is_empty(
                    self._fields[name], record[name],
                ):
                    continue
                # A rule scoped with "Only when" demands the field on the
                # records it covers and on no others.
                condition = conditions.get(name)
                if condition and not self._eh_access_studio_condition_matches(
                    condition, record,
                ):
                    continue
                missing.add(name)
        if missing:
            labels = [
                self._fields[name].string or name for name in sorted(missing)
            ]
            raise ValidationError(_(
                "Your access profile requires %(fields)s to be set on"
                " %(model)s.",
                fields=", ".join(labels),
                model=self._description or self._name,
            ))

    @api.model_create_multi
    def create(self, vals_list):
        blank = self.browse()
        if self._name == "mail.scheduled.message":
            blank._eh_access_studio_check_scheduled(vals_list)
        active_name = getattr(self, "_active_name", "active")
        context_default = self.env.context.get("default_%s" % active_name)
        if active_name in self._fields and (
            any(
                active_name in (vals or {}) and not vals.get(active_name)
                for vals in vals_list
            )
            # A default_active=False context key creates the record
            # archived just as surely as putting it in the payload.
            or (context_default is not None and not context_default)
        ):
            # Creating a record already archived is an archive operation
            # with extra steps.
            blank._eh_access_studio_check_op(
                "restrict_archive", allow_empty=True,
            )
        vals_list = [
            blank._eh_access_studio_check_x2many_commands(
                blank._eh_access_studio_strip_locked_on_create(vals),
            )
            for vals in vals_list
        ]
        # Worked out BEFORE the insert. Once Postgres raises a constraint
        # violation the transaction is aborted, and any further query,
        # including the rule lookup, fails with "current transaction is
        # aborted" instead of producing an explanation.
        # The MESSAGE too: translating it looks up the user's language,
        # which is a query, and on Odoo 18 and earlier that query fails
        # on the aborted transaction and buries the explanation under a
        # cache error.
        locked_names = blank._eh_access_studio_locked_field_names()
        _locked_rules = blank._eh_access_studio_enforced_rules()
        locked_technical = [
            name for name, bucket in _locked_rules.items()
            if bucket.get("readonly") or bucket.get("invisible")
        ]
        # Translated now, formatted later: the violation names the one
        # column that failed, and only that field should be blamed.
        template = _(
            "This %(model)s cannot be saved because your access profile"
            " locks %(fields)s, and the record cannot be created without"
            " them. Ask an administrator to adjust the profile or to"
            " create it for you."
        ) if locked_names else None
        model_label = self._description or self._name
        try:
            with _eh_data_change(self._name):
                records = super().create(vals_list)
        except (psycopg2.errors.CheckViolation, psycopg2.errors.NotNullViolation) as err:
            # A field can be mandatory through a SQL CHECK or a NOT NULL
            # column without saying required=True in Python (res.partner
            # name is one). When the profile locks such a field, the
            # database is what refuses, and it refuses with a raw
            # violation. Name the locked fields so the user knows why.
            if not template:
                raise
            diag = getattr(err, "diag", None)
            culprit = " ".join(filter(None, (
                getattr(diag, "column_name", None),
                getattr(diag, "constraint_name", None),
            ))) if diag else ""
            column = getattr(diag, "column_name", None) if diag else None
            blamed = (
                [name for name in locked_technical if name == column]
                if column else
                [name for name in locked_technical if name in culprit]
            )
            if culprit and not blamed:
                # The violation is about a column the profile does not
                # touch. Blaming the profile would hide the real cause.
                raise
            labels = [
                self._fields[name].string or name
                for name in blamed if name in self._fields
            ] or locked_names
            raise AccessError(template % {
                "model": model_label, "fields": ", ".join(labels),
            }) from err
        records._eh_access_studio_check_required()
        return records

    def write(self, vals):
        vals = self._eh_access_studio_drop_served_blanks(vals)
        if self._name == "mail.scheduled.message" and vals:
            self._eh_access_studio_check_scheduled([
                dict({
                    "model": message.model, "res_id": message.res_id,
                    "is_note": message.is_note,
                }, **vals)
                for message in self.sudo()
            ])
        self._eh_access_studio_check_field_writes(vals, "write")
        vals = self._eh_access_studio_check_x2many_commands(vals)
        active_name = getattr(self, "_active_name", "active")
        if (
            getattr(_eh_internal_state, "unlinking", 0)
            and active_name in (vals or {}) and not vals.get(active_name)
        ):
            # Core converts unlink() into write({'active': False}) on
            # some models. Only THAT conversion is a delete; an ordinary
            # archive is governed by restrict_archive alone, so a delete
            # restriction no longer blocks the Archive button.
            self._eh_access_studio_check_op("restrict_delete")
        if active_name in (vals or {}):
            # Archiving and unarchiving through a plain write are the
            # same operations as the menu entries, so they answer to the
            # same rule in both directions. _active_name is read rather
            # than hardcoded, because a model may name the flag
            # something else. A form that saves the flag unchanged is not
            # archiving anything, so only a real change is judged.
            wanted = bool(vals[active_name])
            if any(bool(flag) != wanted for flag in self.sudo().mapped(active_name)):
                self._eh_access_studio_check_op("restrict_archive")
        snapshot = self._eh_access_studio_conditional_snapshot(vals)
        with _eh_data_change(self._name):
            result = super().write(vals)
        if snapshot:
            self._eh_access_studio_recheck_conditional(snapshot)
        self._eh_access_studio_check_required(written_fields=set(vals or ()))
        return result

    def _eh_access_studio_drop_served_blanks(self, vals):
        """Never write back a blank this module served.

        A hidden field is blanked on read. The client keeps what it read
        and sends it again on save, so a field that is hidden but still
        WRITABLE (a computed field with an inverse, a related field that
        writes through) would have the blank written over the real value,
        and core runs some of those inverses as superuser, where no gate
        stands. Writing the blank is therefore treated as what it is:
        not an edit. It is dropped from the payload, and the value on the
        record stays as it was.
        """
        if not vals or self.env.su or self._eh_access_studio_is_internal():
            return vals
        always, conditional = self._eh_access_studio_hidden_split()
        if not always and not conditional:
            return vals
        cleaned = None
        restore = {}
        for name in list(vals):
            if name not in self._fields:
                continue
            if name not in always and name not in conditional:
                continue
            field = self._fields[name]
            blank = self._eh_access_studio_blank_value(field)
            value = vals[name]
            if not (value == blank or (not value and not blank)):
                continue
            covered = self
            if name in conditional:
                # Judged per record: on the records the condition does
                # NOT cover the value was served truthfully, so a blank
                # there is the person clearing the field, and clearing it
                # must still work.
                condition = conditional[name]
                covered = self.filtered(
                    lambda record: self._eh_access_studio_condition_matches(
                        condition, record,
                    )
                )
                if not covered:
                    continue
                uncovered = self - covered
                if uncovered:
                    restore.setdefault(name, uncovered)
            if cleaned is None:
                cleaned = dict(vals)
            cleaned.pop(name, None)
        if restore:
            self._eh_access_studio_restore_after_write(restore, vals)
        return vals if cleaned is None else cleaned

    def _eh_access_studio_restore_after_write(self, restore, vals):
        """Apply a dropped clear to the records it was really meant for."""
        def apply_later():
            for name, records in restore.items():
                try:
                    with _eh_access_studio_internal():
                        records.write({name: vals[name]})
                except Exception:
                    _logger.exception(
                        "Access Studio: could not clear %s on %s",
                        name, records,
                    )
        self.env.cr.precommit.add(apply_later)

    # ---- conditional locks, judged on the record the write produced ---
    #
    # The check before the write evaluates the condition on the record as
    # it is and on the payload laid over it. That cannot see a condition
    # field the write changes INDIRECTLY: res.partner's company_type is
    # written through an inverse that sets is_company, core writes
    # is_company as superuser before the rest, and any module can do the
    # same with its own fields. So a conditionally locked field this write
    # names is also judged AFTERWARDS, on the record the ORM actually
    # produced. If it changed and the condition now holds, the write is
    # refused and the transaction rolls back.

    def _eh_access_studio_conditional_snapshot(self, vals):
        if not vals or not self or self.env.su or self._eh_access_studio_is_internal():
            return None
        rules = self._eh_access_studio_enforced_rules()
        watched = []
        for name, bucket in rules.items():
            if name not in vals or name not in self._fields:
                continue
            if not (bucket.get("readonly") or bucket.get("invisible")):
                continue
            conditions = bucket.get("conditions") or {}
            condition = self._eh_access_studio_lock_condition(bucket)
            if condition:
                watched.append((name, condition))
        if not watched:
            return None
        before = {
            record.id: {name: record.sudo()[name] for name, _cond in watched}
            for record in self
        }
        return watched, before

    @staticmethod
    def _eh_access_studio_values_equal(field, before, after):
        if field.type in ("many2one", "one2many", "many2many"):
            return set(getattr(before, "ids", None) or []) == set(
                getattr(after, "ids", None) or []
            )
        return (before or False) == (after or False)

    def _eh_access_studio_recheck_conditional(self, snapshot):
        watched, before = snapshot
        changed = []
        for record in self:
            prior = before.get(record.id) or {}
            for name, condition in watched:
                field = self._fields[name]
                if self._eh_access_studio_values_equal(
                    field, prior.get(name), record.sudo()[name],
                ):
                    continue
                if self._eh_access_studio_condition_matches(condition, record):
                    changed.append(name)
        if changed:
            labels = sorted({self._fields[name].string or name for name in changed})
            raise AccessError(_(
                "Your access profile does not allow you to change"
                " %(fields)s on %(model)s.",
                fields=", ".join(labels),
                model=self._description or self._name,
            ))

    # ---- read side of a hidden field ---------------------------------
    #
    # Hiding a field in the arch keeps it off the screen, but read,
    # web_read and search_read all return it to whoever asks. Blanking
    # the value in the read RESULT closes that for every client and API
    # caller, while leaving `record.field` working for server code, which
    # is what computes, constraints and other modules depend on. So this
    # is a client-facing gate, not a data-layer secret: a hidden field is
    # not encryption, and the listing says so.

    def _eh_access_studio_hidden_split(self):
        """(always hidden, {field: condition}) for the current user.

        A hidden rule with a condition hides the field on the records
        where the condition holds, and only there. Fields derived from a
        conditionally hidden field follow the same condition.
        """
        if self.env.su or self._eh_access_studio_is_internal():
            return frozenset(), {}
        rules = self._eh_access_studio_enforced_rules()
        always = set()
        conditional = {}
        for name, bucket in rules.items():
            if not bucket.get("invisible") or name not in self._fields:
                continue
            condition = (bucket.get("conditions") or {}).get("invisible")
            if condition:
                conditional[name] = condition
            else:
                always.add(name)
        # A name is shown through display_name almost everywhere, so
        # hiding the name field and leaving the display name readable
        # hid nothing. Derivation never pulls these in (see
        # _EH_NEVER_DERIVED, which stops an unrelated hidden field from
        # blanking every record's name), but an administrator who names
        # the record's own name field means exactly this.
        identity = {
            name for name in (
                getattr(self, "_rec_name", None), "name", "complete_name",
                "parent_id",
            ) if name and name in self._fields
        }
        for label in _EH_DISPLAY_FIELDS:
            if label not in self._fields:
                continue
            covered = identity & set(always)
            if covered:
                always.add(label)
                continue
            conditioned = identity & set(conditional)
            if conditioned:
                conditional.setdefault(
                    label, conditional[sorted(conditioned)[0]],
                )
        effective = self._eh_access_studio_effective_hidden(always)
        derived = {}
        for name, condition in conditional.items():
            for member in self._eh_access_studio_closure({name}):
                if member in effective:
                    continue
                if member in derived and derived[member] != condition:
                    derived[member] = "(%s) or (%s)" % (derived[member], condition)
                else:
                    derived[member] = condition
        return effective, derived

    def _eh_access_studio_blank_labels(self, result):
        """Blank the LABEL of a many2one whose target hides its name.

        A read of another model answers (id, "the name"), and that name
        is built by the other model under sudo, where no gate stands. So
        a contact whose name is hidden was still spelled out in every
        record that points at it.
        """
        if self.env.su or self._eh_access_studio_is_internal():
            return
        blanked = []
        for name, field in self._fields.items():
            if field.type != "many2one" or not getattr(field, "comodel_name", None):
                continue
            if getattr(field, "comodel_name", None) not in self.env:
                continue
            comodel = self.env[getattr(field, "comodel_name", None)]
            if "display_name" in comodel._eh_access_studio_hidden_names():
                blanked.append(name)
        if not blanked:
            return
        for row in result:
            if not isinstance(row, dict):
                continue
            for name in blanked:
                value = row.get(name)
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    row[name] = (value[0], "")

    @staticmethod
    def _eh_access_studio_blank_value(field):
        if field.type in ("one2many", "many2many"):
            return []
        if field.type in ("integer", "float", "monetary"):
            return 0
        return False

    def _eh_access_studio_blank_hidden(self, result):
        if not result or self._eh_access_studio_is_internal():
            return result
        always, conditional = self._eh_access_studio_hidden_split()
        if not always and not conditional:
            return result
        labels = self._eh_access_studio_blank_labels(result)
        verdicts = {}
        prefetched = None
        if conditional:
            ids = [
                row["id"] for row in result
                if isinstance(row, dict) and isinstance(row.get("id"), int)
            ]
            # One recordset, so the condition's fields are read for all
            # the rows at once instead of one SELECT per row.
            prefetched = {record.id: record for record in self.browse(ids)}
        for row in result:
            if not isinstance(row, dict):
                continue
            for name in always:
                if name in row:
                    row[name] = self._eh_access_studio_blank_value(self._fields[name])
            if not conditional:
                continue
            record = (prefetched or {}).get(row.get("id"))
            for name, condition in conditional.items():
                if name not in row:
                    continue
                if record is None:
                    applies = True
                else:
                    key = (record.id, condition)
                    if key not in verdicts:
                        verdicts[key] = self._eh_access_studio_condition_matches(
                            condition, record,
                        )
                    applies = verdicts[key]
                if applies:
                    row[name] = self._eh_access_studio_blank_value(self._fields[name])
        return result

    def _eh_access_studio_effective_hidden(self, hidden):
        """The hidden set, plus every field that carries a hidden value.

        Blanking one field by name was not enough: email_normalized,
        email_formatted and any related field showed the same value
        under another name. A field joins the set when it is RELATED
        through a hidden field, or COMPUTED from one, repeated until
        nothing new is added. A related field that mirrors a field
        hidden on ANOTHER model joins it too: that is how delegation
        (_inherits) shows a contact's fields on a user, and how
        related fields show them on any record. The model's name and
        display fields are never pulled in this way, see
        _EH_NEVER_DERIVED.
        """
        result = set(hidden or ()) | self._eh_access_studio_foreign_hidden()
        if not result:
            return frozenset()
        return self._eh_access_studio_closure(result)

    def _eh_access_studio_closure(self, hidden):
        """Same-model derivation, memoised on the field graph."""
        key = (id(self.pool), self._name, frozenset(hidden))
        cached = _EH_DERIVED_CACHE.get(key)
        if cached is not None:
            return cached
        protected = set(_EH_NEVER_DERIVED)
        if getattr(self, "_rec_name", None):
            protected.add(self._rec_name)
        result = set(hidden)
        # A related field mirrors the hidden one, so it follows all the
        # way down.
        changed = True
        while changed:
            changed = False
            for name, field in self._fields.items():
                if name in result or name in protected:
                    continue
                related = getattr(field, "related", None)
                if not related:
                    continue
                first = related.split(".")[0] if isinstance(related, str) else related[0]
                if first in result:
                    result.add(name)
                    changed = True
        # A computed field is followed ONE hop from a field the
        # administrator named, and only when the user cannot write it.
        # Odoo declares one @api.depends per compute METHOD and a method
        # usually sets several fields, so following it to a fixed point
        # hid a quarter of a large model, and following it into a
        # writable field let the blank be saved back.
        # A computed field is followed only when EVERY input it declares
        # is hidden, so it can carry nothing else. Following a field
        # because it shares one dependency was far too broad: Odoo
        # declares @api.depends per compute METHOD, and a plain field
        # like a document's state appears in the dependencies of many
        # unrelated methods, so hiding the state blanked every amount on
        # the document as well.
        seed = set(hidden)
        identity = {
            name for name in (getattr(self, "_rec_name", None), "name")
            if name and name in self._fields
        } - seed
        for name, field in self._fields.items():
            if name in result or name in protected:
                continue
            if not field.compute or not field.readonly or field.inverse:
                continue
            try:
                depends = self.pool.field_depends[field]
            except Exception:
                depends = ()
            # The record's own name is not information about a hidden
            # field: a formatted address is name plus email, and it does
            # carry the email. Everything else the field is computed
            # from must be hidden for it to carry nothing but the hidden
            # value.
            inputs = {
                dep.split(".")[0] for dep in (depends or ()) if dep
            } - identity
            if inputs and inputs <= seed:
                result.add(name)
        frozen = frozenset(result)
        _EH_DERIVED_CACHE[key] = frozen
        return frozen

    def _eh_access_studio_related_targets(self):
        """(field, target model, target field) for every field of this
        model that carries a value READ FROM another model.

        Only RELATED fields count: they mirror one field of one other
        record, so hiding the source and hiding the mirror mean the same
        thing. Dependency paths were followed here too for one version,
        and that was wrong twice over. Odoo declares one @api.depends per
        compute METHOD and a method usually sets several fields, so
        hiding a contact's email also blanked an employee's work phone;
        and a computed field with an inverse is WRITABLE, so the blank
        was saved back and destroyed the value it was meant to protect.
        A stored copy on another model is that model's data and follows
        that model's own rules, which is what the listing says.
        """
        key = (id(self.pool), self._name)
        cached = _EH_RELATED_TARGETS.get(key)
        if cached is not None:
            return cached
        targets = []
        for name, field in self._fields.items():
            if name in _EH_NEVER_DERIVED or name == getattr(self, "_rec_name", None):
                continue
            paths = []
            related = getattr(field, "related", None)
            if related:
                paths.append(related.split(".") if isinstance(related, str) else list(related))
            for path in paths:
                if len(path) < 2:
                    continue
                model = self
                try:
                    for segment in path[:-1]:
                        link = model._fields[segment]
                        if not getattr(link, "comodel_name", None):
                            raise KeyError(segment)
                        model = self.env[getattr(link, "comodel_name", None)]
                except KeyError:
                    continue
                entry = (name, model._name, path[-1])
                if entry not in targets:
                    targets.append(entry)
        result = tuple(targets)
        _EH_RELATED_TARGETS[key] = result
        return result

    def _eh_access_studio_own_hidden(self):
        """Hidden on this model by its own rules (and delegation), plus
        same-model derivation. No cross-model mirrors, so it can be asked
        of any other model without recursing. A field hidden only on
        some records counts: a mirror on another model cannot tell which
        of its records point at a hidden one without reading it."""
        rules = self._eh_access_studio_enforced_rules()
        named = {
            name for name, bucket in rules.items()
            if bucket.get("invisible") and name in self._fields
        }
        return self._eh_access_studio_closure(named) if named else frozenset()

    def _eh_access_studio_foreign_hidden(self):
        if self.env.su or self._eh_access_studio_is_internal():
            return set()
        if self._eh_access_studio_active_profile_ids() == ():
            # Nobody restricts this user: nothing to mirror, no lookups.
            return set()
        found = set()
        own = {}
        for name, model_name, target in self._eh_access_studio_related_targets():
            if name in found:
                continue
            if model_name not in own:
                own[model_name] = (
                    self.env[model_name]._eh_access_studio_own_hidden()
                    if model_name in self.env else frozenset()
                )
            if target in own[model_name]:
                found.add(name)
        return found

    def _eh_access_studio_hidden_names(self):
        """Every field hidden on at least some records, for the user.

        Search and sort cannot be judged per record (a condition is a
        Python expression, not a domain), so a field hidden on SOME
        records is scrubbed from searches and sorts on ALL of them:
        otherwise search_count would reveal on the hidden records what
        read withholds. Reading stays per record, see
        _eh_access_studio_blank_hidden.
        """
        always, conditional = self._eh_access_studio_hidden_split()
        if not conditional:
            return always
        return frozenset(always) | frozenset(conditional)

    @staticmethod
    def _eh_access_studio_field_of(expression):
        """The field name inside a domain or order expression.

        'salary:sum', 'hire_date:month' and 'salary desc' all name a
        field. Reading only the part before a dot let an aggregate
        spelling walk past the scrub, and HAVING on an aggregate over a
        hidden field answers which records hold which value.
        """
        if not isinstance(expression, str):
            return ""
        return expression.strip().split(" ")[0].split(":")[0]

    def _eh_access_studio_path_hidden(self, path):
        """Does this field path read a hidden field on any model it
        walks through? res.users email, and partner_id.email from a sale
        order, both end on the contact's hidden email."""
        model = self
        path = self._eh_access_studio_field_of(path) if isinstance(path, str) else path
        segments = path.split(".") if isinstance(path, str) else list(path)
        for index, segment in enumerate(segments):
            if segment in model._eh_access_studio_hidden_names():
                return True
            field = model._fields.get(segment)
            if field is None or not getattr(field, "comodel_name", None) or index == len(segments) - 1:
                return False
            model = model.env[getattr(field, "comodel_name", None)]
        return False

    @staticmethod
    def _eh_access_studio_scrubbed_constant(operator, value):
        """What a scrubbed condition becomes. A constant, so it carries
        no information about the hidden value. "Is not set" becomes
        TRUE, so the blank group a hidden grouping collapses into opens
        on the records it counts instead of on none."""
        if operator == "=" and value in (False, None):
            return True
        if operator == "in" and list(value or []) in ([False], [None]):
            return True
        # A negated condition ("not this value") is true of most records,
        # so answering nothing turned an ordinary filter into an empty
        # list. Either answer is a constant and tells the caller nothing.
        return operator in _EH_NEGATIVE_OPERATORS

    def _eh_access_studio_scrub_domain(self, domain):
        """Neutralise search conditions on hidden fields.

        Searching can reveal a value as surely as reading it: a series of
        email ilike '...' searches reconstructs an address one character
        at a time. Every condition that reads a hidden field, directly,
        through a path, or inside a sub-domain on a related model, is
        replaced by a constant, which carries no information either way.
        An OR with other fields (the way name_search looks at name, email
        and reference together) keeps working on the visible fields.
        """
        if not domain or self.env.su or self._eh_access_studio_is_internal():
            return domain
        if self._eh_access_studio_active_profile_ids() == ():
            return domain
        try:
            from odoo.fields import Domain as _Domain
        except ImportError:
            _Domain = None
        if _Domain is not None and isinstance(domain, _Domain):
            return domain.map_conditions(self._eh_access_studio_scrub_condition)
        if not isinstance(domain, (list, tuple)):
            return domain
        scrubbed = []
        for leaf in domain:
            if (
                isinstance(leaf, (list, tuple)) and len(leaf) == 3
                and isinstance(leaf[0], str)
                and self._eh_access_studio_path_hidden(leaf[0])
            ):
                scrubbed.append(
                    (1, "=", 1)
                    if self._eh_access_studio_scrubbed_constant(leaf[1], leaf[2])
                    else ("id", "=", False)
                )
            else:
                scrubbed.append(leaf)
        return scrubbed

    def _eh_access_studio_scrub_condition(self, cond):
        from odoo.fields import Domain as _Domain
        field_expr = str(cond.field_expr)
        if self._eh_access_studio_path_hidden(field_expr):
            return (
                _Domain.TRUE
                if self._eh_access_studio_scrubbed_constant(cond.operator, cond.value)
                else _Domain.FALSE
            )
        if isinstance(cond.value, _Domain) and "." not in field_expr:
            # ('partner_id', 'any', <domain on res.partner>): delegation
            # and auto_join links are compiled inline, never through the
            # related model's own _search, so its conditions are scrubbed
            # here with that model's hidden set.
            field = self._fields.get(field_expr)
            if field is not None and getattr(field, "comodel_name", None) and getattr(field, "comodel_name", None) in self.env:
                inner = self.env[getattr(field, "comodel_name", None)]._eh_access_studio_scrub_domain(cond.value)
                if inner is not cond.value:
                    return _Domain(field_expr, cond.operator, inner)
        return cond

    def _eh_access_studio_scrub_order(self, order):
        """Drop sort terms on hidden fields: sorting by a value reveals
        its order even when the value itself is blanked."""
        hidden = self._eh_access_studio_hidden_names()
        if not hidden or not order or not isinstance(order, str):
            return order
        kept = [
            term for term in order.split(",")
            if not self._eh_access_studio_path_hidden(term)
        ]
        return ",".join(kept) or None

    def _eh_access_studio_expand_domain(self, domain):
        """Resolve searches on computed fields before scrubbing.

        A condition on display_name is not a condition on a hidden field,
        so scrubbing the domain as written let it through; Odoo then
        expands it into _rec_names_search (email, ref, vat on a contact)
        while compiling the query, after the scrub. On Odoo 19 the same
        full optimisation core runs is applied first, so the scrub sees
        the conditions that actually reach SQL, model-specific search
        overrides included. Earlier series expand through
        _search_display_name, which is scrubbed below.
        """
        if not domain or not self._eh_access_studio_hidden_names():
            return domain
        active_name = getattr(self, "_active_name", None)
        if active_name and active_name in str(domain):
            # The optimiser folds a condition that matches everything,
            # including the ('active', 'in', [True, False]) that asks for
            # archived records. Core then adds active = True because it
            # no longer sees one, and the archived records disappear.
            return domain
        try:
            from odoo.fields import Domain as _Domain
        except ImportError:
            return domain
        return _Domain(domain).optimize_full(self)

    @api.model
    def _search_display_name(self, operator, value):
        domain = super()._search_display_name(operator, value)
        if domain is NotImplemented:
            return domain
        return self._eh_access_studio_scrub_domain(domain)

    def _eh_access_studio_search_is_a_read(self):
        """Is this search the caller asking for data?

        Scrubbing exists to stop a person probing a hidden value with
        search conditions. Business code that searches by that value as
        the person (res.partner.find_or_create matching an email while a
        message is posted) is not probing, and scrubbing it made that
        code create duplicate contacts. So conditions are scrubbed while
        the outermost call is a search-shaped read, whatever model it
        started on (a sale order search can reach the contact through
        partner_id.email), and when no RPC call is in flight at all (the
        conservative reading, as for Duplicate).
        """
        entry = _eh_rpc_entry()
        if entry is None:
            return True
        method = entry[2] or ""
        return (
            method in _EH_READ_METHODS
            or "search" in method
            or "read_group" in method
            or "grouping" in method
        )

    def _search(self, domain, *args, **kwargs):
        # Signature agnostic: the domain is the first argument on every
        # series, and the rest of the parameters moved between 16 and 19.
        # order is the fourth positional parameter everywhere.
        if not self._eh_access_studio_search_is_a_read():
            return super()._search(domain, *args, **kwargs)
        domain = self._eh_access_studio_expand_domain(domain)
        domain = self._eh_access_studio_scrub_domain(domain)
        if "order" in kwargs:
            kwargs["order"] = self._eh_access_studio_scrub_order(kwargs["order"])
        elif len(args) >= 3:
            args = list(args)
            args[2] = self._eh_access_studio_scrub_order(args[2])
            args = tuple(args)
        return super()._search(domain, *args, **kwargs)

    def _eh_access_studio_blank_group_rows(self, rows):
        """Blank hidden fields in an aggregate result.

        One click on Group By reaches read_group, which never formats
        through _read_format, so a hidden field came back in full as a
        group label or an aggregate. Keys there can be plain names or
        aggregate spellings such as 'amount:sum', so both shapes are
        matched.
        """
        if not rows or self._eh_access_studio_is_internal():
            return rows
        rules = self._eh_access_studio_enforced_rules()
        hidden = self._eh_access_studio_effective_hidden({
            name for name, bucket in rules.items()
            if bucket.get("invisible") and name in self._fields
        })
        if not hidden:
            return rows
        for row in rows:
            if not isinstance(row, dict):
                continue
            blanked = False
            for key in list(row):
                if key.startswith("__"):
                    continue
                base_name = key.split(":")[0].split(".")[0]
                if base_name in hidden:
                    row[key] = False
                    blanked = True
            if blanked:
                # The drill-down domain of a group carries the value it
                # groups on, so leaving it would hand the hidden value
                # straight back the moment the group is opened.
                for key in ("__domain", "__extra_domain"):
                    row.pop(key, None)
        return rows

    @api.model
    @_eh_readonly
    def read_group(self, domain, fields, groupby, offset=0, limit=None,
                   orderby=False, lazy=True):
        if not _EH_MODERN_READ_GROUP:
            # Odoo 16 groups here and nowhere else: _read_group has the
            # legacy shape, so the reshaping override stands aside and
            # this is the only place the caller's domain, sort and
            # grouping terms can be judged.
            hidden = self._eh_access_studio_group_hidden()
            if hidden:
                domain = self._eh_access_studio_scrub_domain(domain)
                if isinstance(orderby, str):
                    orderby = self._eh_access_studio_scrub_order(orderby) or False
                groupby_list = [groupby] if isinstance(groupby, str) else list(groupby or ())
                visible, positions = self._eh_access_studio_collapse(groupby_list, hidden)
                if positions:
                    rows = super().read_group(
                        domain, fields, visible, offset=offset, limit=limit,
                        orderby=orderby, lazy=lazy,
                    )
                    for row in rows:
                        if isinstance(row, dict):
                            for index in positions:
                                row[groupby_list[index]] = False
                    return self._eh_access_studio_blank_group_rows(rows)
        rows = super().read_group(
            domain, fields, groupby, offset=offset, limit=limit,
            orderby=orderby, lazy=lazy,
        )
        return self._eh_access_studio_blank_group_rows(rows)

    if "aggregates" in inspect.signature(models.BaseModel.read_group).parameters:
        @api.model
        def read_group(self, domain, groupby=(), aggregates=(), having=(),
                       offset=0, limit=None, order=None):
            """Odoo 20 exposes tuple grouping directly at the public API.

            Native read_group delegates through our secured _read_group,
            retaining domain scrubbing, group collapse and aggregate blanks.
            Keep its real signature and its core RPC decorator contract.
            """
            return super().read_group(
                domain, groupby=groupby, aggregates=aggregates, having=having,
                offset=offset, limit=limit, order=order,
            )

    def _eh_access_studio_group_hidden(self):
        """Hidden names for grouping, or an empty set when none apply.

        Same guard as _search: business code that groups on the person's
        behalf gets real numbers, while a grouping the person asked for
        is collapsed. Merging groups under server code would hand a
        compute or a report wrong totals.
        """
        if self.env.su or self._eh_access_studio_is_internal():
            return frozenset()
        if not self._eh_access_studio_search_is_a_read():
            return frozenset()
        rules = self._eh_access_studio_enforced_rules()
        return self._eh_access_studio_effective_hidden({
            name for name, bucket in rules.items()
            if bucket.get("invisible") and name in self._fields
        })

    @staticmethod
    def _eh_access_studio_spec_hidden(spec, hidden):
        return isinstance(spec, str) and spec.split(":")[0].split(".")[0] in hidden

    def _eh_access_studio_collapse(self, groupby, hidden):
        """(visible groupby, positions of the hidden terms)."""
        positions = [
            index for index, spec in enumerate(groupby or ())
            if self._eh_access_studio_spec_hidden(spec, hidden)
        ]
        visible = [
            spec for index, spec in enumerate(groupby or ())
            if index not in positions
        ]
        return visible, positions

    def _eh_access_studio_expand_rows(self, rows, positions, width, aggregates, hidden):
        """Put a blank back where each collapsed term was, and blank
        aggregates computed over a hidden field."""
        blank_aggregates = [
            index for index, spec in enumerate(aggregates or ())
            if self._eh_access_studio_spec_hidden(spec, hidden)
        ]
        expanded = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                expanded.append(row)
                continue
            values = list(row[:width])
            rest = list(row[width:])
            for index in positions:
                values.insert(index, False)
            for index in blank_aggregates:
                if index < len(rest):
                    rest[index] = False
            expanded.append(tuple(values + rest))
        return expanded

    def _read_group(self, *args, **kwargs):
        """The low level grouping call, which returns TUPLES.

        Blanking the label of a group on a hidden field was not enough:
        the groups themselves still told the user how many distinct
        values existed and how many records shared each one. A hidden
        grouping term is therefore taken OUT of the grouping before the
        query runs, and a blank is put back in its place, so the answer
        has the shape the caller asked for and one group where the
        hidden field was. Aggregates over a hidden field are blanked,
        and HAVING conditions and sort terms on one are neutralised.

        Only the modern signature (Odoo 17 and later: groupby,
        aggregates) is reshaped. On Odoo 16 the read_group override
        blanks the labels.
        """
        if not _EH_MODERN_READ_GROUP:
            return super()._read_group(*args, **kwargs)
        hidden = self._eh_access_studio_group_hidden()
        if not hidden:
            return super()._read_group(*args, **kwargs)
        args = list(args)
        names = ("domain", "groupby", "aggregates", "having", "offset", "limit", "order")
        call = dict(zip(names, args))
        call.update(kwargs)
        groupby = list(call.get("groupby") or ())
        aggregates = list(call.get("aggregates") or ())
        visible, positions = self._eh_access_studio_collapse(groupby, hidden)
        call["groupby"] = visible
        if call.get("having"):
            call["having"] = self._eh_access_studio_scrub_domain(call["having"])
        if isinstance(call.get("order"), str):
            call["order"] = self._eh_access_studio_scrub_order(call["order"])
        rows = super()._read_group(**call)
        return self._eh_access_studio_expand_rows(
            rows, positions, len(visible), aggregates, hidden,
        )

    def _read_grouping_sets(self, domain, grouping_sets, aggregates=(), order=None, **kwargs):
        """Odoo 19's pivot and grouped views read through GROUPING SETS,
        a path that does not pass through _read_group. Same treatment."""
        hidden = self._eh_access_studio_group_hidden()
        if not hidden:
            return super()._read_grouping_sets(
                domain, grouping_sets, aggregates=aggregates, order=order, **kwargs,
            )
        collapsed = [
            self._eh_access_studio_collapse(list(groupby), hidden)
            for groupby in grouping_sets
        ]
        if isinstance(order, str):
            order = self._eh_access_studio_scrub_order(order)
        results = super()._read_grouping_sets(
            domain, [visible for visible, _positions in collapsed],
            aggregates=aggregates, order=order, **kwargs,
        )
        return [
            self._eh_access_studio_expand_rows(
                rows, positions, len(visible), aggregates, hidden,
            )
            for rows, (visible, positions) in zip(results, collapsed)
        ]

    # Context keys a caller sets to make display_name include MORE of the
    # record (res.partner builds "Name <email>" under show_email). They
    # arrive from the RPC payload, so with a hidden field in play they
    # would put that field straight back into the display string.
    _EH_DISPLAY_CONTEXT_PREFIXES = ("show_",)
    _EH_DISPLAY_CONTEXT_KEYS = frozenset((
        "partner_show_db_id", "address_inline", "formatted_display_name",
    ))

    def _eh_access_studio_noisy_context(self, context):
        # Only keys that are SET. Checking mere presence found the keys
        # this method had already set to False, so export_data, which
        # re-enters itself on the cleaned recordset, recursed without end.
        return [
            key for key in (context or {})
            if (key.startswith(self._EH_DISPLAY_CONTEXT_PREFIXES)
                or key in self._EH_DISPLAY_CONTEXT_KEYS)
            and context.get(key)
        ]

    def _eh_access_studio_display_is_sensitive(self, fnames=None):
        """Would an enriched display name reveal a hidden field? On this
        model, or on a record one of the requested fields points at: a
        sale order's partner_id is displayed with the CONTACT's rules."""
        if self._eh_access_studio_hidden_names():
            return True
        for name in fnames or ():
            field = self._fields.get(name)
            if field is not None and getattr(field, "comodel_name", None) and getattr(field, "comodel_name", None) in self.env:
                if self.env[getattr(field, "comodel_name", None)]._eh_access_studio_hidden_names():
                    return True
        return False

    def _eh_access_studio_without_display_context(self, fnames=None):
        """This recordset with display-enriching context keys removed,
        when a hidden field could show through them; otherwise self."""
        noisy = self._eh_access_studio_noisy_context(self.env.context)
        if not noisy or not self._eh_access_studio_display_is_sensitive(fnames):
            return self
        return self.with_context(**{key: False for key in noisy})

    def name_search(self, *args, **kwargs):
        """The autocomplete reads the same values a read does.

        It is answered from display_name, which core builds under sudo
        and which a display-enriching context key can grow to include a
        hidden field, so the same stripping applies here. When the
        printed name itself is hidden, the labels come back empty rather
        than spelling out what read() withholds.
        """
        records = self._eh_access_studio_without_display_context(["display_name"])
        result = super(BaseAccessStudio, records).name_search(*args, **kwargs)
        if not result or "display_name" not in self._eh_access_studio_hidden_names():
            return result
        return [
            (row[0], "") if isinstance(row, (list, tuple)) and row else row
            for row in result
        ]

    def web_name_search(self, *args, **kwargs):
        parent = getattr(super(), "web_name_search", None)
        if parent is None:  # arrived with Odoo 17
            raise AttributeError("web_name_search is not available")
        records = self._eh_access_studio_without_display_context(["display_name"])
        result = getattr(
            super(BaseAccessStudio, records), "web_name_search",
        )(*args, **kwargs)
        hidden = self._eh_access_studio_hidden_names()
        if not result or "display_name" not in hidden:
            return result
        for row in result:
            if isinstance(row, dict):
                for key in ("display_name", "__formatted_display_name"):
                    if key in row:
                        row[key] = ""
        return result

    def _eh_access_studio_clean_spec(self, specification):
        """Strip display-enriching keys from the sub-contexts of a web_read
        specification. Core resolves a related record's display name
        under sudo, where no gate runs, so a caller putting show_email on
        the partner_id sub-spec got 'Name <hidden email>' back."""
        if (
            not isinstance(specification, dict) or self.env.su
            or self._eh_access_studio_is_internal()
        ):
            return specification
        cleaned = {}
        for name, sub in specification.items():
            field = self._fields.get(name)
            if isinstance(sub, dict) and field is not None and getattr(field, "comodel_name", None) \
                    and getattr(field, "comodel_name", None) in self.env:
                comodel = self.env[getattr(field, "comodel_name", None)]
                sub = dict(sub)
                context = sub.get("context")
                if isinstance(context, dict):
                    noisy = self._eh_access_studio_noisy_context(context)
                    if noisy and comodel._eh_access_studio_display_is_sensitive(
                        list((sub.get("fields") or {}).keys()),
                    ):
                        sub["context"] = {
                            key: value for key, value in context.items()
                            if key not in noisy
                        }
                if isinstance(sub.get("fields"), dict):
                    sub["fields"] = comodel._eh_access_studio_clean_spec(sub["fields"])
            cleaned[name] = sub
        return cleaned

    def _eh_access_studio_clean_call(self, args, kwargs, position, keyword):
        """Clean the specification argument wherever the caller put it."""
        if keyword in kwargs:
            kwargs = dict(kwargs, **{keyword: self._eh_access_studio_clean_spec(kwargs[keyword])})
        elif len(args) > position:
            args = list(args)
            args[position] = self._eh_access_studio_clean_spec(args[position])
            args = tuple(args)
        return args, kwargs

    def onchange(self, *args, **kwargs):
        """An onchange answer can tell the caller about a hidden value.

        Core recomputes fields under sudo, so sending a CANDIDATE value
        for a hidden field (a VAT number) and reading which keys come
        back says whether another record holds it, without ever reading
        the field. A hidden field is one this user may not set anyway,
        so it is dropped from the values and from the changed-field list
        before core runs, and hidden values are blanked in the answer.
        """
        hidden = self._eh_access_studio_hidden_names()
        if hidden:
            args = list(args)
            kwargs = dict(kwargs)

            def strip_values(values):
                return {
                    name: value for name, value in values.items()
                    if name not in hidden
                }

            def strip_names(names):
                if isinstance(names, (list, tuple)):
                    return [name for name in names if name not in hidden]
                if isinstance(names, str) and names in hidden:
                    return ""
                return names

            # The caller chooses positional or keyword form; both arrive
            # here, and stripping only one of them left the other open.
            if args and isinstance(args[0], dict):
                args[0] = strip_values(args[0])
            for key in ("values",):
                if isinstance(kwargs.get(key), dict):
                    kwargs[key] = strip_values(kwargs[key])
            if len(args) > 1:
                args[1] = strip_names(args[1])
            for key in ("field_names", "field_name", "field_onchange"):
                if key in kwargs:
                    kwargs[key] = strip_names(kwargs[key])
            args = tuple(args)
        result = super().onchange(*args, **kwargs)
        if hidden and isinstance(result, dict) and isinstance(result.get("value"), dict):
            # REMOVED, not blanked: a key in an onchange answer marks that
            # field dirty in the client, so a blank would be saved back
            # over the value. Leaving the key out says "nothing changed".
            for name in list(result["value"]):
                if name in hidden:
                    result["value"].pop(name, None)
        return result

    @_eh_readonly
    def web_read(self, *args, **kwargs):
        args, kwargs = self._eh_access_studio_clean_call(args, kwargs, 0, "specification")
        records = self._eh_access_studio_without_display_context()
        parent = getattr(super(BaseAccessStudio, records), "web_read", None)
        if parent is None:  # Odoo 16 reads through read() instead
            raise AttributeError("web_read is not available on this series")
        return parent(*args, **kwargs)

    @api.model
    @_eh_readonly
    def web_search_read(self, *args, **kwargs):
        args, kwargs = self._eh_access_studio_clean_call(args, kwargs, 1, "specification")
        return super().web_search_read(*args, **kwargs)

    def web_save(self, *args, **kwargs):
        args, kwargs = self._eh_access_studio_clean_call(args, kwargs, 1, "specification")
        parent = getattr(super(), "web_save", None)
        if parent is None:  # Odoo 16 saves through create / write
            raise AttributeError("web_save is not available on this series")
        return parent(*args, **kwargs)

    def _read_format(self, fnames, load="_classic_read"):
        """The single funnel every read-shaped call formats through.

        read() was not enough: search_read and web_search_read reach
        _read_format directly (odoo/orm/models.py: search_read calls
        records._read_format), so a hidden field came back in full to
        anyone who asked for a list instead of a record.
        """
        records = self._eh_access_studio_without_display_context(fnames)
        if records is not self:
            # display_name is computed per context; drop the cached value
            # so it is rebuilt without the caller's enrichment keys.
            return records._eh_access_studio_blank_hidden(
                super(BaseAccessStudio, records)._read_format(fnames, load=load)
            )
        result = super()._read_format(fnames, load=load)
        return self._eh_access_studio_blank_hidden(result)

    # ---- import and export, at the data path -------------------------
    #
    # Removing the cog entries is not a restriction: /web/export/xlsx
    # and /web/export/csv call records.export_data (addons/web/
    # controllers/export.py) and the import wizard calls load(), and
    # neither route reads the view. Both are gated here so the toggles
    # mean what their labels say.

    def _eh_access_studio_check_transfer(self, global_flag, model_flag, label):
        if self.env.su or self.env.uid == 1:
            return
        if self._name.startswith("eh.access."):
            return
        if "eh.access.profile" not in self.env:
            return
        profile_ids = self._eh_access_studio_active_profile_ids()
        if not profile_ids:
            return
        profiles = self.env["eh.access.profile"].sudo().browse(profile_ids)
        blocked = any(profiles.mapped(global_flag))
        if not blocked:
            model_lines = self._eh_access_studio_model_lines()
            blocked = bool(model_lines) and any(model_lines.mapped(model_flag))
        if blocked:
            raise AccessError(_(
                "Your access profile does not allow you to %(action)s"
                " %(model)s records.",
                action=label,
                model=self._description or self._name,
            ))

    def export_data(self, fields_to_export):
        cleaned = self._eh_access_studio_without_display_context()
        if cleaned is not self:
            return cleaned.export_data(fields_to_export)
        self._eh_access_studio_check_transfer(
            "hide_export", "restrict_export", _("export"),
        )
        rules = self._eh_access_studio_enforced_rules()
        conditional_columns = {}
        if rules:
            hidden = set()
            for path in (fields_to_export or []):
                # A path such as parent_id/email walks into another
                # record. Checking only the first segment let the second
                # one carry a hidden field straight out of the database.
                segments = [seg for seg in path.split("/") if seg]
                model = self
                for index, segment in enumerate(segments):
                    if index == 0:
                        bucket = rules.get(segment) or {}
                        scope = model
                    else:
                        bucket = self._eh_access_studio_related_rules(
                            segments[:index],
                        ).get(segment) or {}
                        scope = self._eh_access_studio_walk(segments[:index])
                    derived = scope._eh_access_studio_effective_hidden({
                        name for name, b in (
                            rules if index == 0 else
                            self._eh_access_studio_related_rules(segments[:index])
                        ).items()
                        if b.get("invisible") and name in scope._fields
                    }) if scope is not None else frozenset()
                    conditions = bucket.get("conditions") or {}
                    if index == 0 and bucket.get("invisible") and conditions.get("invisible"):
                        # A rule scoped with "Only when" covers some
                        # records: the column is exported and the cells
                        # it covers are blanked below, instead of the
                        # whole column being refused for everybody.
                        conditional_columns[segment] = conditions["invisible"]
                        continue
                    if bucket.get("invisible") or segment in derived:
                        hidden.add(segment)
            if hidden:
                labels = [
                    self._fields[name].string or name for name in hidden
                    if name in self._fields
                ]
                raise AccessError(_(
                    "Your access profile hides %(fields)s on %(model)s,"
                    " so they cannot be exported.",
                    fields=", ".join(labels or hidden),
                    model=self._description or self._name,
                ))
        result = super().export_data(fields_to_export)
        if conditional_columns:
            self._eh_access_studio_blank_export(
                result, fields_to_export, conditional_columns,
            )
        return result

    def _eh_access_studio_blank_export(self, result, fields_to_export, columns):
        """Blank the cells a conditional rule covers, record by record."""
        rows = (result or {}).get("datas")
        if not rows:
            return
        positions = {}
        for position, path in enumerate(fields_to_export or []):
            segments = [segment for segment in path.split("/") if segment]
            if len(segments) == 1 and segments[0] in columns:
                positions[position] = columns[segments[0]]
        if not positions:
            return
        for record, row in zip(self, rows):
            if not isinstance(row, list):
                continue
            for position, condition in positions.items():
                if position >= len(row):
                    continue
                if self._eh_access_studio_condition_matches(condition, record):
                    row[position] = ""

    def _eh_access_studio_walk(self, segments):
        """The model an export or import path ends on, or None."""
        model = self
        for segment in segments:
            field = model._fields.get(segment)
            if field is None or not getattr(field, "comodel_name", None):
                return None
            model = self.env[getattr(field, "comodel_name", None)]
        return model

    def _eh_access_studio_related_rules(self, segments):
        """Field rules of the model an export path has walked into."""
        model = self
        for segment in segments:
            field = model._fields.get(segment)
            if field is None or not getattr(field, "comodel_name", None):
                return {}
            model = self.env[getattr(field, "comodel_name", None)]
        return model._eh_access_studio_enforced_rules()

    @api.model
    def load(self, fields, data):
        with _eh_rpc_rebase(self.env.uid, self._name, "load"):
            return self._eh_access_studio_load(fields, data)

    def _eh_access_studio_load(self, fields, data):
        self._eh_access_studio_check_transfer(
            "hide_import", "restrict_import", _("import"),
        )
        # An import that quietly writes blanks into locked columns is
        # the worst version of this rule: the importer reports success
        # and the data is gone. Name the columns and refuse instead.
        rules = self._eh_access_studio_enforced_rules()
        if rules:
            locked = set()
            for path in (fields or []):
                segments = [seg for seg in path.split("/") if seg]
                for index, segment in enumerate(segments):
                    if index == 0:
                        bucket = rules.get(segment) or {}
                    else:
                        bucket = self._eh_access_studio_related_rules(
                            segments[:index],
                        ).get(segment) or {}
                    if bucket.get("readonly") or bucket.get("invisible"):
                        locked.add(segment)
            locked = sorted(locked)
            if locked:
                labels = [
                    self._fields[name].string or name for name in locked
                    if name in self._fields
                ]
                raise AccessError(_(
                    "Your access profile locks %(fields)s on %(model)s,"
                    " so a file containing those columns cannot be"
                    " imported. Remove the columns or ask an"
                    " administrator to adjust the profile.",
                    fields=", ".join(labels or locked),
                    model=self._description or self._name,
                ))
        return super().load(fields, data)

    def unlink(self):
        depth = getattr(_eh_internal_state, "unlinking", 0)
        _eh_internal_state.unlinking = depth + 1
        try:
            return super().unlink()
        finally:
            _eh_internal_state.unlinking = depth

    def toggle_active(self):
        self._eh_access_studio_check_op("restrict_archive")
        return super().toggle_active()

    # The Odoo 19 web client does NOT call toggle_active from the cog
    # menu: it calls action_archive / action_unarchive (see
    # addons/web/static/src/model/relational_model/record.js and
    # dynamic_list.js). Guarding toggle_active alone left the UI path
    # completely open, so both entry points are gated here.

    def action_archive(self):
        self._eh_access_studio_check_op("restrict_archive")
        return super().action_archive()

    def action_unarchive(self):
        self._eh_access_studio_check_op("restrict_archive")
        return super().action_unarchive()

    def _eh_access_studio_is_user_duplicate(self):
        """True when this copy is the user's own Duplicate.

        Hide Duplicate is about the Duplicate command, not about every
        copy the framework makes. Credit notes and reversals, backorders
        and many other flows copy records internally from a business
        method, and blocking those broke core accounting and inventory
        for the profile. A Duplicate is a direct `copy` (or `copy_data`)
        call on the model, over any transport, so that entry point is
        gated, and only the outermost copy, never the copies it makes of
        its lines. With no RPC call at all (a shell, a test, a server
        action) it is gated too, which is the conservative reading.

        "The user's Duplicate" is the outermost RPC call on this thread,
        whatever transport carried it (see _eh_rpc_entry). Record
        creation itself is governed by restrict_create, which is enforced
        unconditionally.
        """
        if getattr(_eh_internal_state, "copy_depth", 0):
            return False
        entry = _eh_rpc_entry()
        if entry is None:
            # No RPC call on this thread: a shell, a test, a server
            # action. Gated, which is the conservative reading.
            return True
        _uid, model_name, method = entry
        return model_name == self._name and method in ("copy", "copy_data")

    @contextlib.contextmanager
    def _eh_access_studio_copy_scope(self):
        depth = getattr(_eh_internal_state, "copy_depth", 0)
        _eh_internal_state.copy_depth = depth + 1
        try:
            yield
        finally:
            _eh_internal_state.copy_depth = depth

    def copy_data(self, default=None):
        # copy() is not the only duplication path: copy_data() plus a
        # create() is the documented way to duplicate with changes, and
        # it never reaches copy().
        if self._eh_access_studio_is_user_duplicate():
            self._eh_access_studio_check_op("restrict_duplicate")
        with self._eh_access_studio_copy_scope():
            result = super().copy_data(default=default)
        if self._eh_access_studio_copy_data_is_read():
            # Called straight over RPC, copy_data is a READ: it hands the
            # caller every copyable value of the record, hidden fields
            # included. Business code that copies a record internally
            # (reversals, backorders) never takes this branch, so its
            # duplicates keep their values.
            result = self._eh_access_studio_blank_copy_rows(result)
        return result

    def _eh_access_studio_blank_copy_rows(self, rows):
        """Blank hidden fields in copy_data rows, lines included.

        copy_data nests the copyable lines as (0, 0, values) commands, and
        those values belong to the line model, whose own hidden fields
        would otherwise ride along inside the parent's answer.
        """
        rows = self._eh_access_studio_blank_hidden(rows)
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            for name, value in row.items():
                field = self._fields.get(name)
                if (
                    field is None
                    or field.type not in ("one2many", "many2many")
                    or not isinstance(value, (list, tuple))
                ):
                    continue
                nested = [
                    command[2] for command in value
                    if isinstance(command, (list, tuple)) and len(command) > 2
                    and command[0] == 0 and isinstance(command[2], dict)
                ]
                if nested and getattr(field, "comodel_name", None) in self.env:
                    self.env[getattr(field, "comodel_name", None)]._eh_access_studio_blank_copy_rows(nested)
        return rows

    def _eh_access_studio_copy_data_is_read(self):
        """True when this copy_data call IS the caller's RPC request."""
        if getattr(_eh_internal_state, "copy_depth", 0):
            return False
        if self.env.su or self._eh_access_studio_is_internal():
            return False
        entry = _eh_rpc_entry()
        if not entry:
            return False
        _uid, model_name, method = entry
        return method == "copy_data" and model_name == self._name

    def copy(self, default=None):
        if self._eh_access_studio_is_user_duplicate():
            self._eh_access_studio_check_op("restrict_duplicate")
        # copy() is reachable over RPC, and `default` is caller supplied.
        # The internal marker below switches the field gate off so the
        # SOURCE record's locked values can be carried over, so the
        # caller's own overrides have to be neutralised first, at EVERY
        # level: top-level locked keys are stripped, and nested one2many
        # / many2many commands are checked as the real user. Checking
        # only the top level let copy({'child_ids': [(1, id, {...})]})
        # write a locked field on another, existing record.
        if default:
            default = self._eh_access_studio_strip_locked_on_create(default)
            default = self._eh_access_studio_check_x2many_commands(default)
        # Duplication is one decision, taken above. The create it runs
        # underneath carries the source record's values, locked fields
        # included, and that copy is not the user editing them.
        with self._eh_access_studio_copy_scope(), _eh_access_studio_internal():
            return super().copy(default=default)
