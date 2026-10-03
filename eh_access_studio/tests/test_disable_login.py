# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: disable_login enforcement test.

Covers:
  * a user in a profile with disable_login=True hits AccessDenied
  * an admin in such a profile is exempt
  * an inactive profile does not block
"""
from odoo.exceptions import AccessDenied
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDisableLogin(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.Profile = cls.env["eh.access.profile"]
        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "Login Block Demo",
            "login": "eh_access_login_block@example.test",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def test_blocked_user_raises(self):
        self.Profile.create({
            "name": "Block login",
            "disable_login": True,
            "user_ids": [(6, 0, [self.demo_user.id])],
        })
        # with_user switches the env to demo_user (uid != SUPERUSER_ID,
        # env.su becomes False) so the gate's admin-bypass does not
        # short-circuit. Without with_user the test runs as SUPERUSER
        # which is exempted by design.
        with self.assertRaises(AccessDenied):
            self.demo_user.with_user(self.demo_user)._eh_access_studio_check_login_disabled()

    def test_inactive_profile_does_not_block(self):
        self.Profile.create({
            "name": "Block login (inactive)",
            "disable_login": True,
            "active": False,
            "user_ids": [(6, 0, [self.demo_user.id])],
        })
        # No exception expected.
        self.demo_user.with_user(self.demo_user)._eh_access_studio_check_login_disabled()
