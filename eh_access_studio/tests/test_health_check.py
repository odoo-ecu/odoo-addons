# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: health-check tests.

Covers:
  * clean install reports success
  * orphan profile (no users) flagged
  * no-op profile (no rules) flagged
  * expired-but-active profile flagged
  * malformed domain rule flagged
"""
from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestHealthCheck(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.Profile = cls.env["eh.access.profile"]
        cls.IrModel = cls.env["ir.model"]
        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "Health Demo",
            "login": "eh_access_health@example.test",
        })
        # Wipe demo data noise
        cls.Profile.search([]).unlink()

    def test_clean_install_passes(self):
        action = self.Profile.action_health_check()
        self.assertEqual(action["params"]["type"], "success")

    def test_orphan_profile_flagged(self):
        self.Profile.create({
            "name": "Orphan",
            "user_ids": [(6, 0, [])],
            "hide_export": True,
        })
        action = self.Profile.action_health_check()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("Orphan", action["params"]["message"])

    def test_no_op_profile_flagged(self):
        self.Profile.create({
            "name": "No-op",
            "user_ids": [(6, 0, [self.demo_user.id])],
        })
        action = self.Profile.action_health_check()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("No-op", action["params"]["message"])

    def test_expired_active_profile_flagged(self):
        yesterday = date.today() - timedelta(days=1)
        self.Profile.create({
            "name": "Expired survivor",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "hide_export": True,
            "date_until": yesterday,
        })
        action = self.Profile.action_health_check()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("Expired survivor", action["params"]["message"])

    def test_malformed_domain_rule_flagged(self):
        partner_model = self.IrModel._get("res.partner")
        profile = self.Profile.create({
            "name": "Bad domain",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": partner_model.id,
                "read_right": True,
                "apply_filter": True,
                "domain": '[("user_id", "=", "__uid__")]',
            })],
        })
        # Bypass the constraint by writing the malformed value via SQL.
        rule_id = profile.domain_line_ids.id
        self.env.cr.execute(
            "UPDATE eh_access_domain SET domain=%s WHERE id=%s",
            ("[(garbage", rule_id),
        )
        # Invalidate the field cache so the next read fetches the
        # corrupt value.
        profile.domain_line_ids.invalidate_recordset(["domain"])
        action = self.Profile.action_health_check()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("Bad domain", action["params"]["message"])
