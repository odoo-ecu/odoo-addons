# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: profile-template wizard tests.

Covers the four scenarios:
  * read_only          flips the readonly flag
  * own_records_only   stamps a domain rule with __uid__
  * vendor_portal      sets the global toggles
  * auditor            sets readonly + date_until in the future
"""
from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import AccessError, UserError


@tagged("post_install", "-at_install")
class TestTemplateWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.Profile = cls.env["eh.access.profile"].with_context(active_test=False)
        cls.Wizard = cls.env["eh.access.template.wizard"]
        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "Template Demo",
            "login": "eh_access_template@example.test",
        })

    def test_read_only_template(self):
        wiz = self.Wizard.create({
            "template": "read_only",
            "name": "Read-only viewer",
            "user_ids": [(6, 0, [self.demo_user.id])],
        })
        wiz.action_create_profile()
        profile = self.Profile.search([("name", "=", "Read-only viewer")], limit=1)
        self.assertTrue(profile)
        self.assertTrue(profile.readonly)
        self.assertFalse(profile.active, "Templates must not activate policy before review")
        self.assertIn(self.demo_user, profile.user_ids)

    def test_own_records_template(self):
        wiz = self.Wizard.create({
            "template": "own_records_only",
            "name": "Own sales orders",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "target_model": "res.partner",
        })
        wiz.action_create_profile()
        profile = self.Profile.search([("name", "=", "Own sales orders")], limit=1)
        self.assertTrue(profile)
        self.assertTrue(profile.hide_export)
        self.assertEqual(len(profile.domain_line_ids), 1)
        line = profile.domain_line_ids
        self.assertEqual(line.model_name, "res.partner")
        self.assertTrue(line.apply_filter)
        self.assertIn("__uid__", line.domain)

    def test_auditor_template_sets_date_until(self):
        wiz = self.Wizard.create({
            "template": "auditor",
            "name": "Q4 auditor",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "duration_days": 14,
        })
        wiz.action_create_profile()
        profile = self.Profile.search([("name", "=", "Q4 auditor")], limit=1)
        self.assertTrue(profile)
        self.assertTrue(profile.readonly)
        self.assertTrue(profile.disable_debug_mode)
        expected_until = date.today() + timedelta(days=14)
        self.assertEqual(profile.date_until, expected_until)

    def test_unsupported_owner_model_is_rejected_before_profile_creation(self):
        wizard = self.Wizard.create({"template": "own_records_only", "name": "Invalid owner field", "target_model": "res.company"})
        with self.assertRaises(UserError):
            wizard.action_create_profile()
        self.assertFalse(self.Profile.search([("name", "=", "Invalid owner field")]))

    def test_friendly_model_selection_updates_technical_target(self):
        wizard = self.Wizard.create({"template": "own_records_only", "name": "Friendly model", "target_model_id": self.env["ir.model"]._get_id("res.partner")})
        self.assertEqual(wizard.target_model, "res.partner")
        action = wizard.action_create_profile()
        self.assertFalse(self.Profile.browse(action["res_id"]).active)

    def test_template_action_requires_manager(self):
        wizard = self.Wizard.create({"template": "read_only", "name": "Not authorized"})
        with self.assertRaises(AccessError):
            wizard.with_user(self.demo_user).action_create_profile()

    def test_vendor_portal_template(self):
        wiz = self.Wizard.create({
            "template": "vendor_portal",
            "name": "Contractor view",
            "user_ids": [(6, 0, [self.demo_user.id])],
        })
        wiz.action_create_profile()
        profile = self.Profile.search([("name", "=", "Contractor view")], limit=1)
        self.assertTrue(profile)
        self.assertTrue(profile.hide_export)
        self.assertTrue(profile.hide_import)
        self.assertTrue(profile.disable_debug_mode)
