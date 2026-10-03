# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: conflict report + diagnostic action tests.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestConflictAndDiagnostic(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.Profile = cls.env["eh.access.profile"]
        cls.IrModel = cls.env["ir.model"]
        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "Conflict Demo",
            "login": "eh_access_conflict_demo@example.test",
        })
        cls.partner_model = cls.IrModel._get("res.partner")

    def test_no_conflict_when_only_one_profile(self):
        self.Profile.create({
            "name": "Solo",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "read_right": True,
                "apply_filter": False,
            })],
        })
        action = self.Profile.action_conflict_report()
        self.assertEqual(action["params"]["type"], "info")

    def test_conflict_detected_across_two_profiles(self):
        self.Profile.create({
            "name": "Strict",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "read_right": True,
                "apply_filter": True,
                "domain": '[("user_id", "=", "__uid__")]',
            })],
        })
        self.Profile.create({
            "name": "Loose",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "read_right": True,
                "create_right": True,
                "write_right": True,
                "apply_filter": False,
                "domain": "[]",
            })],
        })
        action = self.Profile.action_conflict_report()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("res.partner", action["params"]["message"])

    def test_diagnostic_summary_lists_rules(self):
        comment_field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"),
            ("name", "=", "comment"),
        ], limit=1)
        self.assertTrue(
            comment_field,
            "res.partner.comment field should exist in base for this test",
        )
        profile = self.Profile.create({
            "name": "Showcase",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "hide_export": True,
            "hide_chatter": True,
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "field_ids": [(6, 0, [comment_field.id])],
                "invisible": True,
            })],
        })
        action = profile.action_diagnostic()
        message = action["params"]["message"]
        self.assertIn("Showcase", message)
        self.assertIn("Export", message)
        self.assertIn("Chatter", message)

    def test_diagnostic_no_op_message(self):
        profile = self.Profile.create({
            "name": "Empty",
            "user_ids": [(6, 0, [self.demo_user.id])],
        })
        action = profile.action_diagnostic()
        self.assertIn("no-op", action["params"]["message"].lower())
