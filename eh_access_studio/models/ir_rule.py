# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: ir.rule._compute_domain extension.

We extend Odoo's built-in record-rule machinery so the domains we
store on eh.access.domain are AND-ed onto the standard rule domain
returned for each (model, mode) pair.

Why ir.rule and not BaseModel.create / write / unlink?
* ir.rule is the canonical, well-tested gate.
* It runs once per (model, mode) and is cached by Odoo.
* It applies to search, read, write and unlink without per-record
  Python checks.
* Users hitting the gate get Odoo's standard 'access denied' dialog.

Mode handling:
* read   uses the rule's domain when read_right is True
* create gates entirely (Odoo expects ALL-or-NONE on create)
* write  uses domain when write_right is True
* unlink uses domain when delete_right is True

A profile that does not grant a given right contributes a 'block all'
domain ([(id, '=', False)]) for that mode. The OR-across-rules
combinator below means any rule that grants the right opens the gate
for the records matching that rule's filter.

Domain composition goes through the two helpers below rather than a
specific API shape. Odoo 19 exposes odoo.fields.Domain with `&` and `|`
operators; 16, 17 and 18 compose plain lists through
odoo.osv.expression. Both are supported from one source so the module
does not need a second implementation per series.
"""
import logging
from functools import reduce

from odoo import api, fields, models, release

try:  # Odoo 19+
    from odoo.fields import Domain
except ImportError:  # Odoo 16, 17, 18
    Domain = None

try:  # Odoo 18 and earlier
    if Domain is None:
        from odoo.osv import expression
    else:
        # Odoo 19 still ships odoo.osv, but importing it emits a
        # deprecation warning on every module load and the Domain API
        # above is what this series wants anyway.
        expression = None
except ImportError:
    expression = None

from .base_model import _eh_internal_state

_logger = logging.getLogger(__name__)

BLOCK_ALL = [("id", "=", False)]

# Models the chatter waiver never covers: they ARE the chatter.
_EH_WAIVER_EXCLUDED = frozenset((
    "mail.message", "mail.activity", "ir.attachment", "mail.followers",
))
PASS_ALL = []  # empty domain matches every record


def _as_domain(value):
    """Normalise a domain to this series' preferred representation."""
    if Domain is not None:
        return value if isinstance(value, Domain) else Domain(value or [])
    if value is None:
        return []
    return list(value)


def _and(left, right):
    if Domain is not None:
        return _as_domain(left) & _as_domain(right)
    if expression is not None:
        return expression.AND([_as_domain(left), _as_domain(right)])
    return _as_domain(left) + _as_domain(right)


def _or_all(domains):
    if not domains:
        return None
    if Domain is not None:
        return reduce(lambda a, b: a | b, [_as_domain(d) for d in domains])
    if expression is not None:
        return expression.OR([_as_domain(d) for d in domains])
    return _as_domain(domains[0])


