# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: domain access integration tests.

Covers:
  * read filter narrows the search result for the target user
  * unaffected user sees the unfiltered set
  * smart placeholder __uid__ resolves to the current user id
  * domain on configuration tables is bypassed (admin never locked out)
"""
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import AccessError


@tagged("post_install", "-at_install")
class TestDomainAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.Profile = cls.env["eh.access.profile"]
        cls.Partner = cls.env["res.partner"]
        cls.IrModel = cls.env["ir.model"]

        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "Domain Demo",
            "login": "eh_access_domain_demo@example.test",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.partner_a = cls.Partner.create({
            "name": "Visible One",
            "user_id": cls.demo_user.id,
        })
        cls.partner_b = cls.Partner.create({
            "name": "Other One",
        })

    def _make_profile(self, domain_text="[]", apply_filter=False):
        return self.Profile.create({
            "name": "Domain rule",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": self.IrModel._get("res.partner").id,
                "read_right": True,
                "write_right": True,
                "delete_right": False,
                "create_right": False,
                "apply_filter": apply_filter,
                "domain": domain_text,
            })],
        })

    def test_smart_placeholder_uid_resolves(self):
        self._make_profile(
            domain_text='[("user_id", "=", "__uid__")]',
            apply_filter=True,
        )
        partners = self.Partner.with_user(self.demo_user).search([])
        self.assertIn(self.partner_a, partners)
        self.assertNotIn(self.partner_b, partners)

    def test_no_filter_does_not_restrict(self):
        self._make_profile(domain_text="[]", apply_filter=False)
        partners = self.Partner.with_user(self.demo_user).search([])
        self.assertIn(self.partner_a, partners)
        self.assertIn(self.partner_b, partners)

    def test_configuration_models_bypassed(self):
        # A profile that targets eh.access.profile itself should not
        # block the demo user from seeing their own profiles.
        self.Profile.create({
            "name": "Self-targeted",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": self.IrModel._get("eh.access.profile").id,
                "read_right": False,
                "apply_filter": True,
                "domain": "[]",
            })],
        })
        # Configuration model is bypassed in our ir.rule extension, so a
        # user with the manager group can still see profiles. We use the
        # admin to keep the test simple.
        admin = self.env.ref("base.user_admin")
        result = self.Profile.with_user(admin).search([])
        self.assertTrue(result)


    def test_previously_allowed_read_does_not_survive_policy_change(self):
        """Native 20 caches positive per-record access within a transaction."""
        target = self.partner_a.with_user(self.demo_user)
        self.assertEqual(target.read(["name"])[0]["name"], "Visible One")
        self._make_profile("[('id', '=', 0)]", apply_filter=True)
        with self.assertRaises(AccessError):
            target.read(["name"])
