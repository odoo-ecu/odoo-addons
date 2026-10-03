# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestAccessInspector(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Inspector subject", "login": "eh_inspector_subject@example.test",
            "email": "eh_inspector_subject@example.test", "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id, cls.env.ref("base.group_partner_manager").id])],
        })
        cls.model = cls.env["ir.model"]._get("res.partner")
        cls.own = cls.env["res.partner"].create({"name": "Inspector owned", "user_id": cls.user.id})
        cls.other = cls.env["res.partner"].create({"name": "Inspector other"})
        cls.Profile = cls.env["eh.access.profile"]

    def _wizard(self, **values):
        vals = {"user_id": self.user.id, "model_id": self.model.id,
                "company_id": self.env.company.id, "record_id": self.own.id, "operation": "read"}
        vals.update(values)
        return self.env["eh.access.inspector"].create(vals)

    def _own_profile(self):
        return self.Profile.create({"name": "Inspector own records", "user_ids": [(6, 0, self.user.ids)],
            "domain_line_ids": [(0, 0, {"model_id": self.model.id,
                "read_right": True, "write_right": True, "create_right": False, "delete_right": False,
                "apply_filter": True, "domain": '[("user_id", "=", "__uid__")]'})]})

    def test_record_result_agrees_with_runtime(self):
        self._own_profile()
        allowed = self._wizard()
        denied = self._wizard(record_id=self.other.id)
        allowed.action_inspect()
        denied.action_inspect()
        self.assertEqual(allowed.decision, "allowed")
        self.assertEqual(denied.decision, "denied")
        def check_read(record):
            if hasattr(record, "check_access"):
                record.check_access("read")
            else:
                record.check_access_rights("read")
                record.check_access_rule("read")
        check_read(self.own.with_user(self.user))
        with self.assertRaises(AccessError):
            check_read(self.other.with_user(self.user))
        self.assertIn("Inspector own records", allowed.result_html)
        self.assertIn("Combined record filter", allowed.result_html)

    def test_readonly_edit_denied_without_business_write(self):
        self.Profile.create({"name": "Inspector no edits", "readonly": True,
                             "user_ids": [(6, 0, self.user.ids)]})
        wizard = self._wizard(operation="write")
        with patch.object(type(self.own), "write", side_effect=AssertionError("Inspector attempted a business write")):
            wizard.action_inspect()
        self.assertEqual(wizard.decision, "denied")
        self.assertEqual(self.own.name, "Inspector owned")

    def test_create_is_partial_and_does_not_create_business_records(self):
        wizard = self._wizard(operation="create", record_id=False)
        with patch.object(type(self.own), "create", side_effect=AssertionError("Inspector attempted business creation")):
            wizard.action_inspect()
        self.assertEqual(wizard.decision, "conditional")
        self.assertIn("Create values", wizard.result_html)

    def test_without_sample_never_claims_record_access(self):
        wizard = self._wizard(record_id=False)
        wizard.action_inspect()
        self.assertEqual(wizard.decision, "conditional")

    def test_native_acl_denial(self):
        wizard = self._wizard(model_id=self.env["ir.model"]._get_id("ir.config_parameter"), record_id=False)
        wizard.action_inspect()
        self.assertEqual(wizard.decision, "denied")

    def test_nonmanager_direct_invocation_is_denied(self):
        wizard = self._wizard()
        with self.assertRaises(AccessError):
            wizard.with_user(self.user).action_inspect()

    def test_company_not_allowed_to_subject_is_rejected(self):
        other_company = self.env["res.company"].create({"name": "Inspector forbidden company"})
        wizard = self._wizard(company_id=other_company.id)
        with self.assertRaises(UserError):
            wizard.action_inspect()

    @mute_logger("odoo.addons.eh_access_studio.wizards.eh_access_inspector")
    def test_failed_evidence_does_not_claim_complete_access(self):
        wizard = self._wizard()
        with patch.object(type(wizard), "_evidence", side_effect=ValueError("Unavailable contribution")):
            wizard.action_inspect()
        self.assertEqual(wizard.decision, "unknown")

    def test_inactive_profile_not_in_effective_result(self):
        self.Profile.create({"name": "Unpublished inspector draft", "active": False,
                             "readonly": True, "user_ids": [(6, 0, self.user.ids)]})
        wizard = self._wizard(operation="write")
        wizard.action_inspect()
        self.assertEqual(wizard.decision, "allowed")
        self.assertNotIn("Unpublished inspector draft", wizard.result_html)


