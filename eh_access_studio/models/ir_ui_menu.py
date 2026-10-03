# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: menu hiding.

Where this hooks in matters more than what it does.

An earlier version filtered inside ir.ui.menu.search_fetch. That is the
wrong layer: core calls search_fetch from _visible_menu_ids, which is
ormcached on ('frozenset(groups)', 'debug') and NOT on the user, so one
profiled user's hidden menus were baked into a cache entry shared by
every colleague holding the same group set. Colleagues lost menus they
were entitled to, and the leak went both ways.

The correct hook is _load_menus_blacklist, the extension point core
provides for exactly this, consumed by load_menus which IS cached per
user ('self.env.uid', 'debug', 'self.env.lang'). get_user_roots is
filtered as well so a hidden root app disappears from the apps menu,
and it rides load_menus_root, also keyed per user.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def _eh_access_studio_hidden_menu_ids(self):
        """Menu ids hidden for the current user by an active profile."""
        if "eh.access.profile" not in self.env:
            return set()
        # NOTE: no env.su bail. Core caches the menu tree per uid
        # (load_menus, load_menus_root), with no superuser dimension in
        # the key, so one sudo menu load for a profiled user would
        # otherwise poison that user's cache and un-hide everything for
        # the rest of its lifetime. The uid identifies the person either
        # way, which is what the rule is about.
        try:
            profile_model = self.env["eh.access.profile"].sudo()
            today = profile_model._today_for_cache_key()
            # Resolve across EVERY company the user is allowed into, not
            # just the active one. Core caches the menu tree on
            # ('self.env.uid', 'debug', 'self.env.lang') with no company
            # in the key (addons/base/models/ir_ui_menu.py), so a
            # company-dependent answer here would make the tree depend
            # on whichever company happened to load first. Taking the
            # union is deterministic and errs towards hiding, which is
            # the safe direction for navigation chrome. Per-company menu
            # sets for one user are a documented limitation.
            # The user's own allowed companies, not env.companies: the
            # latter follows the company switcher, and the cache key has
            # no company in it, so a request-dependent answer would make
            # the tree depend on the switcher state at first load.
            company_ids = (
                self.env.user.company_ids.ids
                or profile_model._eh_safe_company_ids()
            )
            profile_ids = set()
            for company_id in company_ids:
                profile_ids.update(profile_model._get_active_profile_ids(
                    self.env.user.id, company_id, today,
                ))
            if not profile_ids:
                return set()
            hidden_ids = set(
                profile_model.browse(sorted(profile_ids))
                .mapped("hidden_menu_ids.id")
            )
        except Exception:
            # Menus are the first thing a user sees. A lookup failure
            # must degrade to "hide nothing", never to a blank client.
            _logger.exception("Access Studio: hidden-menu lookup failed")
            return set()
        if not hidden_ids:
            return set()
        # Never hide our own configuration menu: an administrator must
        # always be able to reach the profile that is hiding things.
        own_menu = self.env.ref(
            "eh_access_studio.menu_eh_access_root", raise_if_not_found=False
        )
        if own_menu:
            hidden_ids.discard(own_menu.id)
        return hidden_ids

    def _load_menus_blacklist(self):
        result = super()._load_menus_blacklist()
        hidden_ids = self._eh_access_studio_hidden_menu_ids()
        if not hidden_ids:
            return result
        return list(result) + sorted(hidden_ids)

    @api.model
    def get_user_roots(self):
        roots = super().get_user_roots()
        hidden_ids = self._eh_access_studio_hidden_menu_ids()
        if not hidden_ids:
            return roots
        return roots.filtered(lambda menu: menu.id not in hidden_ids)
