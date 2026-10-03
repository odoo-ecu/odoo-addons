# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: YAML import / export round-trip test.

Covers:
  * an exported profile reimports to an equivalent record
  * lines and toggles survive the round trip
  * import is idempotent: re-running import on the same payload does
    not create duplicates of an existing profile name
"""
import base64

import yaml
from odoo.exceptions import AccessError, UserError

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestYamlWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.Profile = cls.env["eh.access.profile"]
        cls.Wizard = cls.env["eh.access.yaml.wizard"]
        cls.IrModel = cls.env["ir.model"]
        cls.IrField = cls.env["ir.model.fields"]

        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "YAML Demo",
            "login": "eh_access_yaml_demo@example.test",
        })
        cls.partner_model = cls.IrModel._get("res.partner")
        cls.comment_field = cls.IrField.search([
            ("model", "=", "res.partner"),
            ("name", "=", "comment"),
        ], limit=1)

    def _make_full_profile(self, name="Round-trip"):
        return self.Profile.create({
            "name": name,
            "user_ids": [(6, 0, [self.demo_user.id])],
            "hide_export": True,
            "hide_chatter": True,
            "hide_custom_filter": True,
            "hide_custom_group": True,
            "restrict_module_management": True,
            # Hidden menus are part of the round trip on purpose: the
            # importer used to resolve them with a search on
            # complete_name, a non-stored compute with no search method,
            # so every export that hid a menu raised on import.
            "hidden_menu_ids": [(6, 0, [self.env.ref("base.menu_administration").id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "field_ids": [(6, 0, [self.comment_field.id])],
                "invisible": True,
                "hide_external_link": True,
                "condition": "is_company",
            })],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "restrict_create": True,
                "restrict_export": True,
                "hide_search_panel": True,
            })],
            "node_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "kind": "button",
                "target_name": "action_view_orders",
                "target_label": "View Orders",
                "condition": "is_company",
            })],
            "domain_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "read_right": True,
                "write_right": True,
                "apply_filter": True,
                "soft_restrict": True,
                "domain": '[("user_id", "=", "__uid__")]',
            })],
        })

    def _export(self, profile):
        wiz = self.Wizard.create({
            "mode": "export",
            "profile_ids": [(6, 0, [profile.id])],
        })
        wiz.action_export()
        return (wiz.payload.content if hasattr(wiz.payload, "content") else base64.b64decode(wiz.payload)).decode("utf-8")

    def _import(self, text):
        wiz = self.Wizard.create({
            "mode": "import",
            "payload": base64.b64encode(text.encode("utf-8")),
        })
        wiz.action_preview()
        wiz.confirm_active = True
        wiz.action_import()
        return wiz

    def test_export_then_import_yields_equivalent_profile(self):
        original = self._make_full_profile(name="Source RT")
        text = self._export(original)
        self.assertIn("Source RT", text)
        self.assertIn("hide_export", text)
        self.assertIn("__uid__", text)

        # Delete the original then reimport to a clean state.
        original.unlink()
        self._import(text)
        clone = self.Profile.search([("name", "=", "Source RT")], limit=1)
        self.assertTrue(clone, "imported profile should exist")
        self.assertTrue(clone.hide_export)
        self.assertTrue(clone.hide_chatter)
        self.assertTrue(clone.hide_custom_filter)
        self.assertTrue(clone.hide_custom_group)
        self.assertTrue(clone.restrict_module_management)
        self.assertTrue(clone.model_line_ids.hide_search_panel)
        self.assertEqual(clone.field_line_ids.condition, "is_company")
        self.assertEqual(clone.node_line_ids.condition, "is_company")
        self.assertTrue(clone.domain_line_ids.soft_restrict)
        self.assertEqual(len(clone.field_line_ids), 1)
        self.assertTrue(clone.field_line_ids.invisible)
        self.assertTrue(
            clone.field_line_ids.hide_external_link,
            "hide_external_link must survive the round trip",
        )
        self.assertEqual(
            clone.hidden_menu_ids,
            self.env.ref("base.menu_administration"),
            "hidden menus must survive the round trip",
        )
        self.assertEqual(len(clone.model_line_ids), 1)
        self.assertTrue(clone.model_line_ids.restrict_create)
        self.assertEqual(len(clone.node_line_ids), 1)
        self.assertEqual(clone.node_line_ids.target_name, "action_view_orders")
        self.assertEqual(len(clone.domain_line_ids), 1)
        self.assertTrue(clone.domain_line_ids.apply_filter)
        self.assertIn("__uid__", clone.domain_line_ids.domain)
        self.assertIn(self.demo_user, clone.user_ids)

    def test_import_is_idempotent_by_name(self):
        original = self._make_full_profile(name="Idempotent RT")
        text = self._export(original)
        self._import(text)
        self._import(text)
        matches = self.Profile.search([("name", "=", "Idempotent RT")])
        self.assertEqual(len(matches), 1, "no duplicate profiles on re-import")


    def _wizard_for(self, data):
        return self.Wizard.create({"mode": "import", "payload": base64.b64encode(yaml.safe_dump(data).encode())})

    def test_preview_changes_no_policy_and_apply_requires_review(self):
        profile = self._make_full_profile("Preview unchanged")
        wizard = self._wizard_for({"schema_version": 2, "profiles": [{"name": profile.name, "hide_export": False}]})
        with self.assertRaises(UserError):
            wizard.action_import()
        wizard.action_preview()
        self.assertTrue(profile.hide_export)
        self.assertIn("hide_export", wizard.summary)
        with self.assertRaises(UserError):
            wizard.action_import()
        wizard.confirm_active = True
        wizard.action_import()
        self.assertFalse(profile.hide_export)

    def test_null_and_empty_lists_clear_assignments_and_rules(self):
        profile = self._make_full_profile("Clear collections")
        wizard = self._wizard_for({"profiles": [{"name": profile.name, "users": [], "companies": None,
                                               "hidden_menus": [], "field_lines": None}]})
        wizard.action_preview()
        wizard.confirm_active = True
        wizard.action_import()
        self.assertFalse(profile.user_ids)
        self.assertFalse(profile.company_ids)
        self.assertFalse(profile.hidden_menu_ids)
        self.assertFalse(profile.field_line_ids)
        self.assertTrue(profile.model_line_ids, "Missing keys preserve existing rules")

    def test_missing_reference_and_unknown_keys_fail_preview(self):
        bad_entries = [
            {"name": "Missing user", "users": ["absent_user_7654321"]},
            {"name": "Missing model", "domain_lines": [{"model": "absent.model"}]},
            {"name": "Missing field", "field_lines": [{"model": "res.partner", "fields": ["absent_field"], "invisible": True}]},
            {"name": "Typo", "hide_exprot": True},
            {"name": "Wrong boolean", "hide_export": "false"},
            {"name": "Bad condition", "node_lines": [{"model": "res.partner", "target_name": "x", "condition": "env.user"}]},
        ]
        for entry in bad_entries:
            with self.subTest(entry=entry), self.assertRaises(UserError):
                self._wizard_for({"profiles": [entry]}).action_preview()

    def test_missing_and_ambiguous_menu_fail_instead_of_dropping(self):
        self.env["ir.ui.menu"].create([{"name": "Ambiguous portable menu"}, {"name": "Ambiguous portable menu"}])
        for label in ("Ambiguous portable menu", "Absent portable menu"):
            with self.assertRaises(UserError):
                self._wizard_for({"profiles": [{"name": "Menu check", "hidden_menus": [label]}]}).action_preview()

    def test_policy_edit_after_preview_refuses_stale_apply(self):
        profile = self._make_full_profile("Stale preview")
        wizard = self._wizard_for({"profiles": [{"name": profile.name, "hide_export": False}]})
        wizard.action_preview()
        profile.field_line_ids.condition = "not is_company"
        wizard.confirm_active = True
        with self.assertRaises(UserError):
            wizard.action_import()
        self.assertTrue(profile.hide_export)

    def test_preview_evidence_cannot_be_forged(self):
        wizard = self._wizard_for({"profiles": [{"name": "Forgery"}]})
        with self.assertRaises(AccessError):
            wizard.write({"preview_digest": "anything", "preview_state": {}})
        with self.assertRaises(AccessError):
            self.Wizard.create({"mode": "import", "preview_digest": "anything"})

    def test_non_manager_cannot_export_or_preview_using_public_methods(self):
        profile = self._make_full_profile("No unauthorized export")
        wizard = self.Wizard.create({"mode": "export", "profile_ids": [(6, 0, profile.ids)]})
        for method in ("action_export", "action_preview", "action_import"):
            with self.assertRaises(AccessError):
                getattr(wizard.with_user(self.demo_user), method)()

    def test_unsupported_schema_rejected_legacy_accepted(self):
        with self.assertRaises(UserError):
            self._wizard_for({"schema_version": 99, "profiles": [{"name": "Future"}]}).action_preview()
        wizard = self._wizard_for({"profiles": [{"name": "Legacy accepted", "active": False, "users": [self.demo_user.login]}]})
        wizard.action_preview()
        wizard.action_import()
        self.assertTrue(self.Profile.with_context(active_test=False).search([("name", "=", "Legacy accepted")]))

    def test_stable_reference_and_unique_fallback_for_unregistered_action(self):
        action = self.env["ir.actions.server"].create({"name": "Portable custom action", "model_id": self.partner_model.id, "state": "code", "code": "pass"})
        profile = self._make_full_profile("Custom action roundtrip")
        profile.model_line_ids.hidden_server_action_ids = action
        data = self.Wizard._serialise_profile(profile)
        ref = data["model_lines"][0]["hidden_server_actions"][0]
        self.assertEqual(ref["name"], action.name)
        self.assertEqual(ref["model"], "res.partner")
        ref["xml_id"] = "missing_module.missing_action"
        self.assertEqual(self.Wizard._resolve_refs("ir.actions.server", [ref]), action.ids)

    def test_whole_import_rolls_back_when_later_entry_is_stale(self):
        first = self._make_full_profile("A batch first")
        last = self._make_full_profile("Z batch last")
        wizard = self._wizard_for({"profiles": [{"name": first.name, "hide_export": False}, {"name": last.name, "hide_export": False}]})
        wizard.action_preview()
        last.hide_custom_filter = False
        wizard.confirm_active = True
        with self.assertRaises(UserError):
            wizard.action_import()
        self.assertTrue(first.hide_export)
        self.assertTrue(last.hide_export)


    def test_duplicate_yaml_keys_and_recursive_aliases_fail_review(self):
        for text in ("profiles: [{name: Duplicate, hide_export: true, hide_export: false}]",
                     "profiles: &loop [*loop]"):
            wizard = self.Wizard.create({"mode": "import", "payload": base64.b64encode(text.encode())})
            with self.assertRaises(UserError):
                wizard.action_preview()