@tagged("post_install", "-at_install")
class TestAccessElementPicker(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.profile = cls.env["eh.access.profile"].create({"name": "Picker draft", "active": False})
        cls.model = cls.env["ir.model"]._get("res.partner")
        cls.view = cls.env["ir.ui.view"].create({"name": "Inspector element fixture", "model": "res.partner", "type": "form",
            "arch_db": '<form><header><button name="action_archive" type="object" string="Archive fixture"/></header><sheet><field name="name"/></sheet></form>'})

    def _wizard(self):
        return self.env["eh.access.element.wizard"].create({"profile_id": self.profile.id, "model_id": self.model.id, "kind": "button"})

    def test_discovers_real_elements_and_creates_idempotent_rule(self):
        wizard = self._wizard()
        wizard.action_discover()
        choice = wizard.choice_ids.filtered(lambda line: line.target_name == "action_archive")
        self.assertTrue(choice)
        self.assertIn(self.view.name, choice.view_names)
        wizard.choice_id = choice
        wizard.action_apply()
        wizard.action_apply()
        lines = self.profile.node_line_ids.filtered(lambda line: line.target_name == "action_archive")
        self.assertEqual(len(lines), 1)
        self.assertFalse(self.profile.active)

    def test_choice_from_another_wizard_is_refused(self):
        wizard, other = self._wizard(), self._wizard()
        other.action_discover()
        wizard.choice_id = other.choice_ids[:1]
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_active_profile_requires_review(self):
        wizard = self._wizard()
        wizard.action_discover()
        wizard.choice_id = wizard.choice_ids[:1]
        self.profile.active = True
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_nonmanager_cannot_discover_elements(self):
        with self.assertRaises(AccessError):
            self._wizard().with_user(self.env.ref("base.public_user")).action_discover()


@tagged("post_install", "-at_install")
class TestGuidedRecordFilter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = cls.env["ir.model"]._get("res.partner")
        cls.owner = cls.env["ir.model.fields"].search([("model_id", "=", cls.model.id), ("name", "=", "user_id")], limit=1)
        cls.profile = cls.env["eh.access.profile"].create({"name": "Guided filter draft", "active": False})

    def _wizard(self, **values):
        vals = {"profile_id": self.profile.id, "model_id": self.model.id, "owner_field_id": self.owner.id,
                "scope": "own", "rights": "read_write", "additional_domain": "[('active', '=', True)]"}
        vals.update(values)
        return self.env["eh.access.domain.wizard"].create(vals)

    def test_visual_filter_keeps_runtime_user_token(self):
        wizard = self._wizard()
        wizard.action_apply()
        line = self.profile.domain_line_ids
        self.assertEqual(len(line), 1)
        self.assertIn("__uid__", line.domain)
        self.assertIn("active", line.domain)
        self.assertTrue(line.read_right)
        self.assertTrue(line.write_right)
        self.assertFalse(line.create_right)
        self.assertFalse(self.profile.active)

    def test_wrong_model_owner_field_is_rejected(self):
        wrong_field = self.env["ir.model.fields"].search([("model", "=", "res.users"), ("name", "=", "company_id")], limit=1)
        wizard = self._wizard(owner_field_id=wrong_field.id)
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertFalse(self.profile.domain_line_ids)

    def test_invalid_filter_never_mutates_profile(self):
        wizard = self._wizard(additional_domain="not a domain")
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertFalse(self.profile.domain_line_ids)

    def test_second_filter_cannot_silently_widen_access(self):
        wizard = self._wizard()
        wizard.action_apply()
        with self.assertRaises(UserError):
            self._wizard(scope="all", additional_domain="[]").action_apply()
        self.assertEqual(len(self.profile.domain_line_ids), 1)

    def test_builder_never_changes_live_profile(self):
        self.profile.active = True
        with self.assertRaises(UserError):
            self._wizard().action_apply()
        self.assertFalse(self.profile.domain_line_ids)

    def test_nonmanager_cannot_apply_filter(self):
        with self.assertRaises(AccessError):
            self._wizard().with_user(self.env.ref("base.public_user")).action_apply()
