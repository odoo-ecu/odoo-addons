# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage
# License LGPL-3.0 or later.
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPolicyRevisions(TransactionCase):

    def setUp(self):
        super().setUp()
        self.profile = self.env["eh.access.profile"].create({"name": "Revision subject", "hide_export": True})
        self.Revision = self.env["eh.access.revision"]
        self.user = self.env["res.users"].with_context(no_reset_password=True).create({"name": "Revision nonmanager", "login": "revision_nonmanager"})

    def test_snapshot_immutable_and_restore_preserves_prior_state(self):
        revision = self.Revision._snapshot(self.profile, "Baseline")
        original_hash = revision.fingerprint
        self.profile.hide_export = False
        expected = self.Revision._policy_fingerprint(self.profile)
        with self.assertRaises(UserError):
            revision._restore(expected)
        revision._restore(expected, confirm_active=True)
        self.assertTrue(self.profile.hide_export)
        self.assertEqual(self.Revision._policy_fingerprint(self.profile), original_hash)
        self.assertEqual(self.Revision.search_count([("profile_id", "=", self.profile.id)]), 3)
        with self.assertRaises(AccessError):
            revision.write({"reason": "Rewrite history"})
        with self.assertRaises(AccessError):
            revision.unlink()
        with self.assertRaises(AccessError):
            self.Revision.create({"name": "Fake", "policy": {}, "fingerprint": "fake", "profile_name": "Fake"})

    def test_restore_rejects_stale_and_child_line_changes(self):
        revision = self.Revision._snapshot(self.profile, "Baseline")
        expected = self.Revision._policy_fingerprint(self.profile)
        self.env["eh.access.node"].create({"profile_id": self.profile.id, "model_id": self.env["ir.model"]._get_id("res.partner"), "kind": "button", "target_name": "x"})
        with self.assertRaises(UserError):
            revision._restore(expected, confirm_active=True)
        self.assertTrue(self.profile.node_line_ids)

    def test_restore_keeps_current_profile_identity(self):
        revision = self.Revision._snapshot(self.profile, "Before rename")
        self.profile.write({"name": "Renamed role", "hide_export": False})
        revision._restore(self.Revision._policy_fingerprint(self.profile), confirm_active=True)
        self.assertEqual(self.profile.name, "Renamed role")
        self.assertTrue(self.profile.hide_export)

    def test_restore_preview_cannot_be_retargeted_or_forged(self):
        revision = self.Revision._snapshot(self.profile, "Baseline")
        self.profile.hide_export = False
        wizard = self.env["eh.access.restore.wizard"].create({"revision_id": revision.id})
        self.assertIn("hide_export", wizard.summary)
        with self.assertRaises(AccessError):
            wizard.write({"expected_fingerprint": "forged"})
        with self.assertRaises(AccessError):
            wizard.write({"revision_id": revision.id})
        wizard.confirm_active = True
        wizard.action_apply()
        self.assertTrue(self.profile.hide_export)

    def test_non_manager_cannot_access_mutation_helpers_or_restore(self):
        revision = self.Revision._snapshot(self.profile, "Baseline")
        with self.assertRaises(AccessError):
            self.Revision.with_user(self.user)._snapshot(self.profile, "Unauthorized")
        with self.assertRaises(AccessError):
            revision.with_user(self.user).action_restore()
        with self.assertRaises(AccessError):
            revision.with_user(self.user)._restore(revision.fingerprint, True)

    def test_relation_only_policy_edits_lock_before_mutation(self):
        """Reviewed apply also excludes ordinary edits that issue no row UPDATE."""
        model = self.env["ir.model"]._get_id("res.partner")
        partner_field = self.env["ir.model.fields"].search([("model", "=", "res.partner"), ("name", "=", "function")], limit=1)
        line = self.env["eh.access.field"].create({"profile_id": self.profile.id, "model_id": model,
                                                   "field_ids": [(6, 0, partner_field.ids)], "invisible": True})
        menu = self.env["ir.ui.menu"].search([], limit=1)
        self.profile.hidden_menu_ids = menu
        self.env.flush_all()
        cursor_type = type(self.env.cr)
        original_execute = cursor_type.execute
        governance_locked = []
        profile_locked = []
        profile_versioned = []
        mutations = []

        def observed_execute(cursor, query, *args, **kwargs):
            code = (query._sql_tuple[0] if hasattr(query, "_sql_tuple") else
                    query.code if hasattr(query, "code") else str(query))
            if "pg_advisory_xact_lock" in code:
                governance_locked.append(True)
            if "SELECT id FROM eh_access_profile" in code and "FOR UPDATE" in code:
                self.assertTrue(governance_locked, "Governance lock must precede policy rows.")
                profile_locked.append(True)
            if "UPDATE eh_access_profile SET write_date = write_date" in code:
                self.assertTrue(profile_locked)
                profile_versioned.append(True)
            if (any(table in code for table in ("eh_access_profile_menu_rel", "eh_access_field_field_rel"))
                    and code.lstrip().upper().startswith(("INSERT", "DELETE", "UPDATE"))):
                mutations.append(bool(profile_locked and profile_versioned))
            return original_execute(cursor, query, *args, **kwargs)

        for record, values in ((self.profile, {"hidden_menu_ids": [(5, 0, 0)]}),
                               (line, {"field_ids": [(5, 0, 0)]})):
            governance_locked.clear()
            profile_locked.clear()
            profile_versioned.clear()
            mutations.clear()
            with patch.object(cursor_type, "execute", new=observed_execute):
                record.write(values)
                self.env.flush_all()
            self.assertTrue(mutations, "Observe an actual native relation change.")
            self.assertTrue(all(mutations), "Policy row lock and version fence must precede every relation mutation.")
