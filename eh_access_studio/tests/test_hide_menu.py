# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: hide-menu integration tests.

Covers:
  * the loaded menu tree still contains the target menu for an
    unaffected user
  * the loaded menu tree drops the target menu for an affected user
  * the configuration menu is never hidden, even if listed
  * one profiled user does not strip menus from a colleague who shares
    the same group set (the cross-user cache leak regression)

Note the assertions go through load_menus, not search_fetch. Filtering
at search_fetch put our result inside core's _visible_menu_ids ormcache,
which is keyed on the group set and NOT on the user, so it leaked across
users. load_menus is keyed per user and is the supported path.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestHideMenu(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.demo_user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Menu Demo",
            "login": "eh_access_menu_demo@example.test",
            # Both users carry the SAME group set on purpose: the point
            # of the leak regression is that identical groups must not
            # mean a shared, pruned menu tree. group_system is what
            # makes base.menu_administration visible at all.
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("base.group_system").id,
            ])],
        })
        cls.unaffected_user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Unaffected",
            "login": "eh_access_unaffected@example.test",
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("base.group_system").id,
            ])],
        })
        cls.target_menu = cls.env.ref("base.menu_administration")
        cls.profile = cls.env["eh.access.profile"].create({
            "name": "Hide Settings",
            "user_ids": [(6, 0, [cls.demo_user.id])],
            "hidden_menu_ids": [(6, 0, [cls.target_menu.id])],
        })

    def _menu_ids_for(self, user, clear_cache=True):
        # The cache clear is opt-out on purpose: the cross-user leak
        # test must read BOTH users without clearing in between, since
        # a shared cache entry is exactly the thing it is guarding
        # against. Clearing every time would make that test unable to
        # fail.
        if clear_cache:
            self.env.registry.clear_cache("default", "templates")
        Menu = self.env["ir.ui.menu"].with_user(user)
        menus = Menu.load_menus(False)
        return {key for key in menus.keys() if isinstance(key, int)}

    def test_unaffected_user_still_sees_target_menu(self):
        self.assertIn(self.target_menu.id, self._menu_ids_for(self.unaffected_user))

    def test_affected_user_loses_target_menu(self):
        self.assertNotIn(self.target_menu.id, self._menu_ids_for(self.demo_user))

    def test_no_cross_user_leak_for_same_group_set(self):
        """A colleague with identical groups keeps their menus.

        Regression guard: both users hold exactly base.group_user, so a
        cache keyed on the group set rather than the user would serve
        the profiled user's pruned tree to the unaffected one.
        """
        self.assertNotIn(
            self.target_menu.id, self._menu_ids_for(self.demo_user),
        )
        self.assertIn(
            self.target_menu.id,
            self._menu_ids_for(self.unaffected_user, clear_cache=False),
            "the colleague must keep their menus even when the profiled"
            " user populated the cache first",
        )

    def test_configuration_menu_always_visible(self):
        own_menu = self.env.ref("eh_access_studio.menu_eh_access_root")
        self.profile.write({
            "hidden_menu_ids": [(6, 0, [own_menu.id])],
        })
        # Demo user is not in the manager group, so they should not see
        # the menu via group ACL anyway. The test is really about admins
        # never being locked out, so we re-run with a manager.
        manager = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Manager",
            "login": "eh_access_manager_demo@example.test",
            "group_ids": [
                (6, 0, [
                    self.env.ref("base.group_user").id,
                    self.env.ref("eh_access_studio.group_eh_access_manager").id,
                ]),
            ],
        })
        self.profile.write({
            "user_ids": [(6, 0, [manager.id])],
        })
        self.assertIn(own_menu.id, self._menu_ids_for(manager))