class IrRule(models.Model):
    _inherit = "ir.access" if release.version_info[0] >= 20 else "ir.rule"

    @api.model
    def _compute_domain(self, model_name, mode="read"):
        # Odoo 20 merges ACLs and rules into ir.access. The native model
        # _access_domain supplies its own base domain; this method returns
        # only our additional restriction there, with identical failure
        # handling and bypass checks to earlier releases.
        base_domain = Domain.TRUE if release.version_info[0] >= 20 else super()._compute_domain(model_name, mode)

        # Skip extension when the call originates from our own resolver
        # to avoid recursion when the engine reads its own configuration
        # tables.
        #
        # SECURITY: the context arrives from the RPC payload verbatim,
        # so the key alone must never be enough to switch the gate off.
        # Every internal caller reads the configuration tables through
        # sudo(), so we additionally require env.su. A regular user who
        # sends {'eh_access_studio_bypass': True} stays gated.
        if self.env.context.get("eh_access_studio_bypass") and self.env.su:
            return base_domain

        # Record rules never apply to the superuser, but Odoo 17 still
        # asks for the domain under sudo, to decide what to flush before
        # a search. Resolving profiles there searched our own tables,
        # which flushed, which asked again: infinite recursion. Our own
        # configuration tables are never restricted either.
        if self.env.su or model_name.startswith("eh.access."):
            return base_domain

        # While the registry is still being built, reading our own
        # configuration can re-enter the rule engine through the very
        # model it is trying to evaluate (res.users on the 16 and 17
        # series), which ends in a cache miss loop during install. There
        # is no user session to restrict at that point anyway.
        if not getattr(self.env.registry, "ready", True):
            return base_domain

        # Decide the failure posture BEFORE doing any work: a user with
        # no active profile must never be affected by a bug in this
        # extension, while a user who IS under a profile must not have
        # the gate silently removed by one.
        try:
            gated = self._eh_access_studio_is_gated(model_name)
        except Exception:
            # Unknown means gated. Returning the untouched core domain
            # here would mean the most likely failure of all, the
            # profile lookup, silently removes every record rule this
            # module applies. The exemptions inside _eh_access_studio_is_gated
            # (superuser, the studio's own tables) are re-checked below
            # so an administrator can still reach the configuration.
            _logger.exception(
                "Access Studio: failed to resolve the active profile"
                " set for %s/%s, treating the user as gated",
                model_name, mode,
            )
            if self.env.su or model_name.startswith("eh.access."):
                return base_domain
            # A user named on no profile at all (portal, public, anyone
            # the administrator never restricted) is not this product's
            # to gate, and one failed lookup must not lock them out.
            listed = self.env["eh.access.profile"].sudo()._eh_listed_in_any_profile(
                self.env.uid,
            )
            if listed is False:
                return base_domain
            if self._eh_access_studio_is_administrator():
                # Failing closed must never leave the database with no
                # way back through the interface.
                return base_domain
            gated = True

        try:
            extra = self._eh_access_studio_domain(model_name, mode)
        except Exception:
            _logger.exception(
                "Access Studio: failed to compute domain for %s/%s",
                model_name, mode,
            )
            if not gated:
                return base_domain
            if self._eh_access_studio_is_administrator():
                # Failing closed must never leave the database with no
                # way back through the interface. The comment here used
                # to claim the exemption inside _eh_access_studio_is_gated
                # covered this branch; it does not, because an
                # administrator NAMED on a profile is gated like anyone
                # else, so the exemption is applied here too.
                return base_domain
            # FAIL CLOSED. This is a security gate, so a crash inside it
            # blocks the model for this request instead of quietly
            # handing back the unrestricted core domain.
            return _and(base_domain, BLOCK_ALL)

        if extra is None:
            return base_domain

        return _and(base_domain, extra)

    def _eh_access_studio_is_administrator(self):
        """True for the users who must always be able to repair this."""
        try:
            for xmlid in ("base.group_system", "base.group_erp_manager"):
                group = self.env.ref(xmlid, raise_if_not_found=False)
                if group and self.env.user.id in self._eh_group_member_ids(group):
                    return True
        except Exception:
            _logger.exception(
                "Access Studio: could not determine administrator status",
            )
        return False

    @staticmethod
    def _eh_group_member_ids(group):
        # sudo: a user may not be allowed to read group membership, and
        # an exemption that cannot read its own list exempts nobody.
        group = group.sudo()
        for candidate in ("all_user_ids", "users", "user_ids"):
            if candidate in group._fields:
                return group[candidate].ids
        return []

    def _eh_access_studio_is_gated(self, model_name):
        """True when this (user, model) pair is under an active profile.

        Used to pick the failure posture in _compute_domain: fail closed
        for a user the product is actually restricting, fail open for
        everyone else so a bug here cannot take a database down.
        """
        if "eh.access.profile" not in self.env:
            return False
        if model_name.startswith("eh.access."):
            return False
        if self.env.su:
            return False
        profile_model = self.env["eh.access.profile"].sudo()
        return bool(profile_model._eh_active_profile_ids_any_company())

    def _eh_access_studio_is_waived(self, model_name, mode):
        """True while this module is deliberately checking WRITE access
        for something that is not a data change: posting to a chatter or
        scheduling an activity. The document's own record rules would
        otherwise refuse both on a record the profile makes read only,
        which is the opposite of the documented behaviour. A real write
        to that model is never waived (see base_model._eh_data_change).
        """
        if mode != "write":
            return False
        if not getattr(_eh_internal_state, "write_op_off", False):
            return False
        if model_name in _EH_WAIVER_EXCLUDED:
            # The waiver exists for the DOCUMENT's write check. The
            # chatter's own models keep their rules: core checks nothing
            # else before letting somebody edit or delete an activity.
            return False
        return model_name not in (getattr(_eh_internal_state, "changing", None) or ())

    def _eh_access_studio_domain(self, model_name, mode):
        """Return the AND-extension to apply for the current user.

        Return value:
          * None   ... no extra restriction (most permissive)
          * Domain ... restrict to the records matching this domain
        """
        if "eh.access.profile" not in self.env:
            return None
        if self.env.su or model_name.startswith("eh.access."):
            return None
        if self._eh_access_studio_is_waived(model_name, mode):
            return None
        profile_model = self.env["eh.access.profile"].sudo()
        # Every company the user may enter, not just the active one: the
        # company switcher is user controlled, so resolving against the
        # active company alone let a user step out of a company-scoped
        # profile by switching company.
        profile_ids = profile_model._eh_active_profile_ids_any_company()
        if not profile_ids:
            return None

        # Skip our own configuration tables so admins don't lock
        # themselves out via a rule that targets eh.access.* itself.
        if model_name.startswith("eh.access."):
            return None

        rule_ids = profile_model._eh_domain_rule_ids_for(
            tuple(profile_ids), model_name,
        )
        if not rule_ids:
            return None
        rules = self.env["eh.access.domain"].sudo().with_context(
            eh_access_studio_bypass=True,
        ).browse(rule_ids).exists()
        if not rules:
            return None

        right_field = {
            "read": "read_right",
            "create": "create_right",
            "write": "write_right",
            "unlink": "delete_right",
        }.get(mode)
        if not right_field:
            return None

        # OR across rules: a user passes through if ANY rule grants the
        # right and the record matches that rule's filter. A rule that
        # does NOT grant the right contributes BLOCK_ALL for the OR;
        # a rule that grants the right with no filter contributes
        # PASS_ALL, which when OR-ed makes the whole result
        # unrestricted. We short-circuit to None in that case.
        # The same forge-proof date the profile window uses, not
        # context_today, whose timezone the caller controls.
        today = fields.Date.from_string(
            self.env["eh.access.profile"].sudo()._today_for_cache_key()
        )
        leaves = []
        for rule in rules:
            # Soft restriction: the records stay visible, the filter
            # only decides what may be changed. So on a read THIS rule
            # contributes nothing, and on write or unlink it contributes
            # its filter as usual.
            #
            # `continue`, not `return None`: returning would drop every
            # OTHER rule on the model as well, so one soft rule would
            # quietly unlock reading for all of them.
            if rule.soft_restrict and mode == "read" and rule.read_right:
                # Only a rule that GRANTS read may abstain on a read.
                # Skipping one that grants nothing removed its block
                # from the OR, and an access level of No access then
                # read as no restriction at all: full disclosure.
                continue
            if getattr(rule, right_field):
                resolved = rule._resolved_domain(today=today)
                if not resolved:
                    # PASS_ALL anywhere collapses the OR to no
                    # restriction.
                    return None
                leaves.append(resolved)
            else:
                leaves.append(BLOCK_ALL)

        if not leaves:
            return None

        return _or_all(leaves)
