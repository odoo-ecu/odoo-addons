# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: authentication and web-client gates.

* The sign-in gate runs in _login, not only in _check_credentials.
  _check_credentials is the password path; an authentication provider
  such as OAuth signs a user in without ever calling it, and a provider
  loaded after this module could also catch the AccessDenied raised
  from inside it. _login is the single funnel every interactive sign-in
  goes through, so the gate sits there and _check_credentials keeps a
  second copy for direct callers.
* The disable_debug_mode toggle is enforced in models/ir_http.py, which
  clears the flag on the SESSION for every route. controllers/web.py
  only tidies the address bar; it is not the gate.
"""
import contextlib
import logging

from odoo import api, fields, models, release, SUPERUSER_ID, _
from odoo.exceptions import AccessDenied

from .base_model import _eh_rpc_entry

_logger = logging.getLogger(__name__)

# _login is a classmethod with a db argument before Odoo 19.
_EH_LOGIN_IS_CLASSMETHOD = release.version_info[0] < 19

# {(db, date): whether any profile disables sign-in}. Core caches a
# password check per (uid, password), so the gate runs on every API call;
# opening a cursor each time to learn that no profile blocks anybody was
# most of its cost. Re-read once a day, and whenever a profile changes.
_EH_LOGIN_BLOCK_POSSIBLE = {}


def _eh_login_block_possible(db, cls):
    """Could ANY profile refuse a sign-in in this database today?"""
    import time
    # A minute, not a day: only the worker that wrote the profile clears
    # this, so a blocked account could keep working elsewhere until the
    # stamp expired.
    key = (db, int(time.time() // 60))
    known = _EH_LOGIN_BLOCK_POSSIBLE.get(key)
    if known is not None:
        return known
    try:
        with contextlib.closing(cls.pool.cursor()) as cr:
            cr.execute(
                "SELECT 1 FROM eh_access_profile"
                " WHERE active AND disable_login LIMIT 1"
            )
            known = bool(cr.fetchone())
    except Exception:
        known = True  # unknown means ask, every time
    _EH_LOGIN_BLOCK_POSSIBLE.clear()
    _EH_LOGIN_BLOCK_POSSIBLE[key] = known
    return known

# Contact fields a user edits as personal settings in their preferences.
# Read-Only Mode leaves these alone; field locks still apply to them.
_EH_PERSONAL_SETTINGS = frozenset((
    "tz", "lang", "image_1920", "image_1024", "image_512", "image_256",
    "image_128",
))

# Odoo 20 checks ordinary ACLs on preferences and their inherited contact;
# earlier versions elevated the core safe-field write. Exempt only the
# documented personal settings from our global read-only toggle, without
# elevating native permissions or exempting business fields.
_EH_PERSONAL_SETTINGS_TOKEN = object()


def _eh_personal_settings_scope(env):
    if (
        release.version_info[0] >= 20
        and env.context.get("_eh_personal_settings_token") is _EH_PERSONAL_SETTINGS_TOKEN
        and env.context.get("_eh_personal_settings_uid") == env.uid
    ):
        return (env.uid, env.context.get("_eh_personal_settings_partner"))
    return None


def _eh_without_personal_settings(records):
    return records.with_context(
        _eh_personal_settings_token=None,
        _eh_personal_settings_uid=None,
        _eh_personal_settings_partner=None,
    )


class ResUsers(models.Model):
    _inherit = "res.users"

    access_profile_ids = fields.Many2many(
        "eh.access.profile",
        "eh_access_profile_user_rel",
        "user_id",
        "profile_id",
        string="Access Studio Profiles",
        readonly=True,
        # A duplicated user must not inherit restrictions silently, and
        # copying the link made Duplicate fail for user administrators
        # who cannot write Access Studio profiles.
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if any("access_profile_ids" in (vals or {}) for vals in vals_list):
            if "eh.access.profile" in self.env:
                self.env["eh.access.profile"]._invalidate_active_cache()
        return records

    def write(self, vals):
        """Keep the overlay caches honest when membership is written
        from the user side.

        access_profile_ids is the mirror of eh.access.profile.user_ids
        on the same relation table, so a write here never reaches
        eh.access.profile.write and would otherwise leave every worker
        serving the previous overlay for this user.
        """
        if vals and not self.env.su and self == self.env.user:
            self._eh_access_studio_check_own_preferences(vals)
        if (
            vals and "tz" in vals and not self.env.su
            and _eh_rpc_entry() is not None
            and any(user.sudo().tz != vals["tz"] for user in self)
        ):
            # Only a change the person asked for. Core writes the
            # browser timezone onto the user DURING sign-in on Odoo 16,
            # 17 and 18, outside any RPC call, and refusing that write
            # meant a user on a dated profile could never sign in.
            # Only a real change: a client that resends the whole record
            # must still be able to save the rest of the preferences.
            self._eh_access_studio_check_tz_change(self.ids)
        writing = self
        if release.version_info[0] >= 20:
            if vals and not self.env.su and self == self.env.user and not (set(vals) - _EH_PERSONAL_SETTINGS):
                writing = self.with_context(
                    _eh_personal_settings_token=_EH_PERSONAL_SETTINGS_TOKEN,
                    _eh_personal_settings_uid=self.id,
                    _eh_personal_settings_partner=self.sudo().partner_id.id,
                )
            elif _eh_personal_settings_scope(self.env):
                # A nested write must qualify independently; an inverse or
                # third-party hook may write business data in this stack.
                writing = _eh_without_personal_settings(self)
        result = super(ResUsers, writing).write(vals)
        if "access_profile_ids" in vals and "eh.access.profile" in self.env:
            self.env["eh.access.profile"]._invalidate_active_cache()
        return result

    def _eh_readonly_applies(self):
        scope = _eh_personal_settings_scope(self.env)
        if scope and self.ids == [scope[0]]:
            return False
        return super()._eh_readonly_applies()

    # ---- one gate, four sign-in contracts ------------------------------
    #
    # _login is a CLASSMETHOD on Odoo 16, 17 and 18 and an ordinary
    # method on 19, and its arguments changed twice: (db, login,
    # password, user_agent_env) on 16 and 17, (db, credential,
    # user_agent_env) on 18, (credential, user_agent_env) on 19. An
    # override with one signature does not merely miss the gate on the
    # other series: it makes authenticate() raise, so nobody can sign in
    # at all. Each series therefore gets the shape core calls, and both
    # hand the same gate the user they resolved.

    if _EH_LOGIN_IS_CLASSMETHOD:

        @classmethod
        def _login(cls, db, *args, **kwargs):
            result = super()._login(db, *args, **kwargs)
            uid = result.get("uid") if isinstance(result, dict) else result
            credential = kwargs.get("credential")
            if credential is None and args:
                credential = args[0]
            login = (
                credential.get("login") if isinstance(credential, dict)
                else credential if isinstance(credential, str) else None
            )
            cls._eh_access_studio_gate_signed_in(db, uid, login)
            return result

        @classmethod
        def _eh_access_studio_gate_signed_in(cls, db, uid, login):
            """Apply the sign-in gate on a cursor of our own.

            Core closes the cursor it used before returning, so the gate
            opens one for the user it just authenticated.
            """
            import odoo
            try:
                registry = odoo.registry(db)
            except Exception:
                _logger.exception("Access Studio: sign-in gate could not open %s", db)
                return
            with contextlib.closing(registry.cursor()) as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                if uid:
                    # The date window is read in the SIGNING-IN user's
                    # timezone, so both copies of the gate agree about a
                    # dated profile at a day boundary.
                    env = env(user=uid, su=True)
                users = env["res.users"]
                user = users.browse(uid) if uid else (
                    users.search([("login", "=", login)], limit=1) if login
                    else users.browse()
                )
                if user:
                    user._eh_access_studio_apply_login_gate(login)

    else:

        def _login(self, credential, user_agent_env):
            result = super()._login(credential, user_agent_env)
            login = credential.get("login") if isinstance(credential, dict) else None
            uid = result.get("uid") if isinstance(result, dict) else None
            user = self.browse(uid) if uid else self.search(
                [("login", "=", login)], limit=1,
            ) if login else self.browse()
            if user:
                user.sudo()._eh_access_studio_apply_login_gate(login)
            return result

    def _eh_access_studio_apply_login_gate(self, login=None):
        try:
            self.sudo()._eh_access_studio_check_login_disabled()
        except AccessDenied:
            raise
        except Exception:
            _logger.exception(
                "Access Studio: sign-in gate failed for %s", login or self.login,
            )
            self.sudo()._eh_access_studio_login_failure_posture()

    def _check_credentials(self, credential, user_agent_env):
        # Run the standard credential check first.
        result = super()._check_credentials(credential, user_agent_env)
        try:
            self._eh_access_studio_check_login_disabled()
        except AccessDenied:
            raise
        except Exception:
            _logger.exception(
                "Access Studio: sign-in gate failed for %s", self.login,
            )
            self._eh_access_studio_login_failure_posture()
        return result

    # ---- API sign-in that core has already cached ---------------------
    #
    # Core caches a successful password check on (uid, password):
    # res.users.check on Odoo 16 to 18, _check_uid_passwd on 19. A script
    # that authenticated yesterday therefore never reaches
    # _check_credentials again, and a sign-in block that begins later,
    # on a dated profile, never saw it. The date-keyed, cached block
    # lookup is asked on every call instead, which costs no query.

    def _check_uid_passwd(self, uid, passwd):
        result = super()._check_uid_passwd(uid, passwd)
        self._eh_access_studio_api_gate(uid)
        return result

    if _EH_LOGIN_IS_CLASSMETHOD:

        @classmethod
        def check(cls, db, uid, passwd):
            result = super().check(db, uid, passwd)
            if _eh_login_block_possible(db, cls):
                with contextlib.closing(cls.pool.cursor()) as cr:
                    api.Environment(cr, SUPERUSER_ID, {})["res.users"]._eh_access_studio_api_gate(uid)
            return result

    def _eh_access_studio_api_gate(self, uid):
        if "eh.access.profile" not in self.env:
            return
        user = self.env["res.users"].sudo().browse(uid)
        try:
            profile_model = self.env(user=uid)["eh.access.profile"].sudo()
            blocked = profile_model._eh_login_disabled_for(
                uid, profile_model._today_for_cache_key(),
            )
            if blocked:
                user._eh_access_studio_check_login_disabled()
        except AccessDenied:
            raise
        except Exception:
            _logger.exception(
                "Access Studio: API sign-in gate failed for user %s", uid,
            )
            user._eh_access_studio_login_failure_posture()

    def _eh_access_studio_login_failure_posture(self):
        """The sign-in gate itself failed. Refuse only whom it restricts.

        Refusing everyone turned one defect in this module into a
        database nobody could sign in to, administrators included. The
        same posture as the data gates applies: a user named on no
        profile is not this product's to block, and an administrator is
        always let through so the setup can be repaired. Everyone else
        is refused, as a sign-in refusal the client can show rather than
        a server error.
        """
        try:
            listed = self.env["eh.access.profile"].sudo()._eh_listed_in_any_profile(
                self.id,
            )
        except Exception:
            listed = None
        if listed is False:
            return
        try:
            if self._eh_access_studio_is_admin_user():
                return
        except Exception:
            _logger.exception(
                "Access Studio: administrator check failed for %s", self.login,
            )
        raise AccessDenied(_(
            "Sign-in could not be verified. Please try again or"
            " contact your administrator."
        ))

    def _eh_access_studio_is_admin_user(self):
        admin_groups = (
            self.env.ref("base.group_system", raise_if_not_found=False),
            self.env.ref("base.group_erp_manager", raise_if_not_found=False),
        )
        # Walk the user record's group set so we catch implied
        # membership (e.g. admin via group_system implies group_user).
        # Doing the check from the user side avoids divergence between
        # Odoo 16/17/18 ('user_ids') and Odoo 19 ('all_user_ids') naming.
        user_groups = (
            getattr(self, "all_group_ids", None)
            or getattr(self, "group_ids", None)
            or getattr(self, "groups_id", self.env["res.groups"].browse())
        )
        return any(group and group.id in user_groups.ids for group in admin_groups)

    def _eh_access_studio_check_login_disabled(self):
        """Raise AccessDenied when a profile disables sign-in.

        FAILURE POSTURE: this method does NOT swallow. An earlier
        version caught everything except AccessDenied and logged it,
        which meant any error inside the gate let the sign-in through,
        on exactly the path the gate exists to guard. An exception here
        now propagates and the sign-in fails.
        """
        if not self:
            return
        self.ensure_one()
        if "eh.access.profile" not in self.env:
            return
        # NOTE: do NOT skip on self.env.su here. Core's res.users._login
        # does `user = user.with_user(user).sudo()` before it calls
        # _check_credentials, so env.su is True for EVERY interactive
        # sign-in. Bailing out on su would make this gate dead code on
        # the one path it exists to guard. Recovery stays open through
        # the administrator exemption below.
        if self._eh_access_studio_is_admin_user():
            return
        # A user named on no profile has nothing to block: answer that
        # with one indexed query before any date or company logic runs.
        if self.env["eh.access.profile"].sudo()._eh_listed_in_any_profile(self.id) is False:
            return
        # The stored user timezone, not the request context's, for the
        # same reason as everywhere else: the context is caller supplied.
        today = self.env["eh.access.profile"].sudo()._today_for_cache_key()
        blocking = self.env["eh.access.profile"].sudo().search([
            ("disable_login", "=", True),
            ("active", "=", True),
            ("user_ids", "in", self.id),
            "|", ("date_from", "=", False),
            ("date_from", "<=", today),
            "|", ("date_until", "=", False),
            ("date_until", ">=", today),
        ], limit=1)
        if blocking:
            _logger.info(
                "Access Studio: login denied for %s due to profile %s",
                self.login, blocking.name,
            )
            self._eh_access_studio_log_denied_login(blocking.id)
            raise AccessDenied(_(
                "Sign-in is disabled for this account by an Access"
                " Studio profile."
            ))

    def _eh_access_studio_log_denied_login(self, profile_id):
        """Record the denied sign-in on the profile chatter, durably.

        The AccessDenied raised straight after this call unwinds the
        request transaction, so a message_post on the request cursor
        would be rolled back and the audit trail would be empty exactly
        when it matters. We therefore write the audit line on an
        independent cursor and commit it before the denial propagates.
        """
        login = self.login
        try:
            with self.env.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                profile = env["eh.access.profile"].browse(profile_id)
                if profile.exists():
                    profile.message_post(body=_(
                        "Sign-in denied for %(user)s by this profile.",
                        user=login,
                    ))
        except Exception:
            # Audit failure must not turn a denial into a 500.
            _logger.exception(
                "Access Studio: failed to post login-denial audit"
                " message on profile %s",
                profile_id,
            )


    def _eh_access_studio_check_own_preferences(self, vals):
        """Field locks on a user's own preferences, checked as that user.

        Through Odoo 19, core writes a user's own preferences AS SUPERUSER
        once every key is on the self-writeable list; every ordinary gate
        stands aside for the superuser. Odoo 20 keeps native access checks,
        with a private personal-settings scope established in write().
        Contact fields such as
        email and phone live on the linked contact and are written
        through it in the same superuser step, so a lock on the contact
        is checked here as well.
        """
        self._eh_access_studio_check_field_writes(dict(vals), "write")
        # Every related field the payload names is written on the record
        # at the end of its path, in the same superuser step: the linked
        # contact through delegation (email, phone), and, with HR, the
        # linked employee (private address, work phone and the rest of
        # the self-editable employee fields). Each target answers to its
        # own model's locks, and to Read-Only Mode.
        targets = {}
        for name, value in vals.items():
            field = self._fields.get(name)
            related = getattr(field, "related", None) if field is not None else None
            if not related:
                continue
            path = related.split(".") if isinstance(related, str) else list(related)
            if len(path) < 2:
                continue
            record = self.sudo()
            try:
                for segment in path[:-1]:
                    record = record[segment]
            except (KeyError, AttributeError):
                continue
            if not record:
                continue
            key = (record._name, tuple(record.ids))
            targets.setdefault(key, {})[path[-1]] = value
        for (model_name, ids), target_vals in targets.items():
            target = self.env[model_name].browse(ids)
            target._eh_access_studio_check_field_writes(target_vals, "write")
            # Read-Only Mode keeps the user's own settings (language,
            # timezone, picture) editable, even though they are stored on
            # the contact, but the rest of the record is a business
            # record like any other.
            if set(target_vals) - _EH_PERSONAL_SETTINGS:
                reason = target._eh_access_studio_operation_blocked("write")
                if reason:
                    raise target._eh_access_studio_make_access_error("write", reason)


class ResUsersApikeys(models.Model):
    """Bearer tokens and API keys authenticate WITHOUT going through
    _login, so the sign-in block has to sit here as well, or a disabled
    user keeps full access to /json/2 and XML-RPC with a key they
    generated earlier."""

    _inherit = "res.users.apikeys"

    def _check_credentials(self, *, scope, key):
        uid = super()._check_credentials(scope=scope, key=key)
        if uid:
            user = self.env["res.users"].sudo().browse(uid)
            if user.exists():
                user._eh_access_studio_check_login_disabled()
        return uid


class ResPartnerPersonalSettings(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        scope = _eh_personal_settings_scope(self.env)
        writing = self
        if scope and (self.ids != [scope[1]] or not vals or set(vals) - _EH_PERSONAL_SETTINGS):
            writing = _eh_without_personal_settings(self)
        return super(ResPartnerPersonalSettings, writing).write(vals)

    def _eh_readonly_applies(self):
        scope = _eh_personal_settings_scope(self.env)
        # Native 20 computes inherited access on an empty parent model.
        # Actual writes above require the exact contact and safe fields.
        if scope and (not self or self.ids == [scope[1]]):
            return False
        return super()._eh_readonly_applies()


if release.version_info[0] >= 20:
    class IrAccessPersonalSettings(models.Model):
        _inherit = "ir.access"

        def _get_access_context(self):
            yield from super()._get_access_context()
            # Core caches a child's native domain including the inherited
            # parent's domain. A settings check must never populate the
            # ordinary access cache with its scoped read-only exception.
            yield _eh_personal_settings_scope(self.env)
