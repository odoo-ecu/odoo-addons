# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: profile model.

A profile is a named, activatable bundle of access overlays applied to
one or more users. The profile owns three kinds of rule lines:

* hidden_menu_ids: many2many of menus to remove from the navigation
* field_line_ids:  one2many of per-model field hide / readonly / required
* (more lines added in later phases: model, domain, node, search, chatter)

Active profiles are resolved per (uid, cid) and cached. The cache is
invalidated on profile create, write and unlink.

Cache invalidation pattern: ormcache keyed on uid and cid is used so
that two users on the same profile share view caches (unlike a per-uid
cache key which fragments the cache).
"""
import logging
import uuid

import psycopg2

from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# {timezone: (minute, iso date)}, see _today_for_cache_key.
_EH_TODAY_MEMO = {}


class EhAccessProfile(models.Model):
    _name = "eh.access.profile"
    _description = "User Access Studio Profile"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "active desc, sequence, name"

    name = fields.Char(
        string="Name",
        required=True,
        tracking=True,
        help="Short human-readable label shown in administration screens.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    readonly = fields.Boolean(
        string="Read-Only Mode",
        tracking=True,
        help=(
            "When enabled, users in this profile cannot create, edit or"
            " delete business records. Wizards, chatter, activities,"
            " attachments and saved filters keep working, so the session"
            " stays usable. This is enforced at"
            " the ORM gate, so it also holds for the API and for server"
            " code running as the user, not only in the interface."
            " Reading is unaffected. The Access Studio configuration is"
            " excluded so administrators are never locked out."
        ),
    )

    user_ids = fields.Many2many(
        "res.users",
        "eh_access_profile_user_rel",
        "profile_id",
        "user_id",
        string="Users",
        help="Users to whom this profile applies.",
    )
    company_ids = fields.Many2many(
        "res.company",
        "eh_access_profile_company_rel",
        "profile_id",
        "company_id",
        string="Companies",
        default=lambda self: self.env.company,
        help=(
            "Profile applies to users who can enter any of these"
            " companies, whichever company they currently have selected."
            " Matching only the selected company would let a user escape"
            " the profile with the company switcher. Leave empty when"
            " 'Apply to all companies' is enabled."
        ),
    )
    apply_to_all_companies = fields.Boolean(
        string="Apply to all companies",
        default=True,
        help=(
            "When enabled, the profile applies regardless of the user's"
            " current company. The Companies field is ignored."
        ),
    )

    date_from = fields.Date(
        string="Active From",
        help=(
            "Optional start date. Profile is treated as inactive before"
            " this date."
        ),
    )
    date_until = fields.Date(
        string="Active Until",
        help=(
            "Optional end date. A scheduled action deactivates the"
            " profile when this date has passed."
        ),
    )

    hidden_menu_ids = fields.Many2many(
        "ir.ui.menu",
        "eh_access_profile_menu_rel",
        "profile_id",
        "menu_id",
        string="Hidden Menus",
        help=(
            "Menus and submenus removed from the navigation for users in"
            " this profile."
        ),
    )

    field_line_ids = fields.One2many(
        "eh.access.field",
        "profile_id",
        string="Field Lines",
        copy=True,
    )
    model_line_ids = fields.One2many(
        "eh.access.model",
        "profile_id",
        string="Model Lines",
        copy=True,
    )
    node_line_ids = fields.One2many(
        "eh.access.node",
        "profile_id",
        string="Node Lines",
        copy=True,
    )
    chatter_line_ids = fields.One2many(
        "eh.access.chatter",
        "profile_id",
        string="Chatter Lines",
        copy=True,
    )
    domain_line_ids = fields.One2many(
        "eh.access.domain",
        "profile_id",
        string="Domain Rules",
        copy=True,
    )
    hide_chatter = fields.Boolean(
        string="Hide Chatter (everywhere)",
        tracking=True,
        help="Removes the chatter widget from every form view.",
    )
    hide_send_message = fields.Boolean(
        string="Hide 'Send Message'",
        tracking=True,
        help=(
            "Hides the Send Message button on every chatter and refuses"
            " the post at the server, including over the API."
        ),
    )
    hide_log_note = fields.Boolean(
        string="Hide 'Log Note'",
        tracking=True,
        help=(
            "Hides the Log Note button on every chatter and refuses the"
            " note at the server, including over the API."
        ),
    )
    hide_schedule_activity = fields.Boolean(
        string="Hide 'Schedule Activity'",
        tracking=True,
        help=(
            "Hides the Activities button on every chatter and refuses"
            " activities this user schedules, at the server. Activities a"
            " business flow creates on its own are not affected."
        ),
    )

    hide_import = fields.Boolean(
        string="Hide Import (everywhere)",
        tracking=True,
        help="Removes the Import button on every list and kanban view.",
    )
    hide_export = fields.Boolean(
        string="Hide Export (everywhere)",
        tracking=True,
        help="Removes the Export button on every list and kanban view.",
    )
    hide_spreadsheet = fields.Boolean(
        string="Hide Insert in Spreadsheet (everywhere)",
        tracking=True,
        help=(
            "Turns off the spreadsheet insertion capability for this"
            " user's session, so the 'Insert in Spreadsheet' entries do"
            " not appear in any cog menu."
        ),
    )
    hide_add_property = fields.Boolean(
        string="Hide Add Property (everywhere)",
        tracking=True,
        help=(
            "Removes custom-properties fields, and with them the 'Add"
            " Property' control, from the views this user opens."
        ),
    )
    hide_custom_filter = fields.Boolean(
        string="Hide the custom filter entry",
        tracking=True,
        help=(
            "Remove the entry that lets a user build their own filter"
            " from the search dropdown."
        ),
    )
    hide_custom_group = fields.Boolean(
        string="Hide the custom group by entry",
        tracking=True,
        help=(
            "Remove the entry that lets a user add their own group by"
            " from the search dropdown."
        ),
    )
    restrict_module_management = fields.Boolean(
        string="Block Installing and Upgrading Apps",
        tracking=True,
        help=(
            "Refuse install, upgrade and uninstall of Odoo modules for"
            " this user, at the server, even if they reach the Apps"
            " screen."
        ),
    )

    disable_debug_mode = fields.Boolean(
        string="Disable Developer Mode",
        tracking=True,
        help=(
            "Clears developer mode for this user on every request."
            " Developer mode lives on the session, so the flag is reset"
            " whichever route or URL is used to try to set it."
        ),
    )
    disable_login = fields.Boolean(
        string="Disable Login",
        tracking=True,
        help=(
            "Users in this profile are denied at the authentication"
            " gate. Use only when the profile is targeted at one user;"
            " applying it to a group disables the whole group."
            " Administrators (Settings or Access Rights) are never"
            " refused, so an account that can repair the configuration"
            " is always reachable."
        ),
    )

    rule_count = fields.Integer(
        compute="_compute_rule_count",
        string="Rules",
        store=False,
    )
    user_count = fields.Integer(
        compute="_compute_user_count",
        string="User Count",
        store=False,
    )
    domain_rule_count = fields.Integer(
        compute="_compute_domain_rule_count",
        string="Domain Rule Count",
        store=False,
    )
    hidden_menu_count = fields.Integer(
        compute="_compute_hidden_menu_count",
        string="Hidden Menu Count",
        store=False,
    )

    @api.depends("user_ids")
    def _compute_user_count(self):
        for profile in self:
            profile.user_count = len(profile.user_ids)

    @api.depends("domain_line_ids")
    def _compute_domain_rule_count(self):
        for profile in self:
            profile.domain_rule_count = len(profile.domain_line_ids)

    @api.depends("hidden_menu_ids")
    def _compute_hidden_menu_count(self):
        for profile in self:
            profile.hidden_menu_count = len(profile.hidden_menu_ids)

    @api.depends("hidden_menu_ids", "field_line_ids",
                 "model_line_ids", "node_line_ids", "chatter_line_ids",
                 "domain_line_ids",
                 "hide_import", "hide_export", "hide_spreadsheet",
                 "hide_add_property", "disable_debug_mode", "disable_login",
                 "hide_chatter", "readonly")
    def _compute_rule_count(self):
        for profile in self:
            count = (
                len(profile.hidden_menu_ids)
                + len(profile.field_line_ids)
                + len(profile.model_line_ids)
                + len(profile.node_line_ids)
                + len(profile.chatter_line_ids)
                + len(profile.domain_line_ids)
            )
            for flag in (
                profile.hide_import, profile.hide_export,
                profile.hide_spreadsheet, profile.hide_add_property,
                profile.disable_debug_mode, profile.disable_login,
                profile.hide_chatter, profile.readonly,
            ):
                if flag:
                    count += 1
            profile.rule_count = count

    @api.constrains("name")
    def _check_name_unique(self):
        for record in self:
            if not record.name:
                continue
            # active_test=False so an archived profile still reserves
            # its name: import / export matches profiles by name, and a
            # second profile with the same name would make that
            # ambiguous the moment the archived one is restored.
            twin = self.with_context(active_test=False).search_count([
                ("name", "=", record.name),
                ("id", "!=", record.id),
            ])
            if twin:
                raise ValidationError(_(
                    "Access Studio: a profile named %(name)s already"
                    " exists. Pick a different name so import / export"
                    " can match by name unambiguously.",
                    name=record.name,
                ))

    @api.constrains("user_ids", "readonly")
    def _check_no_admin_in_readonly(self):
        admin_groups = (
            self.env.ref("base.group_system", raise_if_not_found=False),
            self.env.ref("base.group_erp_manager", raise_if_not_found=False),
        )
        admin_groups = tuple(g for g in admin_groups if g)
        if not admin_groups:
            return
        admin_user_ids = set()
        for group in admin_groups:
            # Membership field names differ by series, and picking the
            # wrong one silently disarms the guard:
            #   19: all_user_ids (implied) / user_ids (direct only)
            #   16, 17, 18: users
            # Implied membership matters here, because an administrator
            # who holds base.group_system through an implying group must
            # still be recognised as an administrator.
            members = None
            for candidate in ("all_user_ids", "users", "user_ids"):
                if candidate in group._fields:
                    members = group[candidate]
                    break
            if members is None:
                _logger.error(
                    "Access Studio: cannot read the membership of group"
                    " %s, refusing to weaken the administrator guard",
                    group.id,
                )
                raise ValidationError(_(
                    "Access Studio cannot verify administrator membership"
                    " on this Odoo version, so the profile was not saved."
                ))
            admin_user_ids.update(members.ids)
        for profile in self:
            if not profile.readonly:
                continue
            for user in profile.user_ids:
                if user.id in admin_user_ids:
                    raise ValidationError(_(
                        "Access Studio: %(user)s is an administrator and"
                        " cannot be placed in a read-only profile.",
                        user=user.display_name,
                    ))

    @api.constrains("date_from", "date_until")
    def _check_date_window(self):
        for profile in self:
            if (profile.date_from and profile.date_until
                    and profile.date_from > profile.date_until):
                raise ValidationError(_(
                    "Access Studio: 'Active From' must not be later than"
                    " 'Active Until'."
                ))

    def action_toggle_active(self):
        for profile in self:
            profile.active = not profile.active
        return True

    def _eh_lock_policy(self):
        """Serialize all policy edits, including relation-only rule changes.

        The shared governance lock comes first. A reviewed import may edit
        direct user assignments after taking these rows, so reversing that
        order could deadlock against the governance membership reconciler.
        """
        from .eh_access_role import _lock_governance_users
        _lock_governance_users(self.env)
        if self.ids:
            self.env.cr.execute(
                "SELECT id FROM eh_access_profile WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                [self.ids],
            )
            # Odoo uses repeatable-read transactions. A lock alone does not
            # refresh an older snapshot after a concurrent child insertion.
            # Create a new parent row version without changing its values:
            # conflicting older snapshots then receive a serialization retry.
            self.env.cr.execute(
                "UPDATE eh_access_profile SET write_date = write_date WHERE id = ANY(%s)",
                [self.ids],
            )

    @api.model_create_multi
    def create(self, vals_list):
        self._eh_lock_policy()
        records = super().create(vals_list)
        if not self.env.context.get("eh_access_studio_skip_invalidate"):
            self._invalidate_active_cache()
        return records

    def write(self, vals):
        self._eh_lock_policy()
        result = super().write(vals)
        if not self.env.context.get("eh_access_studio_skip_invalidate"):
            self._invalidate_active_cache()
        return result

    def unlink(self):
        self._eh_lock_policy()
        result = super().unlink()
        if not self.env.context.get("eh_access_studio_skip_invalidate"):
            self._invalidate_active_cache()
        return result

    @api.model
    def _invalidate_active_cache(self):
        """Clear ormcaches and signal other workers.

        Implementation note: in Odoo 19, the @tools.ormcache decorator
        does NOT expose a per-method clear_cache. The only way to
        invalidate ormcaches is via Registry.clear_cache(*cache_names)
        which clears entire cache categories.

        We must clear TWO categories, not one:

        * 'default' holds our own five ormcached lookups (the active
          profile set, the field / node / chatter rule maps).
        * 'templates' holds the post-processed view arch. Odoo caches
          get_view with cache='templates' (addons/base/models/
          ir_ui_view.py, `_get_view_cache`), and 'default' does NOT
          include it: Registry._CACHES_BY_KEY maps 'default' to
          ('default', 'templates.cached_values') only. Every UI rule we
          apply (hidden fields, buttons, pages, filters, chatter, the
          create / edit / delete attributes) is stamped during that
          post-processing, so clearing 'default' alone leaves every
          worker serving the arch built from the PREVIOUS profile
          state until something else happens to evict 'templates'.

        Clearing both categories is still much cheaper than
        clear_all_caches(), which additionally drops 'assets',
        'routing' and 'groups'.

        Cross-worker propagation is deliberately NOT signalled inline.
        clear_cache() records the categories in registry.cache_invalidated,
        and the framework signals them once the transaction commits
        (odoo/service/model.py calls registry.signal_changes() after the
        cursor closes, and registry.reset_changes() when it fails).
        Calling signal_changes() ourselves mid-write clears that set, so
        the framework's post-commit signal would find nothing to send
        and other workers would keep serving the old overlay. We instead
        register a post-commit callback, which covers the paths that
        have no framework signal of their own, such as a shell session
        or a scheduled job.
        """
        try:
            registry = self.env.registry
            transaction = getattr(self.env, "transaction", None)
            native_invalidate = getattr(transaction, "invalidate_ormcache", None)
            if native_invalidate:
                # Odoo 20 owns caches and cross-worker signaling on the
                # transaction. Its read-access cache must be invalidated
                # as well, or an earlier allowed read survives a rule edit.
                native_invalidate("default")
                native_invalidate("templates")
                transaction.invalidate_access_cache()
            else:
                registry.clear_cache("default", "templates")
        except Exception:
            _logger.exception(
                "Access Studio: registry.clear_cache failed"
            )
            return
        try:
            if hasattr(registry, "signal_changes"):
                self.env.cr.postcommit.add(registry.signal_changes)
        except Exception:
            _logger.exception(
                "Access Studio: could not register the post-commit"
                " cache signal"
            )
        try:
            from .res_users import _EH_LOGIN_BLOCK_POSSIBLE
            _EH_LOGIN_BLOCK_POSSIBLE.clear()
        except Exception:
            _logger.exception("Access Studio: could not clear the sign-in stamp")
        try:
            # The Odoo 19 web client keeps get_views, /web/action/load and
            # the menus in the browser (IndexedDB and localStorage), keyed
            # on session_info's registry_hash, and serves that copy first.
            # ir.http folds this stamp into the hash for users under a
            # profile, so an edit here retires their browser copy on the
            # next page load instead of showing yesterday's screens.
            self.env["ir.config_parameter"].sudo().set_param(
                "eh_access_studio.config_stamp", uuid.uuid4().hex,
            )
        except Exception:
            _logger.exception(
                "Access Studio: could not stamp the configuration change"
            )

    @api.model
    @tools.ormcache("uid", "cid", "today_iso")
    def _get_active_profile_ids(self, uid, cid, today_iso):
        """Return profile ids active for a user under a given company on
        a specific date.

        Cached on (uid, cid, today_iso). Including the date in the
        cache key lets a profile expire correctly across midnight
        without waiting for a manual cache flush.

        Invalidation happens on profile / line create, write or unlink.

        COMPANY SCOPE: a company-scoped profile applies to a user who
        can enter ANY of its companies, not only while that company is
        the active one. The active company comes from the company
        switcher, which the user controls, so matching on it alone let a
        user step out of a company-scoped profile by switching company.
        `cid` stays in the signature and the cache key for compatibility
        and because callers still pass it, but it no longer narrows the
        answer.
        """
        try:
            user_company_ids = (
                self.env["res.users"].sudo().browse(uid).company_ids.ids
                or [cid]
            )
            domain = [
                ("active", "=", True),
                ("user_ids", "in", uid),
                "|", ("apply_to_all_companies", "=", True),
                ("company_ids", "in", user_company_ids),
                "|", ("date_from", "=", False),
                ("date_from", "<=", today_iso),
                "|", ("date_until", "=", False),
                ("date_until", ">=", today_iso),
            ]
            return tuple(self.sudo().search(domain).ids)
        except psycopg2.errors.UndefinedTable:
            # Table not yet created (first install path before our model
            # tables exist). Return empty so callers fall back to
            # default behaviour.
            return ()

    def _register_hook(self):
        # The RPC entry hooks are installed when this package is
        # imported. A controller module imported later (a server-wide
        # module loaded after us) is picked up here, once the registry
        # is ready.
        result = super()._register_hook()
        from .base_model import (
            _eh_install_rpc_entry_hooks, _eh_reset_derivation_caches,
            _eh_sync_api_markers,
        )
        _eh_install_rpc_entry_hooks()
        _eh_reset_derivation_caches()
        try:
            _eh_sync_api_markers(self.env)
        except Exception:
            _logger.exception("Access Studio: could not sync API markers")
        return result

    @api.model
    def _eh_listed_in_any_profile(self, uid):
        """Is this user named on ANY active profile? True, False or None.

        The failure posture used to be "we could not resolve the profile
        set, so treat everyone but administrators as restricted". That
        turned one failed lookup into a lockout for portal users, public
        visitors and every internal user nobody had put under a profile.
        This is the cheap second opinion the gates ask before failing
        closed: one indexed query on the membership table, no ORM, no
        cache, no date or company logic. It deliberately ignores the
        time window, so it can only over-report, never under-report.

        None means even this could not be answered, and the caller keeps
        its conservative default.
        """
        try:
            with self.env.cr.savepoint(flush=False):
                return self._eh_listed_query(uid)
        except Exception:
            _logger.exception(
                "Access Studio: profile membership check failed for user %s",
                uid,
            )
            return None

    @api.model
    def _eh_time_bound_user_ids(self, user_ids):
        """Of these users, the ones on an active profile with a date
        window. Raw SQL, like the membership check above."""
        if not user_ids:
            return set()
        self.env.cr.execute(
            """
            SELECT DISTINCT rel.user_id
              FROM eh_access_profile_user_rel rel
              JOIN eh_access_profile profile ON profile.id = rel.profile_id
             WHERE rel.user_id IN %s AND profile.active
               AND (profile.date_from IS NOT NULL OR profile.date_until IS NOT NULL)
            """,
            (tuple(user_ids),),
        )
        return {row[0] for row in self.env.cr.fetchall()}

    def _eh_listed_query(self, uid):
        # Raw SQL on purpose: no ORM, no cache, nothing that can recurse
        # into the gates this answers for.
        if uid:
            self.env.cr.execute(
                """
                SELECT 1
                  FROM eh_access_profile_user_rel rel
                  JOIN eh_access_profile profile ON profile.id = rel.profile_id
                 WHERE rel.user_id = %s AND profile.active
                 LIMIT 1
                """,
                (uid,),
            )
            return bool(self.env.cr.fetchone())
        return False

    @api.model
    def _eh_safe_company_id(self):
        """The company to resolve a profile against, safely.

        env.company reads allowed_company_ids out of the context, which
        arrives verbatim from the RPC payload, and it RAISES when that
        list names a company the user may not enter. Anything that let
        that exception escape turned a forged context key into a way of
        switching the overlay off, so every lookup in this module goes
        through here and falls back to the user's own company.
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

    @api.model
    def _eh_safe_company_ids(self):
        """The allowed company set, resolved the same safe way."""
        try:
            return self.env.companies.ids
        except Exception:
            return self.env.user.sudo().company_ids.ids or [
                self._eh_safe_company_id()
            ]

    @api.model
    @tools.ormcache("uid", "today_iso")
    def _eh_login_disabled_for(self, uid, today_iso):
        """True when an active profile disables sign-in for `uid`.

        Cached, because it is consulted on EVERY request to end sessions
        that were already open when the block was switched on, and on
        every API-key authentication. Invalidated with the rest of the
        overlay caches.
        """
        try:
            return bool(self.sudo().search_count([
                ("disable_login", "=", True),
                ("active", "=", True),
                ("user_ids", "in", uid),
                "|", ("date_from", "=", False),
                ("date_from", "<=", today_iso),
                "|", ("date_until", "=", False),
                ("date_until", ">=", today_iso),
            ]))
        except psycopg2.errors.UndefinedTable:
            return False

    @api.model
    def _eh_active_profile_ids_any_company(self):
        """Profiles active for this user in ANY company they may enter.

        Enforcement resolves against every allowed company rather than
        the active one. The active company comes from the company
        switcher, which the user controls, so resolving against it alone
        let a user step out of a company-scoped profile simply by
        switching to another company. Each per-company answer is still
        served from the ormcached _get_active_profile_ids.
        """
        return self._get_active_profile_ids(
            self.env.user.id,
            self._eh_safe_company_id(),
            self._today_for_cache_key(),
        )

    @api.model
    def _today_for_cache_key(self):
        """ISO-format today, used as part of the active-profile cache key.

        Deliberately NOT fields.Date.context_today: that reads tz out of
        the context, which arrives from the RPC payload, so a caller
        could shift "today" by up to a day and step outside or inside a
        time-bound profile at will. The timezone stored on the user
        record is used instead, and it cannot be forged from a request.
        """
        import time
        user_tz = (self.env.user.sudo().tz or "UTC")
        minute = int(time.time() // 60)
        cached = _EH_TODAY_MEMO.get(user_tz)
        if cached and cached[0] == minute:
            return cached[1]
        import pytz
        from datetime import datetime
        try:
            zone = pytz.timezone(user_tz)
        except Exception:
            zone = pytz.UTC
        value = datetime.now(pytz.UTC).astimezone(zone).date().isoformat()
        # Memoised per timezone for the current minute: this sits on the
        # hot path of every gate lookup, and pytz plus now() on each call
        # was the largest single cost for users with no profile.
        _EH_TODAY_MEMO[user_tz] = (minute, value)
        return value

    @api.model
    @tools.ormcache("profile_ids", "model_name")
    def _eh_domain_rule_ids_for(self, profile_ids, model_name):
        """Cached ids of the domain rules for these profiles on a model.

        The record-rule engine asks for them on EVERY query, and core's
        own answer is cached; searching the table each time turned one
        cached lookup into a database read per query. The rules
        themselves are still read and evaluated live, so a placeholder
        keeps resolving per request.
        """
        try:
            return tuple(self.env["eh.access.domain"].sudo().with_context(
                eh_access_studio_bypass=True,
            ).search([
                ("profile_id", "in", list(profile_ids)),
                ("model_name", "=", model_name),
            ]).ids)
        except psycopg2.errors.UndefinedTable:
            return ()

    @api.model
    @tools.ormcache("uid", "cid", "today_iso", "model_name")
    def _model_line_ids_for(self, uid, cid, today_iso, model_name):
        """Cached lookup of eh.access.model lines for (user, company,
        date, model). Used by base._get_view and base.get_views to
        avoid running a fresh search for every view-render."""
        try:
            profile_ids = self._get_active_profile_ids(uid, cid, today_iso)
            if not profile_ids:
                return ()
            return tuple(self.env["eh.access.model"].sudo().search([
                ("profile_id", "in", list(profile_ids)),
                ("model_name", "=", model_name),
            ]).ids)
        except psycopg2.errors.UndefinedTable:
            return ()

    @api.model
    @tools.ormcache("uid", "cid", "today_iso", "model_name")
    def _chatter_visibility_for(self, uid, cid, today_iso, model_name):
        """Cached chatter visibility flags for the OWL patch RPC.

        Returns a 3-tuple (hide_send, hide_log, hide_schedule). Tuples
        are cheap to cache; the RPC method below converts to a dict
        for the JS contract.
        """
        try:
            profile_ids = self._get_active_profile_ids(uid, cid, today_iso)
            if not profile_ids:
                return (False, False, False)
            profiles = self.sudo().browse(profile_ids)
            per_model_hide = bool(self.env["eh.access.chatter"].sudo().search_count([
                ("profile_id", "in", list(profile_ids)),
                ("model_name", "=", model_name),
            ]))
            global_hide = any(profiles.mapped("hide_chatter")) or per_model_hide
            return (
                global_hide or any(profiles.mapped("hide_send_message")),
                global_hide or any(profiles.mapped("hide_log_note")),
                global_hide or any(profiles.mapped("hide_schedule_activity")),
            )
        except psycopg2.errors.UndefinedTable:
            return (False, False, False)

    def _get_active_profiles_for_current_user(self):
        """Convenience: recordset of active profiles for env.user / env.company."""
        return self.browse(self._eh_active_profile_ids_any_company())

    @api.model
    def get_chatter_button_visibility(self, model_name):
        """Public RPC: tell the OWL chatter patch which buttons to hide.

        Cached per (uid, cid, today, model_name). The OWL patch calls
        this on every chatter mount; without caching that's one DB
        query per chatter render.

        model_name comes from the client, and it becomes part of an
        ormcache key, so an unknown string would let any authenticated
        caller grow a shared cache without bound. We only answer for
        models that actually exist in the registry.
        """
        if not isinstance(model_name, str) or model_name not in self.env:
            return {
                "hide_send_message": False,
                "hide_log_note": False,
                "hide_schedule_activity": False,
            }
        send, log, schedule = self._chatter_visibility_for(
            self.env.user.id,
            self._eh_safe_company_id(),
            self._today_for_cache_key(),
            model_name,
        )
        return {
            "hide_send_message": send,
            "hide_log_note": log,
            "hide_schedule_activity": schedule,
        }

    @api.model
    def get_search_control_visibility(self):
        """Public RPC: which search dropdown entries to hide.

        Mirrors get_chatter_button_visibility. Takes no arguments, so
        there is no client-supplied value to validate, and the answer is
        the same for every model.
        """
        profile_ids = self._eh_active_profile_ids_any_company()
        if not profile_ids:
            return {"hide_custom_filter": False, "hide_custom_group": False}
        profiles = self.sudo().browse(profile_ids)
        return {
            "hide_custom_filter": any(profiles.mapped("hide_custom_filter")),
            "hide_custom_group": any(profiles.mapped("hide_custom_group")),
        }

    @api.model
    def get_interface_flags(self):
        """Public RPC: the profile switches the interface can act on
        without naming a model.

        Odoo 16 draws the chatter and the search menus with components
        this module does not patch there, so the client marks the
        document body from these flags and a stylesheet does the hiding.
        Only the profile-wide switches are answered; a per-model chatter
        rule removes the whole chatter from the arch on the server.
        """
        blank = {
            "hide_send_message": False,
            "hide_log_note": False,
            "hide_schedule_activity": False,
            "hide_custom_filter": False,
            "hide_custom_group": False,
        }
        profile_ids = self._eh_active_profile_ids_any_company()
        if not profile_ids:
            return blank
        profiles = self.sudo().browse(profile_ids)
        whole_chatter = any(profiles.mapped("hide_chatter"))
        return {
            "hide_send_message": whole_chatter or any(profiles.mapped("hide_send_message")),
            "hide_log_note": whole_chatter or any(profiles.mapped("hide_log_note")),
            "hide_schedule_activity": whole_chatter or any(
                profiles.mapped("hide_schedule_activity")
            ),
            "hide_custom_filter": any(profiles.mapped("hide_custom_filter")),
            "hide_custom_group": any(profiles.mapped("hide_custom_group")),
        }

    def action_duplicate_profile(self):
        """Copy the profile under a new name and open the copy."""
        self.ensure_one()
        clone = self.copy(default={
            "name": _("%(name)s (copy)", name=self.name),
            "active": False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "eh.access.profile",
            "view_mode": "form",
            "res_id": clone.id,
            "target": "current",
        }

    def action_diagnostic(self):
        """Render a one-screen summary of what this profile does."""
        self.ensure_one()
        lines = []
        if self.readonly:
            lines.append(_("- Read-only mode: blocks create / edit / delete on every model"))
        if self.hide_chatter:
            lines.append(_("- Chatter hidden on every form view"))
        for flag, label in (
            ("hide_send_message", _("Send Message button hidden")),
            ("hide_log_note", _("Log Note button hidden")),
            ("hide_schedule_activity", _("Activities button hidden")),
            ("hide_import", _("Import button hidden everywhere")),
            ("hide_export", _("Export button hidden everywhere")),
            ("hide_spreadsheet", _("Insert in Spreadsheet turned off for the session")),
            ("hide_add_property", _("Custom properties and Add Property hidden everywhere")),
            ("disable_login", _("Sign-in disabled for the targeted users")),
            ("disable_debug_mode", _("Developer mode disabled (debug=0 enforced)")),
        ):
            if self[flag]:
                lines.append("- " + label)
        if self.hidden_menu_ids:
            sample = self.hidden_menu_ids[:6]
            tail = "..." if len(self.hidden_menu_ids) > 6 else ""
            lines.append(_(
                "- %(n)s menu(s) hidden: %(names)s%(tail)s",
                n=len(self.hidden_menu_ids),
                names=", ".join(sample.mapped("display_name")),
                tail=tail,
            ))
        if self.field_line_ids:
            lines.append(_(
                "- %(n)s field rule(s) across %(models)s model(s)",
                n=len(self.field_line_ids),
                models=len(self.field_line_ids.mapped("model_id")),
            ))
        if self.model_line_ids:
            lines.append(_(
                "- %(n)s model rule(s) across %(models)s model(s)",
                n=len(self.model_line_ids),
                models=len(self.model_line_ids.mapped("model_id")),
            ))
        if self.node_line_ids:
            lines.append(_(
                "- %(n)s node rule(s) (button / tab / link / filter)",
                n=len(self.node_line_ids),
            ))
        if self.chatter_line_ids:
            lines.append(_(
                "- %(n)s per-model chatter override(s)",
                n=len(self.chatter_line_ids),
            ))
        if self.domain_line_ids:
            lines.append(_(
                "- %(n)s domain rule(s): strict ORM-level gates",
                n=len(self.domain_line_ids),
            ))
        if not lines:
            lines = [_("- No rules configured. The profile is a no-op.")]

        body = _(
            "%(name)s applies to %(users)s user(s) under %(scope)s.\n\n%(body)s",
            name=self.name,
            users=len(self.user_ids),
            scope=_("all companies") if self.apply_to_all_companies
                  else _("%(n)s company / companies", n=len(self.company_ids)),
            body="\n".join(lines),
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": _("Profile summary: %(name)s", name=self.name),
                "message": body,
                "sticky": True,
            },
        }

    def action_test_plan(self):
        """Render a test plan for this profile.

        We deliberately do not impersonate a user from this action.
        Session-level impersonation is risky enough that we want each
        invocation to be an explicit, conscious step (sign in as the
        target user in a private browser window). What we surface here
        is a deterministic, repeatable test plan listing the rules that
        will fire and the users to test as.
        """
        self.ensure_one()
        if not self.user_ids:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "warning",
                    "title": _("Test plan not available"),
                    "message": _("Add at least one user to the profile first."),
                    "sticky": False,
                },
            }
        # Display by display_name (not login) to keep PII off-screen
        # where possible. Logins are visible in the User form anyway.
        names = ", ".join(self.user_ids.mapped("display_name"))
        message = _(
            "Sign in as one of %(users)s in a private browser window."
            " The active-profile cache, the ir.rule extension and the"
            " view post-processors all run on every page load, so the"
            " first navigation exercises the full enforcement path.",
            users=names,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": _("Test plan: %(name)s", name=self.name),
                "message": message,
                "sticky": True,
            },
        }

    @api.model
    def action_health_check(self):
        """Scan the configuration for common issues.

        Surfaces (in order):
          * profiles with no users assigned
          * profiles with no rules and no global toggles
          * profiles whose Active Until date has passed but are still
            active (cron has not yet run, or it failed)
          * domain rules whose stored domain does not parse
        """
        import ast
        warnings = []
        active = self.search([("active", "=", True)])

        no_users = active.filtered(lambda p: not p.user_ids)
        if no_users:
            warnings.append(_(
                "%(n)s active profile(s) have no users assigned: %(names)s",
                n=len(no_users),
                names=", ".join(no_users[:5].mapped("name"))
                + ("..." if len(no_users) > 5 else ""),
            ))

        no_op = active.filtered(lambda p: p.rule_count == 0)
        if no_op:
            warnings.append(_(
                "%(n)s active profile(s) have no rules (no-op):"
                " %(names)s",
                n=len(no_op),
                names=", ".join(no_op[:5].mapped("name"))
                + ("..." if len(no_op) > 5 else ""),
            ))

        today = fields.Date.context_today(self)
        expired = active.filtered(
            lambda p: p.date_until and p.date_until < today,
        )
        if expired:
            warnings.append(_(
                "%(n)s active profile(s) past their Active Until date"
                " (cron has not deactivated them yet): %(names)s",
                n=len(expired),
                names=", ".join(expired[:5].mapped("name"))
                + ("..." if len(expired) > 5 else ""),
            ))

        bad_domains = []
        domain_rules = self.env["eh.access.domain"].sudo().search([
            ("apply_filter", "=", True),
        ])
        for rule in domain_rules:
            if not rule.domain:
                bad_domains.append(rule)
                continue
            try:
                parsed = ast.literal_eval(rule.domain)
                if not isinstance(parsed, list):
                    bad_domains.append(rule)
            except (ValueError, SyntaxError):
                bad_domains.append(rule)
        if bad_domains:
            unique_profile_names = sorted({r.profile_id.name for r in bad_domains})
            warnings.append(_(
                "%(n)s domain rule(s) cannot be parsed and will fail"
                " closed at runtime. Profiles affected: %(names)s",
                n=len(bad_domains),
                names=", ".join(unique_profile_names[:5])
                + ("..." if len(unique_profile_names) > 5 else ""),
            ))

        if not warnings:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "success",
                    "title": _("Access Studio: health check passed"),
                    "message": _(
                        "No issues found across %(n)s active profile(s).",
                        n=len(active),
                    ),
                    "sticky": False,
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning",
                "title": _("Access Studio: health check"),
                "message": "\n\n".join(warnings),
                "sticky": True,
            },
        }

    @api.model
    def action_conflict_report(self):
        """Detect profiles that contradict each other for the same user.

        We surface the most common operational class of conflict: a
        user is in two active profiles that both target the same model
        on the Domain Access tab with different filters or different
        rights. Returns a sticky notification listing every conflict so
        admins can rationalise their setup.
        """
        Domain = self.env["eh.access.domain"].sudo()
        active = self.search([("active", "=", True)])
        # Build user -> [(profile, domain_line)] mapping
        rows = []
        for profile in active:
            for line in profile.domain_line_ids:
                for user in profile.user_ids:
                    rows.append((user, profile, line))

        by_user_model = {}
        for user, profile, line in rows:
            key = (user.id, line.model_name)
            by_user_model.setdefault(key, []).append((profile, line))

        conflicts = []
        for (user_id, model_name), entries in by_user_model.items():
            if len(entries) < 2:
                continue
            distinct = set()
            for profile, line in entries:
                distinct.add((
                    line.read_right, line.create_right,
                    line.write_right, line.delete_right,
                    line.apply_filter, line.domain or "[]",
                ))
            if len(distinct) > 1:
                user = self.env["res.users"].browse(user_id)
                # Dedupe profile names so a user in three rules across
                # two profiles renders cleanly as 'A, B' rather than
                # 'A, A, B'. The local variable was previously named
                # `_` which shadowed the translation function inside
                # the comprehension; rename to avoid the optical
                # confusion.
                unique_profiles = sorted({
                    profile.name for profile, _line in entries
                })
                names = ", ".join(unique_profiles)
                conflicts.append(_(
                    "%(user)s on %(model)s: %(profiles)s",
                    user=user.display_name,
                    model=model_name,
                    profiles=names,
                ))

        if not conflicts:
            message = _("No domain-rule conflicts detected.")
        else:
            # Cap displayed conflicts so a degenerate setup (hundreds of
            # contradictions) does not produce a wall of text the user
            # cannot read.
            CAP = 25
            shown = conflicts[:CAP]
            extra = len(conflicts) - len(shown)
            tail = (
                _("\n... and %(extra)s more.", extra=extra)
                if extra > 0 else ""
            )
            message = _(
                "%(n)s conflict(s) detected:\n\n%(list)s%(tail)s\n\n"
                "Resolution: deactivate one of the profiles, or align"
                " their domain filters so they describe the same record"
                " set.",
                n=len(conflicts),
                list="\n".join("- " + c for c in shown),
                tail=tail,
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning" if conflicts else "info",
                "title": _("Conflict report"),
                "message": message,
                "sticky": True,
            },
        }

    @api.model
    def _eh_ensure_cron_schedule(self):
        """Bring the time-window job to its current schedule.

        The cron record is noupdate, which is right for a user who has
        tuned it, but it means an existing install would keep the old
        daily interval for ever. Called from a data file on every
        install and upgrade; it only ever moves a DAILY job to hourly,
        so a schedule an administrator changed deliberately is kept.
        """
        cron = self.env.ref(
            "eh_access_studio.cron_deactivate_expired_profiles",
            raise_if_not_found=False,
        )
        if cron and cron.interval_type == "days" and cron.interval_number == 1:
            cron.sudo().write({"interval_type": "hours", "interval_number": 1})
        return True

    def _cron_invalidate_for_date_boundary(self):
        """Clear the overlay caches when a time window opens today.

        Expiry already invalidates, because the cron archives the
        profile. A profile whose date_from is reached has no such event,
        and core caches the menu tree on (uid, debug, lang) with no date
        in the key, so without this the navigation would not start
        hiding anything until something else evicted the cache.
        """
        # The UTC date is not the users' date. Somewhere in the world it
        # is still yesterday or already tomorrow, and core caches the
        # menu tree with no date in its key, so any window that opens or
        # closes within a day of now invalidates the caches. The job
        # runs hourly, which bounds how long a boundary can be missed.
        from datetime import timedelta
        today = fields.Date.today()
        lo, hi = today - timedelta(days=1), today + timedelta(days=1)
        starting = self.sudo().search_count([
            ("active", "=", True),
            "|",
            "&", ("date_from", ">=", lo), ("date_from", "<=", hi),
            "&", ("date_until", ">=", lo), ("date_until", "<=", hi),
        ])
        if starting:
            _logger.info(
                "Access Studio: %s profile(s) start today, invalidating"
                " the overlay caches", starting,
            )
            self._invalidate_active_cache()
        return starting

    def _cron_deactivate_expired_profiles(self):
        """Deactivate profiles whose Active Until has passed.

        Per-record savepoint keeps a single bad row from blocking the
        whole batch. The cache is invalidated once at the end rather
        than once per profile so a thousand-row cleanup does not fire a
        thousand registry signals.

        The same daily run also handles the other end of a time window,
        see _cron_invalidate_for_date_boundary.
        """
        self._cron_invalidate_for_date_boundary()
        # Archive only once the last local day of the window is over
        # EVERYWHERE, i.e. a day after the UTC date passes it. The live
        # check already ends the profile at each user's own midnight;
        # archiving on the UTC date cut it short for users behind UTC.
        from datetime import timedelta
        today = fields.Date.today() - timedelta(days=1)
        expired = self.search([
            ("active", "=", True),
            ("date_until", "!=", False),
            ("date_until", "<", today),
        ])
        if not expired:
            return True
        deactivated = 0
        for profile in expired:
            with self.env.cr.savepoint():
                # Skip the per-row invalidation by writing in a context
                # that the line mixin and our own write hooks honour.
                # We fall back to a single invalidate call below.
                profile.with_context(eh_access_studio_skip_invalidate=True).active = False
                profile.with_context(eh_access_studio_skip_invalidate=True).message_post(
                    body=_(
                        "Access Studio: profile auto-deactivated because"
                        " its Active Until date (%(date)s) has passed.",
                        date=profile.date_until,
                    ),
                )
                deactivated += 1
        # One bulk invalidation at the end.
        self._invalidate_active_cache()
        _logger.info(
            "Access Studio: cron deactivated %s expired profile(s).",
            deactivated,
        )
        return True
