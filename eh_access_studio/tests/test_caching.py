# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: cache invalidation tests.

Covers:
  * model-line cache miss → DB lookup → cache hit
  * cache invalidates when an eh.access.model line is created
  * cache invalidates when a profile is deactivated
  * chatter visibility cache returns the expected tuple shape
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCaching(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.Profile = cls.env["eh.access.profile"]
        cls.IrModel = cls.env["ir.model"]
        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "Cache Test",
            "login": "eh_access_cache@example.test",
        })
        cls.partner_model = cls.IrModel._get("res.partner")

    def _today(self):
        return self.Profile._today_for_cache_key()

    def test_model_line_cache_invalidates_on_line_create(self):
        profile = self.Profile.create({
            "name": "Cache test",
            "user_ids": [(6, 0, [self.demo_user.id])],
        })
        # Initial lookup: no model lines → empty tuple.
        ids_a = self.Profile._model_line_ids_for(
            self.demo_user.id, self.env.company.id,
            self._today(), "res.partner",
        )
        self.assertEqual(ids_a, ())

        # Add a model line. The mixin should invalidate the cache.
        self.env["eh.access.model"].create({
            "profile_id": profile.id,
            "model_id": self.partner_model.id,
            "restrict_create": True,
        })
        ids_b = self.Profile._model_line_ids_for(
            self.demo_user.id, self.env.company.id,
            self._today(), "res.partner",
        )
        self.assertEqual(len(ids_b), 1)

    def test_model_line_cache_invalidates_on_profile_deactivate(self):
        profile = self.Profile.create({
            "name": "Cache deactivate",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "restrict_create": True,
            })],
        })
        ids_a = self.Profile._model_line_ids_for(
            self.demo_user.id, self.env.company.id,
            self._today(), "res.partner",
        )
        self.assertEqual(len(ids_a), 1)
        profile.active = False
        ids_b = self.Profile._model_line_ids_for(
            self.demo_user.id, self.env.company.id,
            self._today(), "res.partner",
        )
        self.assertEqual(ids_b, ())

    def test_chatter_visibility_returns_three_tuple(self):
        result = self.Profile._chatter_visibility_for(
            self.demo_user.id, self.env.company.id,
            self._today(), "res.partner",
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        for item in result:
            self.assertIsInstance(item, bool)

    def test_chatter_visibility_reflects_global_toggle(self):
        self.Profile.create({
            "name": "Hide everywhere",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "hide_chatter": True,
        })
        send, log, schedule = self.Profile._chatter_visibility_for(
            self.demo_user.id, self.env.company.id,
            self._today(), "res.partner",
        )
        self.assertTrue(send)
        self.assertTrue(log)
        self.assertTrue(schedule)
