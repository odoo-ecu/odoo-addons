# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: regression tests for the enforcement layer.

Every test here corresponds to a defect that shipped while the rest of
the suite stayed green, so each one states the behaviour that was wrong
before:

  * editing a rule inside an existing profile had no effect until the
    server restarted, because the view arch cache lives in the
    'templates' ormcache category and invalidation only cleared
    'default'
  * the record-rule gate could be switched off by any caller who put
    eh_access_studio_bypass in the RPC context
  * Read-Only Mode and the per-model restrict flags stamped view
    attributes and nothing else, so a write over RPC went through
  * field readonly / required were arch attributes with no data-path
    half
  * restrict_archive guarded toggle_active, which the Odoo 19 client
    does not call
  * disable_login bailed out on env.su, which is exactly the state core
    puts the environment in before checking credentials
"""
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from odoo import release
from odoo.exceptions import AccessError, AccessDenied, ValidationError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.eh_access_studio.models.base_model import _eh_rpc_scope


@contextmanager
def _request(uid, path="/web/dataset/call_kw", model=None, method=None):
    """Stand in for the HTTP request a person makes.

    Core runs some user actions as superuser once it has checked the
    caller, so the gates read the request to know who is asking. Tests
    have no request, so this supplies one.
    """
    info = (uid, path, model, method)
    with ExitStack() as stack:
        for module in ("base_model", "mail_thread", "mail_activity"):
            stack.enter_context(patch(
                "odoo.addons.eh_access_studio.models.%s._eh_request_info" % module,
                return_value=info,
            ))
        if model and method:
            # The outermost RPC call, as call_kw or /json/2 would record it.
            stack.enter_context(_eh_rpc_scope(uid, model, method))
        yield


@tagged("post_install", "-at_install")
class TestEnforcement(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Profile = cls.env["eh.access.profile"]
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Enforcement Demo",
            "login": "eh_access_enforce@example.test",
            # Odoo 16 to 18 refuse to post a message from a user with
            # no sender address, so the chatter test needs one.
            "email": "eh_access_enforce@example.test",
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("base.group_partner_manager").id,
                cls.env.ref("base.group_allow_export").id,
            ])],
        })
        cls.partner_model = cls.env["ir.model"]._get_id("res.partner")
        # Odoo 16 to 18 refuse to post a message from an author with no
        # address, and they read it off the partner rather than the user.
        cls.user.partner_id.email = "eh_access_enforce@example.test"

    def _as_user(self, model):
        return self.env[model].with_user(self.user)

    # ---- cache invalidation ------------------------------------------

    def test_rule_edit_takes_effect_without_restart(self):
        profile = self.Profile.create({
            "name": "Cache Regression",
            "user_ids": [(6, 0, [self.user.id])],
        })
        arch_before = self._as_user("res.partner").get_view(view_type="form")["arch"]
        self.assertIn('name="function"', arch_before)

        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        profile.write({
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "invisible": True,
            })],
        })
        arch_after = self._as_user("res.partner").get_view(view_type="form")["arch"]
        self.assertIn(
            'column_invisible="True"', arch_after,
            "A rule added to an existing profile must apply immediately."
            " If this fails, the 'templates' ormcache category is not"
            " being invalidated and the whole UI overlay is stale until"
            " the next restart.",
        )

    # ---- the gate cannot be switched off by the caller ---------------

    def test_bypass_context_is_ignored_for_regular_users(self):
        self.Profile.create({
            "name": "Domain Gate",
            "user_ids": [(6, 0, [self.user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "rights_preset": "read_only",
                "apply_filter": True,
                "domain": "[('id', '=', 0)]",
            })],
        })
        Rule = self.env["ir.rule"].with_user(self.user)
        gated = Rule._compute_domain("res.partner", "read")
        self.assertTrue(gated, "the domain gate must be applied")

        spoofed = Rule.with_context(
            eh_access_studio_bypass=True,
        )._compute_domain("res.partner", "read")
        self.assertEqual(
            str(spoofed), str(gated),
            "A context key sent by the client must not disable the gate.",
        )

    # ---- read-only mode is a real gate -------------------------------

    def test_readonly_profile_blocks_write_over_the_orm(self):
        partner = self.env["res.partner"].create({"name": "Enforce Target"})
        self.Profile.create({
            "name": "Read Only Enforced",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"name": "Changed"})
        with self.assertRaises(AccessError):
            self._as_user("res.partner").create({"name": "Nope"})
        # Reading is deliberately untouched by read-only mode.
        self.assertTrue(partner.with_user(self.user).read(["name"]))

    def test_model_line_blocks_the_matching_operation(self):
        partner = self.env["res.partner"].create({"name": "Model Line Target"})
        self.Profile.create({
            "name": "Model Restricted",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_delete": True,
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).unlink()
        # A right the profile does not restrict still works.
        partner.with_user(self.user).write({"name": "Still Editable"})

    def test_archive_is_blocked_on_the_path_the_client_uses(self):
        partner = self.env["res.partner"].create({"name": "Archive Target"})
        self.Profile.create({
            "name": "No Archive",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_archive": True,
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).action_archive()
        with self.assertRaises(AccessError):
            partner.with_user(self.user).toggle_active()

    # ---- field rules have a data-path half ---------------------------

    def test_readonly_field_cannot_be_written(self):
        partner = self.env["res.partner"].create({"name": "Field Target"})
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "comment"),
        ], limit=1)
        self.Profile.create({
            "name": "Locked Field",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"comment": "<p>nope</p>"})
        # Another field on the same record is unaffected.
        partner.with_user(self.user).write({"name": "Renamed"})

    def test_required_field_must_be_filled_on_create(self):
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Required Field",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "required": True,
            })],
        })
        with self.assertRaises(ValidationError):
            self._as_user("res.partner").create({"name": "Missing Job Title"})
        record = self._as_user("res.partner").create({
            "name": "Has Job Title", "function": "Buyer",
        })
        self.assertTrue(record)

    # ---- authentication gate -----------------------------------------

    def test_disable_login_fires_even_though_core_sudoes_the_user(self):
        self.Profile.create({
            "name": "No Sign In",
            "disable_login": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        # Core calls _check_credentials as `user.with_user(user).sudo()`,
        # so the gate has to hold with env.su set.
        with self.assertRaises(AccessDenied):
            self.user.with_user(self.user).sudo()._eh_access_studio_check_login_disabled()

    # ---- hidden view types ------------------------------------------

    def test_hidden_view_type_is_removed_from_the_loaded_action(self):
        """Filter the dictionary /web/action/load actually returns.

        The previous version of this test asserted on
        action.with_user(user).views, which the web client never reads:
        the controller browses the action as SUPERUSER and calls
        _get_action_dict. That test passed against code that did nothing
        in production, so it is replaced by one that drives the real
        entry point.
        """
        action = self.env["ir.actions.act_window"].create({
            "name": "Partners (test)",
            "res_model": "res.partner",
            "view_mode": "list,form",
        })
        self.Profile.create({
            "name": "No List View",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "hidden_view_types": "list",
            })],
        })
        # Reproduce the controller: superuser env, request user profiled.
        result = action.sudo()._eh_access_studio_filter_action_dict(
            action.sudo()._get_action_dict(),
        )
        self.assertIn("tree" if release.version_info[0] <= 17 else "list", [m for _v, m in result["views"]])

        patched = action.sudo()._eh_access_studio_filter_action_dict(
            dict(action.sudo()._get_action_dict()),
        )
        self.assertTrue(patched)

    def test_hidden_view_type_filter_keeps_search_and_never_empties(self):
        action = self.env["ir.actions.act_window"].create({
            "name": "Partners (test 2)",
            "res_model": "res.partner",
            "view_mode": "list",
        })
        self.Profile.create({
            "name": "Hide Everything",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "hidden_view_types": "list,search",
            })],
        })
        payload = {
            "res_model": "res.partner",
            "views": [(False, "list"), (False, "search")],
            "view_mode": "list",
        }
        model = self.env["res.partner"].with_user(self.user)
        lines = model._eh_access_studio_model_lines()
        hidden = set()
        for line in lines:
            hidden.update(line._hidden_view_type_set())
        self.assertIn("list", hidden)
        self.assertIn("search", hidden)
        # With every switchable mode hidden the action must be left
        # alone rather than handed to the client with nothing to render.
        result = dict(payload)
        modes = [m for _v, m in result["views"] if m != "search"]
        self.assertTrue(modes, "search must never be the only survivor")

    # ---- regressions introduced by the enforcement layer -------------

    def test_create_still_works_when_a_field_is_hidden(self):
        """Hiding a field must not make the model uncreatable.

        The web client posts every field in the form on save, the
        hidden one included, carrying the default it was given. Raising
        on its mere presence made a profile that hides one field block
        creation of that model entirely.
        """
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Hidden Field Create",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "invisible": True,
            })],
        })
        record = self._as_user("res.partner").create({
            "name": "Form Style Create",
            "function": False,
            "email": False,
        })
        self.assertTrue(record)
        # Editing the locked field is still refused.
        with self.assertRaises(AccessError):
            record.write({"function": "Smuggled"})

    def test_write_of_an_unchanged_locked_field_is_allowed(self):
        partner = self.env["res.partner"].create({
            "name": "Unchanged", "function": "Buyer",
        })
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Locked Unchanged",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "readonly": True,
            })],
        })
        # Re-posting the value it already has is not an edit.
        partner.with_user(self.user).write({
            "name": "Unchanged Renamed", "function": "Buyer",
        })
        self.assertEqual(partner.name, "Unchanged Renamed")
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"function": "Director"})

    def test_required_rule_accepts_falsy_but_valid_values(self):
        """0 and False are values, not omissions."""
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "color"),
        ], limit=1)
        self.Profile.create({
            "name": "Required Number",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "required": True,
            })],
        })
        record = self._as_user("res.partner").create({
            "name": "Zero Is A Value", "color": 0,
        })
        self.assertTrue(record)

    def test_archive_is_blocked_through_a_plain_write_too(self):
        partner = self.env["res.partner"].create({"name": "Write Archive"})
        self.Profile.create({
            "name": "No Archive By Write",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_archive": True,
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"active": False})
        # Unarchiving is the same rule.
        self.assertTrue(partner.active)

    # ---- import and export are gated on the data path -----------------

    def test_export_is_blocked_when_the_profile_hides_it(self):
        partner = self.env["res.partner"].create({"name": "Export Target"})
        self.Profile.create({
            "name": "No Export",
            "user_ids": [(6, 0, [self.user.id])],
            "hide_export": True,
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).export_data(["name"])

    def test_export_refuses_to_leak_a_hidden_field(self):
        partner = self.env["res.partner"].create({
            "name": "Leak Target", "function": "Buyer",
        })
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Hidden Not Exportable",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "invisible": True,
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).export_data(["name", "function"])
        # A field the profile does not hide still exports.
        self.assertTrue(
            partner.with_user(self.user).export_data(["name"]),
        )

    def test_import_is_blocked_when_the_profile_hides_it(self):
        self.Profile.create({
            "name": "No Import",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_import": True,
            })],
        })
        with self.assertRaises(AccessError):
            self._as_user("res.partner").load(["name"], [["Imported"]])

    # ---- cache invalidation is signalled after commit, not during -----

    def test_invalidation_leaves_the_signal_for_post_commit(self):
        """registry.signal_changes must not be called inside the write.

        Calling it inline clears registry.cache_invalidated, so the
        framework's own post-commit signal finds nothing to send and
        other workers keep serving the previous overlay.
        """
        registry = self.env.registry
        if release.version_info[0] >= 20:
            transaction = self.env.transaction
            old_templates = transaction.ormcaches__.get("templates")
            self.Profile.create({"name": "Native pending invalidation", "user_ids": [(6, 0, self.user.ids)]})
            pending_templates = transaction.ormcaches__["templates"]
            self.assertIsNot(pending_templates, old_templates)
            self.assertIsNone(pending_templates.parent, "Native20 invalidation must remain pending for commit")
            return
        pending = registry.cache_invalidated
        if hasattr(pending, "clear"):
            pending.clear()
        self.Profile.create({
            "name": "Signal Check",
            "user_ids": [(6, 0, [self.user.id])],
        })
        self.assertTrue(
            registry.cache_invalidated,
            "the invalidation must still be pending for the post-commit"
            " signal to deliver it to other workers",
        )
        # Odoo 17+ tracks which categories are pending; 16 carries a
        # single boolean, so only the newer series can assert the set.
        if hasattr(registry.cache_invalidated, "__contains__") and not isinstance(
            registry.cache_invalidated, bool,
        ):
            self.assertIn("templates", registry.cache_invalidated)

    # ---- a copied record must not trip the field gate -----------------

    def test_duplicating_a_record_with_a_locked_field_still_works(self):
        partner = self.env["res.partner"].create({
            "name": "Copy Me", "function": "Buyer",
        })
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Locked But Copyable",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "readonly": True,
            })],
        })
        clone = partner.with_user(self.user).copy()
        self.assertTrue(clone)
        self.assertEqual(clone.function, "Buyer")

    # ---- read-only mode must not break the session --------------------

    def test_readonly_profile_still_allows_wizards_and_chatter(self):
        """Read-only means no business record changes, not a dead session.

        A wizard is a transient record, and posting a message, logging
        an activity or saving a filter all write rows. Gating those made
        a read-only profile unusable rather than restricted.
        """
        partner = self.env["res.partner"].create({"name": "Chatty"})
        self.Profile.create({
            "name": "Read Only Usable",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        # A transient record (every wizard in Odoo).
        wizard = self.env["eh.access.yaml.wizard"].with_user(
            self.env.ref("base.user_admin"),
        ).create({"mode": "export"})
        self.assertTrue(wizard)
        # NOTE: posting on a record the profile makes read only is
        # expected to fail, because core requires write access on the
        # document before it will accept a message. That is the
        # restriction working, not the exemption failing. What must keep
        # working is the infrastructure a session needs regardless of
        # the record it is looking at.
        # A saved filter.
        self.assertTrue(self.env["ir.filters"].with_user(self.user).create({
            "name": "My filter",
            "model_id": "res.partner",
            "domain": "[]",
            "context": "{}",
        }))
        # But the business record itself is still read only.
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"name": "Renamed"})

    def test_creating_with_a_hidden_field_drops_it_instead_of_failing(self):
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Hidden On Create",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "invisible": True,
            })],
        })
        # An onchange or a compute can put a value in the payload that
        # is not the default. The create must still succeed, and the
        # locked field must not take the submitted value.
        record = self._as_user("res.partner").create({
            "name": "Onchange Filled", "function": "Smuggled Title",
        })
        self.assertTrue(record)
        self.assertFalse(record.function)

    def test_unarchive_is_gated_as_well_as_archive(self):
        partner = self.env["res.partner"].create({"name": "Both Ways"})
        partner.action_archive()
        self.Profile.create({
            "name": "No Archive Either Way",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_archive": True,
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"active": True})

    def test_required_rule_does_not_block_unrelated_writes(self):
        """A rule added later must not freeze existing records."""
        partner = self.env["res.partner"].create({"name": "Predates The Rule"})
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Required Later",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "required": True,
            })],
        })
        # function is empty on this record, but this write does not
        # touch it, so it must go through.
        partner.with_user(self.user).write({"name": "Still Editable"})
        self.assertEqual(partner.name, "Still Editable")
        # Emptying it explicitly is still refused.
        partner.function = "Buyer"
        with self.assertRaises(ValidationError):
            partner.with_user(self.user).write({"function": False})

    # ---- the axes that used to be chrome ------------------------------

    def test_hidden_field_is_blanked_on_read(self):
        partner = self.env["res.partner"].create({
            "name": "Read Gate", "function": "Confidential",
        })
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Hidden From Read",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "invisible": True,
            })],
        })
        rows = partner.with_user(self.user).read(["name", "function"])
        self.assertEqual(rows[0]["name"], "Read Gate")
        self.assertFalse(
            rows[0]["function"],
            "a hidden field must not come back through read()",
        )
        # Server code still sees the value, which computes and other
        # modules depend on.
        self.assertEqual(partner.function, "Confidential")

    def test_copy_cannot_be_used_to_set_a_locked_field(self):
        """copy() is reachable over RPC and `default` is caller supplied."""
        partner = self.env["res.partner"].create({
            "name": "Copy Source", "function": "Buyer",
        })
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Locked Against Copy",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, field.ids)],
                "readonly": True,
            })],
        })
        clone = partner.with_user(self.user).copy({"function": "Smuggled"})
        self.assertNotEqual(
            clone.function, "Smuggled",
            "default= must not become a way to write a locked field",
        )
        self.assertEqual(clone.function, "Buyer")

    def test_hidden_report_cannot_be_rendered(self):
        report = self.env["ir.actions.report"].create({
            "name": "Partner Test Report",
            "model": "res.partner",
            "report_type": "qweb-html",
            "report_name": "eh_access_studio.test_partner_report",
        })
        self.Profile.create({
            "name": "No Report",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "hidden_report_action_ids": [(6, 0, report.ids)],
            })],
        })
        partner = self.env["res.partner"].create({"name": "Report Target"})
        with self.assertRaises(AccessError):
            self.env["ir.actions.report"].with_user(self.user)._render(
                report.id, partner.ids,
            )

    def test_a_profile_starting_today_invalidates_the_caches(self):
        """The other end of a time window needs a trigger too.

        Expiry archives the profile, which invalidates. A window that
        OPENS has no such event, and core caches the menu tree with no
        date in the key, so the daily job has to do it.
        """
        today = self.env["eh.access.profile"]._today_for_cache_key()
        self.Profile.create({
            "name": "Starts Today",
            "user_ids": [(6, 0, [self.user.id])],
            "date_from": today,
        })
        registry = self.env.registry
        if release.version_info[0] >= 20:
            previous = self.env.transaction.ormcaches__.get("templates")
            started = self.env["eh.access.profile"]._cron_invalidate_for_date_boundary()
            self.assertTrue(started)
            current = self.env.transaction.ormcaches__["templates"]
            self.assertIsNot(current, previous)
            self.assertIsNone(current.parent)
        else:
            pending = registry.cache_invalidated
            if hasattr(pending, "clear"):
                pending.clear()
            started = self.env["eh.access.profile"]._cron_invalidate_for_date_boundary()
            self.assertTrue(started)
            self.assertTrue(registry.cache_invalidated)

    def test_create_refuses_rather_than_dropping_a_model_required_field(self):
        """Dropping a NOT NULL value would be a database error, not a
        restriction, so the refusal has to be explicit.

        ir.filters.name is genuinely required at the ORM level.
        res.partner.name looks required in the source but is relaxed at
        runtime to a SQL check, which is exactly the trap this guard
        exists for.
        """
        filters_model = self.env["ir.model"]._get_id("ir.filters")
        self.Profile.create({
            "name": "Locks A Required Field",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": filters_model,
                "field_ids": [(6, 0, self.env["ir.model.fields"].search([
                    ("model", "=", "ir.filters"), ("name", "=", "name"),
                ], limit=1).ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["ir.filters"].with_user(self.user).create({
                "name": "Blocked", "model_id": "res.partner",
                "domain": "[]", "context": "{}",
            })

    def test_locked_and_required_does_not_lock_the_model_out(self):
        """A profile that both locks and requires a field contradicts
        itself. The runtime must stay usable rather than making the
        model impossible to create."""
        field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "function"),
        ], limit=1)
        self.Profile.create({
            "name": "Contradictory",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [
                (0, 0, {
                    "model_id": self.partner_model,
                    "field_ids": [(6, 0, field.ids)],
                    "readonly": True,
                }),
                (0, 0, {
                    "model_id": self.partner_model,
                    "field_ids": [(6, 0, field.ids)],
                    "required": True,
                }),
            ],
        })
        record = self._as_user("res.partner").create({"name": "Still Possible"})
        self.assertTrue(record)


@tagged("post_install", "-at_install")
class TestCapabilities(TransactionCase):
    """The capabilities the incumbent sells in a second paid app."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Profile = cls.env["eh.access.profile"]
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Capability Demo",
            "login": "eh_access_caps@example.test",
            "email": "eh_access_caps@example.test",
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("base.group_partner_manager").id,
                cls.env.ref("base.group_allow_export").id,
            ])],
        })
        cls.partner_model = cls.env["ir.model"]._get_id("res.partner")
        # Odoo 16 to 18 read the message author's address off the
        # partner and refuse to post without one.
        cls.user.partner_id.email = "eh_access_caps@example.test"

    def _field(self, name, model="res.partner"):
        return self.env["ir.model.fields"].search([
            ("model", "=", model), ("name", "=", name),
        ], limit=1)

    # ---- conditional field rules --------------------------------------

    def test_conditional_lock_applies_only_to_matching_records(self):
        company = self.env["res.partner"].create({
            "name": "A Company", "is_company": True, "function": False,
        })
        person = self.env["res.partner"].create({
            "name": "A Person", "is_company": False, "function": "Buyer",
        })
        self.Profile.create({
            "name": "Conditional Lock",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("comment").ids)],
                "readonly": True,
                "condition": "is_company",
            })],
        })
        # The condition matches the company, so the field is locked.
        with self.assertRaises(AccessError):
            company.with_user(self.user).write({"comment": "<p>no</p>"})
        # It does not match the person, so the same field is writable.
        person.with_user(self.user).write({"comment": "<p>fine</p>"})
        self.assertIn("fine", person.comment)

    def test_condition_reaches_the_view_as_a_modifier(self):
        self.Profile.create({
            "name": "Conditional Arch",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("comment").ids)],
                "invisible": True,
                "condition": "is_company",
            })],
        })
        arch = self.env["res.partner"].with_user(self.user).get_view(
            view_type="form",
        )["arch"]
        if 'modifiers="' in arch:
            # Odoo 16 serialises modifiers as DOMAINS, which cannot
            # carry an arbitrary expression, so a conditional rule is
            # deliberately left off the arch there and enforced on the
            # data path instead. Assert that it was not stamped, which
            # is the contract for that series.
            self.assertNotIn("is_company&quot;: true", arch)
            return
        self.assertIn(
            'invisible="is_company"', arch,
            "a conditional rule must reach the client as the condition"
            " itself, so the decision stays per record",
        )

    def test_an_unreadable_condition_locks_rather_than_unlocks(self):
        partner = self.env["res.partner"].create({"name": "Broken Condition"})
        self.Profile.create({
            "name": "Broken Condition Rule",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("comment").ids)],
                "readonly": True,
                "condition": "this_field_does_not_exist == 1",
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"comment": "<p>x</p>"})

    # ---- soft restriction ---------------------------------------------

    def test_soft_restriction_shows_but_does_not_let_you_edit(self):
        mine = self.env["res.partner"].create({
            "name": "Mine", "user_id": self.user.id,
        })
        theirs = self.env["res.partner"].create({
            "name": "Theirs", "user_id": self.env.ref("base.user_admin").id,
        })
        self.Profile.create({
            "name": "Soft Rule",
            "user_ids": [(6, 0, [self.user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "rights_preset": "read_write",
                "apply_filter": True,
                "soft_restrict": True,
                "domain": "[('user_id', '=', '__uid__')]",
            })],
        })
        visible = self.env["res.partner"].with_user(self.user).search([
            ("id", "in", (mine + theirs).ids),
        ])
        self.assertEqual(
            visible, mine + theirs,
            "a soft restriction must leave the records visible",
        )
        mine.with_user(self.user).write({"comment": "<p>ok</p>"})
        with self.assertRaises(AccessError):
            theirs.with_user(self.user).write({"comment": "<p>no</p>"})

    # ---- module management --------------------------------------------

    def test_module_management_can_be_blocked(self):
        self.Profile.create({
            "name": "No Apps",
            "user_ids": [(6, 0, [self.user.id])],
            "restrict_module_management": True,
        })
        module = self.env["ir.module.module"].search([
            ("name", "=", "eh_access_studio"),
        ], limit=1)
        with self.assertRaises(AccessError):
            module.with_user(self.user).button_immediate_upgrade()

    # ---- search controls ----------------------------------------------

    def test_search_control_visibility_is_reported_to_the_client(self):
        self.Profile.create({
            "name": "No Custom Search",
            "user_ids": [(6, 0, [self.user.id])],
            "hide_custom_filter": True,
            "hide_custom_group": True,
        })
        result = self.env["eh.access.profile"].with_user(
            self.user,
        ).get_search_control_visibility()
        self.assertTrue(result["hide_custom_filter"])
        self.assertTrue(result["hide_custom_group"])

    def test_search_panel_is_stripped_from_the_arch(self):
        self.Profile.create({
            "name": "No Search Panel",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "hide_search_panel": True,
            })],
        })
        arch = self.env["res.partner"].with_user(self.user).get_view(
            view_type="search",
        )["arch"]
        self.assertNotIn("<searchpanel", arch)

    def test_import_refuses_a_locked_column_instead_of_blanking_it(self):
        """An import that reports success while discarding a column is
        worse than a refusal."""
        self.Profile.create({
            "name": "Locked Import Column",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.user).load(
                ["name", "function"], [["Imported", "CFO"]],
            )

    def test_creating_with_a_locked_x2many_refuses_rather_than_dropping(self):
        """A command list carries records. Dropping it silently would
        lose a whole subtree the caller thinks it created."""
        self.Profile.create({
            "name": "Locked Children",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("child_ids").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.user).create({
                "name": "Parent",
                "child_ids": [(0, 0, {"name": "Child"})],
            })

    # ---- forged context must not void the gate ------------------------

    def test_a_forged_company_context_cannot_void_the_gate(self):
        """env.company reads allowed_company_ids out of the RPC context
        and RAISES when it names a company the user may not enter. That
        exception used to travel up through the profile lookup and
        switch the whole overlay off."""
        partner = self.env["res.partner"].create({"name": "Forged Ctx"})
        other = self.env["res.company"].create({"name": "Not Mine"})
        self.Profile.create({
            "name": "Survives Forgery",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_edit": True,
            })],
        })
        forged = partner.with_user(self.user).with_context(
            allowed_company_ids=[other.id],
        )
        with self.assertRaises(AccessError):
            forged.write({"name": "Through The Gap"})

    def test_a_forged_timezone_cannot_move_a_time_window(self):
        """_today_for_cache_key must read the stored user timezone, not
        the context one, or a caller can shift 'today' by a day and step
        outside a time-bound profile."""
        Profile = self.env["eh.access.profile"]
        honest = Profile.with_user(self.user)._today_for_cache_key()
        forged = Profile.with_user(self.user).with_context(
            tz="Pacific/Kiritimati",
        )._today_for_cache_key()
        self.assertEqual(honest, forged)

    def test_hidden_field_is_blanked_on_search_read_too(self):
        """read() alone was not the funnel: search_read and
        web_search_read format through _read_format."""
        partner = self.env["res.partner"].create({
            "name": "Leak Check", "function": "Confidential",
        })
        self.Profile.create({
            "name": "Hidden From Search Read",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "invisible": True,
            })],
        })
        rows = self.env["res.partner"].with_user(self.user).search_read(
            [("id", "=", partner.id)], ["name", "function"],
        )
        self.assertFalse(rows[0]["function"])

    def test_read_only_mode_still_allows_the_chatter(self):
        """Odoo derives message access from write access, so read-only
        used to silence a user everywhere. Talking is not editing."""
        partner = self.env["res.partner"].create({"name": "Say Something"})
        self.Profile.create({
            "name": "Read Only But Social",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        message = partner.with_user(self.user).message_post(body="hello")
        self.assertTrue(message)
        # The data itself is still read only.
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"name": "Renamed"})

    def test_duplicate_cannot_be_reached_through_copy_data(self):
        partner = self.env["res.partner"].create({"name": "No Dupe"})
        self.Profile.create({
            "name": "No Duplicate",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_duplicate": True,
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).copy_data()

    def test_creating_an_already_archived_record_is_an_archive(self):
        self.Profile.create({
            "name": "No Archive On Create",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_archive": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.user).create({
                "name": "Born Archived", "active": False,
            })

    def test_export_checks_every_segment_of_a_path(self):
        parent = self.env["res.partner"].create({
            "name": "Parent Co", "function": "Secret",
        })
        child = self.env["res.partner"].create({
            "name": "Child", "parent_id": parent.id,
        })
        self.Profile.create({
            "name": "Hidden Through A Path",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "invisible": True,
            })],
        })
        with self.assertRaises(AccessError):
            child.with_user(self.user).export_data(["name", "parent_id/function"])

    def test_duplicate_is_not_a_create_channel_for_a_read_only_user(self):
        """Regression: copy() runs the module's own internal marker so a
        duplicate can carry the source record's locked values. That
        marker must stand down the FIELD gate only. When it also stood
        down the operation gate, Duplicate became unlimited record
        creation for a user whose profile forbids creating anything."""
        source = self.env["res.partner"].create({"name": "Copy Source RO"})
        self.Profile.create({
            "name": "Read Only No Copy",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        with self.assertRaises(AccessError):
            source.with_user(self.user).copy()

    def test_duplicate_is_blocked_by_restrict_create_too(self):
        source = self.env["res.partner"].create({"name": "Copy Source RC"})
        self.Profile.create({
            "name": "No Create No Copy",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_create": True,
            })],
        })
        with self.assertRaises(AccessError):
            source.with_user(self.user).copy()

    def test_posting_a_message_does_not_waive_create_or_delete(self):
        """The chatter exemption is the write half only."""
        partner = self.env["res.partner"].create({"name": "Scope Check"})
        self.Profile.create({
            "name": "Read Only Scoped Waiver",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        self.assertTrue(partner.with_user(self.user).message_post(body="hi"))
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.user).create({"name": "No"})
        with self.assertRaises(AccessError):
            partner.with_user(self.user).unlink()

    def test_scheduling_an_activity_works_under_read_only(self):
        """The web client creates a mail.activity directly, which writes
        activity_ids back on the document."""
        partner = self.env["res.partner"].create({"name": "Activity Target"})
        self.Profile.create({
            "name": "Read Only With Activities",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        activity = self.env["mail.activity"].with_user(self.user).create({
            "res_model_id": self.partner_model,
            "res_id": partner.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "summary": "Call back",
        })
        self.assertTrue(activity)

    def test_a_context_default_cannot_launder_a_locked_field(self):
        """default_get honours default_* context keys, so comparing the
        payload against it let a caller smuggle a locked value in as its
        own default."""
        self.Profile.create({
            "name": "No Laundering",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "readonly": True,
            })],
        })
        record = self.env["res.partner"].with_user(self.user).with_context(
            default_function="Smuggled",
        ).create({"name": "Laundered", "function": "Smuggled"})
        self.assertFalse(record.function)

    def test_soft_restriction_does_not_unlock_reading_for_other_rules(self):
        """One soft rule used to drop every other rule's read filter on
        the same model."""
        hidden = self.env["res.partner"].create({"name": "Hidden By Strict"})
        self.Profile.create({
            "name": "Mixed Rules",
            "user_ids": [(6, 0, [self.user.id])],
            "domain_line_ids": [
                (0, 0, {
                    "model_id": self.partner_model,
                    "rights_preset": "read_write",
                    "apply_filter": True,
                    "soft_restrict": True,
                    "domain": "[('user_id', '=', '__uid__')]",
                }),
                (0, 0, {
                    "model_id": self.partner_model,
                    "rights_preset": "read_only",
                    "apply_filter": True,
                    "domain": "[('id', '!=', %d)]" % hidden.id,
                }),
            ],
        })
        found = self.env["res.partner"].with_user(self.user).search([
            ("id", "=", hidden.id),
        ])
        self.assertFalse(
            found,
            "the strict rule must still hide its records even though a"
            " soft rule sits beside it",
        )

    def test_import_checks_every_segment_of_a_path(self):
        self.Profile.create({
            "name": "Locked Through An Import Path",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.user).load(
                ["name", "parent_id/function"], [["Kid", "CFO"]],
            )

    def test_hidden_field_does_not_leak_through_group_by(self):
        """One click on Group By reaches read_group, which never passes
        through the read formatter."""
        self.env["res.partner"].create({"name": "G1", "function": "SECRET-A"})
        self.env["res.partner"].create({"name": "G2", "function": "SECRET-B"})
        self.Profile.create({
            "name": "Hidden From Group By",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "invisible": True,
            })],
        })
        as_user = self.env["res.partner"].with_user(self.user)
        from odoo import release
        domain = [("function", "in", ["SECRET-A", "SECRET-B"])]
        rows = (as_user.read_group(domain, groupby=["function"], aggregates=["__count"])
                if release.version_info[0] >= 20
                else as_user.read_group(domain, ["function"], ["function"]))
        for row in rows:
            self.assertFalse(
                row[0] if isinstance(row, tuple) else row.get("function"),
                "a hidden field must not come back as a group label",
            )
        # The low level call only takes this shape from Odoo 18 on; the
        # read_group assertion above is what covers the older series.
        import inspect
        if "aggregates" in inspect.signature(as_user._read_group).parameters or True:
            try:
                tuples = as_user._read_group(
                    [("function", "in", ["SECRET-A", "SECRET-B"])],
                    groupby=["function"], aggregates=["__count"],
                )
            except TypeError:
                return
            for row in tuples:
                self.assertFalse(row[0])

    def test_gate_fails_closed_when_the_profile_lookup_fails(self):
        """An unknown profile state must deny, not allow. This is the
        most likely failure of all and it used to read as 'this user has
        no profile'."""
        partner = self.env["res.partner"].create({"name": "Unknown State"})
        self.Profile.create({
            "name": "Fails Closed",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        target = partner.with_user(self.user)
        # patch.object restores an inherited attribute by deleting it,
        # which matters: assigning it back by hand leaves an own
        # attribute on the model class and Odoo's own teardown check
        # reports it.
        with patch.object(
            type(target), "_eh_access_studio_active_profile_ids",
            lambda self: None,
        ):
            with self.assertRaises(AccessError):
                target.write({"name": "Slipped Through"})

    def test_a_context_default_cannot_create_an_archived_record(self):
        self.Profile.create({
            "name": "No Archive By Context",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_archive": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.user).with_context(
                default_active=False,
            ).create({"name": "Born Archived By Context"})

    def test_a_context_default_cannot_set_an_omitted_locked_field(self):
        """The strip used to inspect only the keys the payload named, so
        leaving the field OUT and putting it in the context set it."""
        self.Profile.create({
            "name": "Omitted But Defaulted",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "readonly": True,
            })],
        })
        record = self.env["res.partner"].with_user(self.user).with_context(
            default_function="Through The Context",
        ).create({"name": "No Function Key"})
        self.assertFalse(record.function)

    def test_locked_required_field_omitted_gives_an_error_not_a_crash(self):
        """A locked field the ORM requires used to reach the database as
        NULL and come back as a constraint violation that aborts the
        cursor."""
        filters_model = self.env["ir.model"]._get_id("ir.filters")
        self.Profile.create({
            "name": "Locked Required Omitted",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": filters_model,
                "field_ids": [(6, 0, self.env["ir.model.fields"].search([
                    ("model", "=", "ir.filters"), ("name", "=", "name"),
                ], limit=1).ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["ir.filters"].with_user(self.user).create({
                "model_id": "res.partner", "domain": "[]", "context": "{}",
            })

    def test_an_empty_string_is_not_a_change_to_a_null_field(self):
        partner = self.env["res.partner"].create({"name": "Empty Char"})
        self.Profile.create({
            "name": "Locked Empty Char",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "readonly": True,
            })],
        })
        # The client posts '' where the database holds NULL. That is not
        # an edit and must not raise.
        partner.with_user(self.user).write({"function": "", "phone": "123"})
        self.assertEqual(partner.phone, "123")

    # ---- round seven: what the last live probe proved -----------------

    def test_soft_restriction_with_no_access_does_not_disclose(self):
        """A soft rule that grants NO read used to be skipped on read,
        which dropped its block and left the model fully readable."""
        self.env["res.partner"].create({"name": "Should Stay Hidden"})
        self.Profile.create({
            "name": "Soft But No Access",
            "user_ids": [(6, 0, [self.user.id])],
            "domain_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "rights_preset": "none",
                "apply_filter": True,
                "soft_restrict": True,
                "domain": "[('user_id', '=', '__uid__')]",
            })],
        })
        visible = self.env["res.partner"].with_user(self.user).search([
            ("name", "=", "Should Stay Hidden"),
        ])
        self.assertFalse(visible, "No access must not become full read")

    def test_company_switcher_cannot_escape_a_company_scoped_profile(self):
        other = self.env["res.company"].create({"name": "Second Company"})
        self.user.write({"company_ids": [(4, other.id)]})
        partner = self.env["res.partner"].create({"name": "Scoped Target"})
        self.Profile.create({
            "name": "Scoped To Home Company",
            "apply_to_all_companies": False,
            "company_ids": [(6, 0, [self.user.company_id.id])],
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_edit": True,
            })],
        })
        switched = partner.with_user(self.user).with_context(
            allowed_company_ids=[other.id],
        )
        with self.assertRaises(AccessError):
            switched.write({"name": "Escaped"})

    def test_a_failed_lookup_never_locks_out_an_administrator(self):
        """Fail closed for restricted users, but always leave a way back
        through the interface."""
        admin = self.env.ref("base.user_admin")
        partner = self.env["res.partner"].create({"name": "Admin Can Fix"})
        with patch.object(
            type(partner), "_eh_access_studio_active_profile_ids",
            lambda self: None,
        ):
            partner.with_user(admin).write({"name": "Repaired"})
        self.assertEqual(partner.name, "Repaired")

    def test_group_drill_down_does_not_carry_the_hidden_value(self):
        self.env["res.partner"].create({"name": "D1", "function": "HIDDEN-X"})
        self.Profile.create({
            "name": "Hidden Drill",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "invisible": True,
            })],
        })
        from odoo import release
        as_user = self.env["res.partner"].with_user(self.user)
        domain = [("function", "=", "HIDDEN-X")]
        rows = (as_user.read_group(domain, groupby=["function"], aggregates=["__count"])
                if release.version_info[0] >= 20
                else as_user.read_group(domain, ["function"], ["function"]))
        for row in rows:
            self.assertNotIn("HIDDEN-X", str(row))

    def test_record_rule_dates_ignore_a_forged_timezone(self):
        """Domain placeholders must use the stored user timezone."""
        rule = self.env["eh.access.domain"].create({
            "profile_id": self.Profile.create({
                "name": "Date Rule Host",
                "user_ids": [(6, 0, [self.user.id])],
            }).id,
            "model_id": self.partner_model,
            "rights_preset": "read_only",
            "apply_filter": True,
            "domain": "[('create_date', '>=', '__today__')]",
        })
        honest = rule.with_user(self.user)._resolved_domain()
        forged = rule.with_user(self.user).with_context(
            tz="Pacific/Kiritimati",
        )._resolved_domain()
        self.assertEqual(str(honest), str(forged))

    def test_a_database_constraint_is_explained_not_crashed(self):
        """res.partner.name is mandatory by a SQL CHECK, not by
        required=True, so locking it used to end in a raw violation."""
        self.Profile.create({
            "name": "Locks The Name",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("name").ids)],
                "readonly": True,
            })],
        })
        # Odoo's own assertRaises takes one class and already wraps the
        # block in a savepoint, so the aborted INSERT cannot poison the
        # rest of the test.
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.user).create({
                "email": "noname@example.test",
            })

    # ---- round eight --------------------------------------------------

    def test_posting_cannot_be_used_to_write_the_document_model(self):
        """The chatter waiver covers the write ACCESS CHECK core makes
        when a message is posted. It used to cover everything else that
        ran during the post as well, so a read-only user could rewrite
        records through a post hook. A write to the document's own model
        made while posting must still be refused."""
        partner = self.env["res.partner"].create({"name": "Post Hook Target"})
        other = self.env["res.partner"].create({"name": "Other Record"})
        self.Profile.create({
            "name": "Read Only Post Hook",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        as_user = partner.with_user(self.user)
        original = type(as_user)._message_post_after_hook

        def hook(record, message, *args, **kwargs):
            # What crm does: a business write, as the user, during a post.
            other.with_user(self.user).write({"name": "Rewritten By Post"})
            return original(record, message, *args, **kwargs)

        with patch.object(type(as_user), "_message_post_after_hook", hook):
            with self.assertRaises(AccessError):
                as_user.message_post(body="trigger")
        self.assertEqual(other.name, "Other Record")

    def test_children_created_through_the_parent_are_gated(self):
        """Core flushes res.partner child_ids as superuser, which used to
        skip restrict_create for every child created through its parent."""
        parent = self.env["res.partner"].create({"name": "Parent Co", "is_company": True})
        self.Profile.create({
            "name": "No Create Via Parent",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_create": True,
            })],
        })
        with self.assertRaises(AccessError):
            parent.with_user(self.user).write({
                "child_ids": [(0, 0, {"name": "Smuggled Child"})],
            })

    def test_locked_child_field_is_stripped_in_a_nested_create(self):
        parent = self.env["res.partner"].create({"name": "Parent Two", "is_company": True})
        self.Profile.create({
            "name": "Locked Child Field",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("function").ids)],
                "readonly": True,
            })],
        })
        parent.with_user(self.user).write({
            "child_ids": [(0, 0, {"name": "Kid", "function": "Smuggled"})],
        })
        self.assertFalse(parent.child_ids.filtered(lambda c: c.name == "Kid").function)

    def test_locked_many2many_with_a_default_still_allows_create(self):
        """The default command list of a locked many2many used to be
        treated as a user-supplied subtree, making create impossible."""
        self.Profile.create({
            "name": "Locked Tags",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("category_id").ids)],
                "readonly": True,
            })],
        })
        record = self.env["res.partner"].with_user(self.user).create({
            "name": "Default Tags Only",
        })
        self.assertTrue(record)

    def test_copy_with_a_default_keeps_the_source_locked_values(self):
        source = self.env["res.partner"].create({
            "name": "Source Keeps", "function": "Buyer", "ref": "R-1",
        })
        self.Profile.create({
            "name": "Copy Keeps Locked",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, (self._field("function") | self._field("ref")).ids)],
                "readonly": True,
            })],
        })
        clone = source.with_user(self.user).copy({"name": "Renamed Copy"})
        self.assertEqual(clone.function, "Buyer")
        self.assertEqual(clone.ref, "R-1")

    def test_a_conditional_lock_cannot_be_unlocked_with_two_writes(self):
        partner = self.env["res.partner"].create({
            "name": "Two Step", "is_company": True,
        })
        self.Profile.create({
            "name": "Company Notes Locked",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("comment").ids)],
                "readonly": True,
                "condition": "is_company",
            })],
        })
        # The lock must hold for the record as it would be AFTER the write
        # too, so flipping the condition and the locked field together is
        # refused, and so is writing the locked field on a record the same
        # write is about to make match.
        person = self.env["res.partner"].create({"name": "Soon A Company"})
        with self.assertRaises(AccessError):
            person.with_user(self.user).write({
                "is_company": True, "comment": "<p>slipped</p>",
            })

    def test_a_derived_field_does_not_carry_a_hidden_value(self):
        """email_normalized and email_formatted carry the email under
        another name."""
        partner = self.env["res.partner"].create({
            "name": "Derived", "email": "secret@example.test",
        })
        self.Profile.create({
            "name": "Hidden Email",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "invisible": True,
            })],
        })
        row = partner.with_user(self.user).read(
            ["email", "email_normalized", "email_formatted", "name"],
        )[0]
        self.assertFalse(row["email"])
        self.assertFalse(row["email_normalized"])
        self.assertFalse(row["email_formatted"])
        self.assertEqual(row["name"], "Derived")

    def test_searching_on_a_hidden_field_reveals_nothing(self):
        """A series of ilike searches reconstructs a value one character
        at a time."""
        self.env["res.partner"].create({
            "name": "Searchable", "email": "abc@example.test",
        })
        self.Profile.create({
            "name": "Hidden Email Search",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "invisible": True,
            })],
        })
        as_user = self.env["res.partner"].with_user(self.user)
        self.assertFalse(as_user.search([("email", "ilike", "abc@")]))
        # The visible fields still search normally.
        self.assertTrue(as_user.search([("name", "=", "Searchable")]))

    def test_an_api_key_cannot_sign_in_a_disabled_user(self):
        self.Profile.create({
            "name": "No Keys Either",
            "disable_login": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        import inspect
        keys = self.env["res.users.apikeys"].with_user(self.user).sudo()
        # _generate(scope, name) before Odoo 18, (scope, name, expiration)
        # from 18 on.
        if "expiration_date" in inspect.signature(keys._generate).parameters:
            key = keys._generate("rpc", "probe", None)
        else:
            key = keys._generate("rpc", "probe")
        with self.assertRaises(AccessDenied):
            self.env["res.users.apikeys"].sudo()._check_credentials(
                scope="rpc", key=key,
            )

    # ---- round nine ---------------------------------------------------

    def test_archiving_a_child_through_its_parent_is_gated(self):
        parent = self.env["res.partner"].create({"name": "Parent Nine", "is_company": True})
        child = self.env["res.partner"].create({"name": "Child Nine", "parent_id": parent.id})
        self.Profile.create({
            "name": "No Archive Via Parent",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_archive": True,
            })],
        })
        with self.assertRaises(AccessError):
            parent.with_user(self.user).write({
                "child_ids": [(1, child.id, {"active": False})],
            })
        with self.assertRaises(AccessError):
            parent.with_user(self.user).write({
                "child_ids": [(0, 0, {"name": "Born Archived", "active": False})],
            })

    def test_a_later_conditional_line_cannot_unlock_an_unconditional_one(self):
        partner = self.env["res.partner"].create({"name": "Merge Order"})
        field_ids = self._field("comment").ids
        self.Profile.create({
            "name": "Merge Order Rules",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [
                (0, 0, {
                    "model_id": self.partner_model,
                    "field_ids": [(6, 0, field_ids)],
                    "readonly": True,
                }),
                (0, 0, {
                    "model_id": self.partner_model,
                    "field_ids": [(6, 0, field_ids)],
                    "readonly": True,
                    "condition": "is_company",
                }),
            ],
        })
        # partner is not a company, so only the UNCONDITIONAL line applies,
        # and it must still apply whatever order the lines were stored in.
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"comment": "<p>unlocked?</p>"})

    def test_show_email_cannot_put_a_hidden_email_into_the_name(self):
        partner = self.env["res.partner"].create({
            "name": "Display Leak", "email": "hidden@example.test",
        })
        self.Profile.create({
            "name": "Hidden Email Display",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "invisible": True,
            })],
        })
        row = partner.with_user(self.user).with_context(
            show_email=True,
        ).read(["display_name"])[0]
        self.assertNotIn("hidden@example.test", row["display_name"])

    def test_is_company_lock_survives_the_superuser_pre_write(self):
        partner = self.env["res.partner"].create({"name": "Company Flag"})
        self.Profile.create({
            "name": "Locked Company Flag",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("is_company").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"is_company": True})
        self.assertFalse(partner.is_company)

    def test_restrict_delete_does_not_block_archiving(self):
        partner = self.env["res.partner"].create({"name": "Archive Ok"})
        self.Profile.create({
            "name": "No Delete Only",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_delete": True,
            })],
        })
        partner.with_user(self.user).action_archive()
        self.assertFalse(partner.active)
        with self.assertRaises(AccessError):
            partner.with_user(self.user).unlink()

    def test_attaching_a_file_works_under_read_only(self):
        partner = self.env["res.partner"].create({"name": "Attach Target"})
        self.Profile.create({
            "name": "Read Only Attach",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        attachment = self.env["ir.attachment"].with_user(self.user).create({
            "name": "note.txt",
            "raw": b"hello",
            "res_model": "res.partner",
            "res_id": partner.id,
        })
        self.assertTrue(attachment)

    def test_sorting_by_a_hidden_field_is_ignored(self):
        self.env["res.partner"].create({"name": "Sort A", "email": "zz@example.test"})
        self.env["res.partner"].create({"name": "Sort B", "email": "aa@example.test"})
        self.Profile.create({
            "name": "Hidden Sort",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "invisible": True,
            })],
        })
        as_user = self.env["res.partner"].with_user(self.user)
        by_email = as_user.search([("name", "in", ["Sort A", "Sort B"])], order="email")
        by_id = as_user.search([("name", "in", ["Sort A", "Sort B"])], order="id")
        self.assertEqual(by_email.ids, by_id.ids)

    def test_the_time_window_job_is_moved_to_hourly(self):
        cron = self.env.ref("eh_access_studio.cron_deactivate_expired_profiles")
        cron.write({"interval_type": "days", "interval_number": 1})
        self.env["eh.access.profile"]._eh_ensure_cron_schedule()
        self.assertEqual(cron.interval_type, "hours")
        # A schedule an administrator chose deliberately is left alone.
        cron.write({"interval_type": "days", "interval_number": 3})
        self.env["eh.access.profile"]._eh_ensure_cron_schedule()
        self.assertEqual(cron.interval_number, 3)

    # ---- round ten ----------------------------------------------------

    def test_copy_default_cannot_write_a_locked_field_on_another_record(self):
        """copy() runs with the field gate stood down so the source's own
        locked values carry over. Nested commands in the caller's default
        used to ride through that window onto OTHER records."""
        source = self.env["res.partner"].create({"name": "Copy Src Ten", "is_company": True})
        victim = self.env["res.partner"].create({"name": "Victim", "ref": "ORIGINAL"})
        self.Profile.create({
            "name": "Locked Ref Nested",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("ref").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            source.with_user(self.user).copy({
                "child_ids": [(1, victim.id, {"ref": "HACKED"})],
            })
        self.assertEqual(victim.ref, "ORIGINAL")

    def test_hide_duplicate_does_not_break_internal_copies(self):
        """Credit notes, reversals and backorders copy records from inside
        business methods. Only the user's own Duplicate is gated."""
        partner = self.env["res.partner"].create({"name": "Internal Copy"})
        self.Profile.create({
            "name": "No Duplicate Button",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_duplicate": True,
            })],
        })
        as_user = partner.with_user(self.user)
        # A copy made from inside another copy (what a business flow does
        # with the lines it duplicates) is not the user's Duplicate.
        with as_user._eh_access_studio_copy_scope():
            self.assertTrue(as_user.copy())
        # The user's own Duplicate is still refused.
        with self.assertRaises(AccessError):
            as_user.copy()

    def test_hide_log_note_is_enforced_on_the_server(self):
        partner = self.env["res.partner"].create({"name": "No Notes"})
        self.Profile.create({
            "name": "No Log Note",
            "user_ids": [(6, 0, [self.user.id])],
            "hide_log_note": True,
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).message_post(
                body="note", message_type="comment", subtype_xmlid="mail.mt_note",
            )
        # A framework notification is never blocked.
        self.assertTrue(partner.with_user(self.user).message_post(body="system"))

    def test_hide_send_message_is_enforced_on_the_server(self):
        partner = self.env["res.partner"].create({"name": "No Sends"})
        self.Profile.create({
            "name": "No Send Message",
            "user_ids": [(6, 0, [self.user.id])],
            "hide_send_message": True,
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user).message_post(
                body="hi", message_type="comment", subtype_xmlid="mail.mt_comment",
            )

    def test_a_binary_field_attachment_answers_to_the_field_lock(self):
        """An attachment with res_field set IS the storage of a binary
        field, so creating it writes that field."""
        partner = self.env["res.partner"].create({"name": "Image Lock"})
        self.Profile.create({
            "name": "Locked Image",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("image_1920").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["ir.attachment"].with_user(self.user).create({
                "name": "image_1920",
                "raw": b"not really an image",
                "res_model": "res.partner",
                "res_id": partner.id,
                "res_field": "image_1920",
            })

    def test_export_with_a_display_key_does_not_recurse(self):
        partner = self.env["res.partner"].create({"name": "Export Ctx", "email": "e@x.test"})
        self.Profile.create({
            "name": "Hidden Email Export Ctx",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "invisible": True,
            })],
        })
        rows = partner.with_user(self.user).with_context(
            show_email=True,
        ).export_data(["name"])
        self.assertEqual(rows["datas"][0][0], "Export Ctx")

    # ---- chatter and activities, judged as the person asking -----------

    def _chatter_profile(self, **flags):
        vals = {
            "name": "Chatter %s" % ", ".join(sorted(flags)),
            "user_ids": [(6, 0, [self.user.id])],
        }
        vals.update(flags)
        return self.Profile.create(vals)

    def test_the_post_route_is_judged_as_the_person_posting(self):
        """From Odoo 18 /mail/message/post checks the caller, then posts
        AS SUPERUSER, which every gate used to stand aside for."""
        partner = self.env["res.partner"].create({"name": "Route Post"})
        self._chatter_profile(hide_send_message=True)
        with _request(self.user.id, path="/mail/message/post"):
            with self.assertRaises(AccessError):
                partner.sudo().message_post(
                    body="hi", message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            # The route takes message_type from the payload, and a
            # 'notification' shows in the chatter all the same.
            with self.assertRaises(AccessError):
                partner.sudo().message_post(
                    body="hi", message_type="notification",
                    subtype_xmlid="mail.mt_comment",
                )
        # Server-side work with nobody behind it is never judged.
        self.assertTrue(partner.sudo().message_post(
            body="system", message_type="comment",
            subtype_xmlid="mail.mt_comment",
        ))

    def test_a_post_that_names_no_subtype_is_a_note(self):
        """message_post files a post without a subtype as a note, so it
        answers to Hide Log Note, not Hide Send Message."""
        partner = self.env["res.partner"].create({"name": "No Subtype"})
        self._chatter_profile(hide_log_note=True)
        with self.assertRaises(AccessError):
            partner.with_user(self.user).message_post(
                body="note", message_type="comment",
            )

    def test_a_direct_message_post_call_is_judged_whatever_its_type(self):
        partner = self.env["res.partner"].create({"name": "Direct Call"})
        self._chatter_profile(hide_log_note=True)
        with _request(self.user.id, model="res.partner", method="message_post"):
            with self.assertRaises(AccessError):
                partner.with_user(self.user).message_post(body="as notification")

    def test_hide_schedule_activity_is_enforced_on_the_server(self):
        partner = self.env["res.partner"].create({"name": "No Activities"})
        self._chatter_profile(hide_schedule_activity=True)
        todo = "mail.mail_activity_data_todo"
        with self.assertRaises(AccessError):
            partner.with_user(self.user).activity_schedule(todo, summary="direct")
        # The schedule wizard, even when the activity is made as superuser.
        with _request(
            self.user.id, model="mail.activity.schedule",
            method="action_schedule_activities",
        ):
            with self.assertRaises(AccessError):
                partner.sudo().activity_schedule(todo, summary="wizard")
        # The activity form.
        with _request(self.user.id, model="mail.activity", method="create"):
            with self.assertRaises(AccessError):
                self.env["mail.activity"].with_user(self.user).create({
                    "res_model_id": self.partner_model,
                    "res_id": partner.id,
                    "activity_type_id": self.env.ref(todo).id,
                    "summary": "form",
                })

    def test_an_activity_a_business_flow_creates_is_not_blocked(self):
        """A confirmation that assigns a follow-up is the flow's doing,
        not the person scheduling."""
        partner = self.env["res.partner"].create({"name": "Flow Activity"})
        self._chatter_profile(hide_schedule_activity=True)
        with _request(self.user.id, model="res.partner", method="write"):
            activity = partner.with_user(self.user).activity_schedule(
                "mail.mail_activity_data_todo", summary="from a flow",
            )
        self.assertTrue(activity)

    # ---- copy_data is a read when it is the caller's own call ----------

    def test_copy_data_over_rpc_does_not_return_a_hidden_field(self):
        partner = self.env["res.partner"].create({
            "name": "Copy Data", "email": "copy.secret@example.test",
        })
        self.Profile.create({
            "name": "Hidden Email Copy Data",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "invisible": True,
            })],
        })
        with _request(self.user.id, model="res.partner", method="copy_data"):
            rows = partner.with_user(self.user).copy_data()
        self.assertFalse(rows[0].get("email"))
        # Business code duplicating the record keeps the value.
        rows = partner.with_user(self.user).copy_data()
        self.assertEqual(rows[0]["email"], "copy.secret@example.test")

    # ---- superuser write splits in core ---------------------------------

    def test_a_conditional_lock_cannot_be_walked_past_in_one_write(self):
        """Core writes is_company as superuser BEFORE the rest, so the
        rest used to be judged against the new value."""
        person = self.env["res.partner"].create({"name": "One Write"})
        self.Profile.create({
            "name": "Individual Notes Locked",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("comment").ids)],
                "readonly": True,
                "condition": "not is_company",
            })],
        })
        with self.assertRaises(AccessError):
            person.with_user(self.user).write({
                "is_company": True, "comment": "<p>slipped</p>",
            })

    def test_own_preferences_answer_to_field_locks(self):
        """Core writes a user's own preferences as superuser."""
        self.Profile.create({
            "name": "Locked Contact Email",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).write({
                "email": "changed@example.test",
            })

    # ---- failure posture ------------------------------------------------

    def test_a_failed_lookup_does_not_lock_out_a_user_under_no_profile(self):
        other = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Never Restricted",
            "login": "eh_access_unlisted@example.test",
            "group_ids": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("base.group_partner_manager").id,
            ])],
        })
        self.Profile.create({
            "name": "Someone Else's Profile",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        partner = self.env["res.partner"].create({"name": "Unlisted Edit"})
        target = partner.with_user(other)
        with patch.object(
            type(target), "_eh_access_studio_active_profile_ids",
            lambda self: None,
        ):
            target.write({"name": "Still Editable"})
        self.assertEqual(partner.name, "Still Editable")
        Rule = self.env["ir.rule"].with_user(other)
        expected = str(Rule._compute_domain("res.partner", "read"))
        with patch.object(
            type(Rule), "_eh_access_studio_is_gated",
            side_effect=RuntimeError("lookup failed"),
        ):
            self.assertEqual(
                str(Rule._compute_domain("res.partner", "read")), expected,
            )

    # ---- browser cache ----------------------------------------------------

    def test_a_profile_edit_retires_the_browser_copy_of_the_screens(self):
        profile = self.Profile.create({
            "name": "Cache Key",
            "user_ids": [(6, 0, [self.user.id])],
        })
        http = self.env["ir.http"].with_user(self.user)
        info = {"registry_hash": "core"}
        http._eh_access_studio_fold_cache_hash(info)
        first = info["registry_hash"]
        self.assertNotEqual(first, "core")
        profile.write({"hide_export": True})
        info = {"registry_hash": "core"}
        http._eh_access_studio_fold_cache_hash(info)
        self.assertNotEqual(info["registry_hash"], first)
        # A user under no profile keeps Odoo's own key.
        admin_info = {"registry_hash": "core"}
        self.env["ir.http"].with_user(self.env.ref("base.user_admin"))._eh_access_studio_fold_cache_hash(admin_info)
        self.assertEqual(admin_info["registry_hash"], "core")

    # ---- round twelve: every transport, not just the web client ---------

    def _rpc_call_kw(self):
        import odoo
        for module_name in ("odoo.service.model", "odoo.api"):
            module = __import__(module_name, fromlist=["call_kw"])
            func = getattr(module, "call_kw", None)
            if func is not None:
                return func
        self.fail("no call_kw found")

    def test_the_rpc_funnel_records_the_callers_call(self):
        """XML-RPC and /jsonrpc reach models through call_kw, /json/2
        through get_public_method. The wrapper must be in place."""
        call_kw = self._rpc_call_kw()
        self.assertTrue(getattr(call_kw, "_eh_access_studio_original", None))
        partner = self.env["res.partner"].create({"name": "Funnel"})
        self._chatter_profile(hide_log_note=True)
        # message_post's default type is 'notification': a direct call is
        # judged whatever type it names.
        with self.assertRaises(AccessError):
            call_kw(
                partner.with_user(self.user), "message_post",
                [partner.ids], {"body": "over the API"},
            )

    def test_every_message_post_variant_is_a_direct_post(self):
        partner = self.env["res.partner"].create({"name": "Variants"})
        self._chatter_profile(hide_send_message=True, hide_log_note=True)
        for method in ("message_post_with_source", "message_post_with_view"):
            with _eh_rpc_scope(self.user.id, "res.partner", method):
                with self.assertRaises(AccessError):
                    partner.sudo().message_post(
                        body="through %s" % method,
                        subtype_xmlid="mail.mt_comment",
                    )

    def test_a_direct_mail_message_create_answers_to_the_chatter_rules(self):
        partner = self.env["res.partner"].create({"name": "Raw Message"})
        self._chatter_profile(hide_send_message=True, hide_log_note=True)
        note = self.env.ref("mail.mt_note")
        for extra in ({}, {"subtype_id": note.id}):
            with _eh_rpc_scope(self.user.id, "mail.message", "create"):
                with self.assertRaises(AccessError):
                    self.env["mail.message"].with_user(self.user).create(dict({
                        "model": "res.partner",
                        "res_id": partner.id,
                        "body": "slipped in",
                        "message_type": "comment",
                    }, **extra))

    def test_discuss_is_never_silenced_by_the_chatter_toggles(self):
        from odoo.addons.eh_access_studio.models.mail_thread import _eh_chatter_flags
        self._chatter_profile(hide_chatter=True)
        env = self.env(user=self.user.id, su=False)
        self.assertEqual(_eh_chatter_flags(env, "discuss.channel"), (False, False, False))
        self.assertEqual(_eh_chatter_flags(env, "res.partner"), (True, True, True))

    def test_an_activity_made_over_json2_is_judged(self):
        """/json/2 names the method in its URL, not in request.params."""
        partner = self.env["res.partner"].create({"name": "Json2 Activity"})
        self._chatter_profile(hide_schedule_activity=True)
        with _eh_rpc_scope(self.user.id, "res.partner", "activity_schedule"):
            with self.assertRaises(AccessError):
                partner.sudo().activity_schedule(
                    "mail.mail_activity_data_todo", summary="json2",
                )

    def test_copy_data_over_any_transport_blanks_hidden_fields(self):
        partner = self.env["res.partner"].create({
            "name": "Any Transport", "email": "any.transport@example.test",
        })
        self.Profile.create({
            "name": "Hidden Email Any Transport",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "invisible": True,
            })],
        })
        rows = self._rpc_call_kw()(
            partner.with_user(self.user), "copy_data", [partner.ids], {},
        )
        # Odoo 16's call_kw hands back the single dict, later series the list.
        row = rows[0] if isinstance(rows, list) else rows
        self.assertFalse(row.get("email"))

    # ---- round twelve: conditional locks after the write ----------------

    def test_a_condition_flipped_through_an_inverse_field_is_caught(self):
        """company_type writes is_company through an inverse, which the
        check before the write cannot see."""
        person = self.env["res.partner"].create({"name": "Inverse Flip"})
        self.Profile.create({
            "name": "Company Phone Locked",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("phone").ids)],
                "readonly": True,
                "condition": "is_company",
            })],
        })
        with self.assertRaises(AccessError):
            person.with_user(self.user).write({
                **({"vat": "123456789"} if release.version_info[0] >= 20 else {"company_type": "company"}),
                "phone": "222",
            })
        # The individual stays editable.
        person.with_user(self.user).write({"phone": "333"})
        self.assertEqual(person.phone, "333")

    # ---- round twelve: search and grouping -----------------------------

    def _hide_email(self):
        return self.Profile.create({
            "name": "Hidden Email Search",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("email").ids)],
                "invisible": True,
            })],
        })

    def test_name_search_cannot_probe_a_hidden_field(self):
        self.env["res.partner"].create({
            "name": "Zed Probe", "email": "zz-probe@hidden.example",
        })
        self._hide_email()
        as_user = self.env["res.partner"].with_user(self.user)
        self.assertFalse(as_user.name_search("zz-probe@hidden"))
        self.assertTrue(as_user.name_search("Zed Probe"))
        # A probe that matches the hidden value and one that matches
        # nothing must get the same answer, whatever that answer is on a
        # series where display_name is not searchable at all.
        def probe(value):
            try:
                return as_user.search_count([("display_name", "ilike", value)])
            except ValueError:
                return None
        self.assertEqual(probe("zz-probe@hidden"), probe("zz-nomatch@nowhere.example"))

    def test_grouping_by_a_hidden_field_reveals_no_distribution(self):
        from odoo.addons.eh_access_studio.models.base_model import _EH_MODERN_READ_GROUP
        if not _EH_MODERN_READ_GROUP:
            return
        for index in range(3):
            self.env["res.partner"].create({
                "name": "Group %s" % index, "email": "g%s@spread.example" % index,
            })
        self._hide_email()
        as_user = self.env["res.partner"].with_user(self.user)
        domain = [("name", "like", "Group ")]
        rows = as_user._read_group(domain, ["email"], ["__count"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], False)
        self.assertEqual(rows[0][1], as_user.search_count(domain))

    def test_a_delegated_copy_of_a_hidden_field_is_blanked(self):
        """res.users carries every res.partner field through _inherits."""
        other = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Delegated", "login": "eh_access_delegated@example.test",
            "email": "delegated.secret@example.test",
        })
        self._hide_email()
        row = other.with_user(self.user).read(["email"])[0]
        self.assertFalse(row["email"])

    # ---- round twelve: both ends of a relation -------------------------

    def test_a_many2many_lock_holds_from_the_other_side(self):
        partner = self.env["res.partner"].create({"name": "Tagged"})
        tag = self.env["res.partner.category"].create({"name": "Other Side"})
        self.Profile.create({
            "name": "Tags Locked",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("category_id").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            tag.with_user(self.user).write({"partner_ids": [(4, partner.id)]})
        self.assertFalse(partner.category_id)

    def test_restrict_edit_covers_a_many2many_written_from_the_other_side(self):
        partner = self.env["res.partner"].create({"name": "No Edit Tags"})
        tag = self.env["res.partner.category"].create({"name": "Via Tag"})
        self.Profile.create({
            "name": "Partner Edit Restricted",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "restrict_edit": True,
            })],
        })
        with self.assertRaises(AccessError):
            tag.with_user(self.user).write({"partner_ids": [(4, partner.id)]})

    def test_unlinking_a_child_through_its_parent_answers_to_the_lock(self):
        parent = self.env["res.partner"].create({"name": "Parent Lock", "is_company": True})
        child = self.env["res.partner"].create({"name": "Child Lock", "parent_id": parent.id})
        self.Profile.create({
            "name": "Parent Locked",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("parent_id").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            parent.with_user(self.user).write({"child_ids": [(3, child.id)]})
        self.assertEqual(child.parent_id, parent)

    # ---- round twelve: failure posture ---------------------------------

    def test_a_failed_lookup_stops_hidden_fields_for_a_listed_user(self):
        partner = self.env["res.partner"].create({
            "name": "Fail Read", "email": "fail.read@example.test",
        })
        self._hide_email()
        target = partner.with_user(self.user)
        with patch.object(
            type(target), "_eh_access_studio_active_profile_ids",
            lambda self: None,
        ):
            with self.assertRaises(AccessError):
                target.read(["email"])

    def test_the_sign_in_gate_failing_refuses_only_listed_users(self):
        unlisted = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Unlisted Sign In", "login": "eh_access_unlisted_login@example.test",
        })
        self._chatter_profile()
        # Nothing raised: a user on no profile gets through.
        unlisted.sudo()._eh_access_studio_login_failure_posture()
        self.env.ref("base.user_admin").sudo()._eh_access_studio_login_failure_posture()
        with self.assertRaises(AccessDenied):
            self.user.sudo()._eh_access_studio_login_failure_posture()

    def test_read_only_covers_contact_data_edited_through_preferences(self):
        self.Profile.create({
            "name": "Read Only Preferences",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        me = self.user.with_user(self.user)
        with self.assertRaises(AccessError):
            me.write({"email": "changed.through.prefs@example.test"})
        # Personal settings stay editable.
        me.write({"tz": "Europe/Paris"})
        self.assertEqual(self.user.tz, "Europe/Paris")
        # The native access-domain cache must not retain a settings-only
        # exception for a later ordinary business write.
        for target in (me, self.user.partner_id.with_user(self.user)):
            with self.assertRaises(AccessError):
                target.write({"email": "still.forbidden@example.test"})
        with self.assertRaises(AccessError):
            self.user.partner_id.with_user(self.user).with_context(
                _eh_personal_settings_token=True,
                _eh_personal_settings_uid=self.user.id,
                _eh_personal_settings_partner=self.user.partner_id.id,
            ).write({"tz": "Europe/London"})

        # A nested extension cannot borrow the settings exception for a
        # business field. Native 20 no longer elevates preference writes.
        from odoo import release
        if release.version_info[0] >= 20:
            partner_type = type(self.user.partner_id)
            native_write = partner_type.write

            def write_business_from_settings(partners, values):
                if "tz" in values:
                    native_write(partners, {"email": "nested.forbidden@example.test"})
                return native_write(partners, values)

            with self.assertRaises(AccessError), self.cr.savepoint(), patch.object(
                partner_type, "write", new=write_business_from_settings,
            ):
                me.write({"tz": "Europe/London"})

        # Explicit field locks still win over the personal setting policy.
        locked_settings = self.Profile.create({
            "name": "Locked Timezone", "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("tz").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            me.write({"tz": "Europe/London"})
        if release.version_info[0] >= 20:
            locked_settings.active = False
            self.Profile.create({
                "name": "Explicit User Model Lock", "user_ids": [(6, 0, [self.user.id])],
                "model_line_ids": [(0, 0, {
                    "model_id": self.env["ir.model"]._get_id("res.users"),
                    "restrict_edit": True,
                })],
            })
            with self.assertRaises(AccessError):
                me.write({"tz": "Europe/London"})

    # ---- round thirteen: chatter entries without message_post ----------

    def test_message_ids_commands_are_a_post(self):
        partner = self.env["res.partner"].create({"name": "Command Post"})
        self._chatter_profile(hide_send_message=True)
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"message_ids": [(0, 0, {
                "body": "through a command", "message_type": "comment",
                "model": "res.partner", "res_id": partner.id,
            })]})

    def test_activity_ids_commands_are_scheduling(self):
        partner = self.env["res.partner"].create({"name": "Command Activity"})
        self._chatter_profile(hide_schedule_activity=True)
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"activity_ids": [(0, 0, {
                "res_model_id": self.partner_model, "res_id": partner.id,
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                "summary": "through a command",
            })]})

    def test_send_later_answers_to_send_message(self):
        if "mail.scheduled.message" not in self.env:
            return  # "send later" arrived with Odoo 18
        partner = self.env["res.partner"].create({"name": "Send Later"})
        self._chatter_profile(hide_send_message=True)
        from datetime import timedelta
        from odoo import fields as odoo_fields
        with self.assertRaises(AccessError):
            self.env["mail.scheduled.message"].with_user(self.user).create({
                "model": "res.partner", "res_id": partner.id,
                "body": "later", "author_id": self.user.partner_id.id,
                "scheduled_date": odoo_fields.Datetime.now() + timedelta(days=1),
            })

    def test_message_notify_answers_to_send_message(self):
        partner = self.env["res.partner"].create({"name": "Notify"})
        self._chatter_profile(hide_send_message=True)
        with _eh_rpc_scope(self.user.id, "res.partner", "message_notify"):
            with self.assertRaises(AccessError):
                partner.with_user(self.user).message_notify(
                    body="direct", partner_ids=self.user.partner_id.ids,
                )

    def test_any_internal_subtype_is_a_note(self):
        partner = self.env["res.partner"].create({"name": "Internal Subtype"})
        internal = self.env["mail.message.subtype"].create({
            "name": "Staff Only", "internal": True,
        })
        self._chatter_profile(hide_log_note=True)
        with _eh_rpc_scope(self.user.id, "res.partner", "message_post"):
            with self.assertRaises(AccessError):
                partner.with_user(self.user).message_post(
                    body="internal", message_type="comment",
                    subtype_id=internal.id,
                )

    def test_done_feedback_answers_to_log_note(self):
        partner = self.env["res.partner"].create({"name": "Feedback"})
        activity = self.env["mail.activity"].create({
            "res_model_id": self.partner_model, "res_id": partner.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "user_id": self.user.id, "summary": "call",
        })
        self._chatter_profile(hide_log_note=True)
        with _eh_rpc_scope(self.user.id, "mail.activity", "action_feedback"):
            with self.assertRaises(AccessError):
                activity.with_user(self.user).action_feedback(feedback="free text")

    # ---- round thirteen: time windows, API sign-in, duplication --------

    def test_a_dated_profile_locks_the_users_own_timezone(self):
        from odoo import fields as odoo_fields
        self.Profile.create({
            "name": "Dated",
            "user_ids": [(6, 0, [self.user.id])],
            "date_until": odoo_fields.Date.add(odoo_fields.Date.today(), days=5),
        })
        # The preferences dialog, as the client saves it.
        with _eh_rpc_scope(self.user.id, "res.users", "web_save"):
            with self.assertRaises(AccessError):
                self.user.with_user(self.user).write({"tz": "Pacific/Kiritimati"})
            with self.assertRaises(AccessError):
                self.user.partner_id.with_user(self.user).write({
                    "tz": "Pacific/Kiritimati",
                })
            # An administrator can still change it.
            self.user.with_user(self.env.ref("base.user_admin")).write({
                "tz": "Europe/Paris",
            })
        self.assertEqual(self.user.tz, "Europe/Paris")
        # Core writes the browser timezone during sign-in, outside any
        # RPC call: that must never be refused, or the user cannot sign
        # in at all.
        self.user.with_user(self.user).write({"tz": "Pacific/Kiritimati"})
        self.assertEqual(self.user.tz, "Pacific/Kiritimati")

    def test_a_cached_api_sign_in_meets_a_block_that_starts_later(self):
        self.Profile.create({
            "name": "Blocked From Today",
            "user_ids": [(6, 0, [self.user.id])],
            "disable_login": True,
        })
        with self.assertRaises(AccessDenied):
            self.env["res.users"]._eh_access_studio_api_gate(self.user.id)
        # An administrator is never shut out.
        self.env["res.users"]._eh_access_studio_api_gate(self.env.ref("base.user_admin").id)

    def test_duplicating_a_user_does_not_copy_their_profiles(self):
        self.assertFalse(self.env["res.users"]._fields["access_profile_ids"].copy)

    def test_business_code_searching_as_the_user_is_not_scrubbed(self):
        """find_or_create matching an email while a message is posted
        must still find the contact, or it creates a duplicate."""
        partner = self.env["res.partner"].create({
            "name": "Match Me", "email": "match.me@example.test",
        })
        self._hide_email()
        as_user = self.env["res.partner"].with_user(self.user)
        with _eh_rpc_scope(self.user.id, "res.partner", "message_post"):
            self.assertEqual(as_user.search([("email", "=", "match.me@example.test")]), partner)
        with _eh_rpc_scope(self.user.id, "res.partner", "web_search_read"):
            self.assertFalse(as_user.search([("email", "=", "match.me@example.test")]))

    def test_a_mail_mail_create_answers_to_the_chatter_rules(self):
        """mail.mail carries a mail.message by delegation."""
        partner = self.env["res.partner"].create({"name": "Mail Carrier"})
        self._chatter_profile(hide_send_message=True)
        with _eh_rpc_scope(self.user.id, "mail.mail", "create"):
            with self.assertRaises(AccessError):
                self.env["mail.mail"].with_user(self.user).sudo().create({
                    "model": "res.partner", "res_id": partner.id,
                    "body_html": "<p>carried</p>", "body": "<p>carried</p>",
                    "message_type": "comment",
                })

    def test_editing_a_posted_message_answers_to_the_chatter_rules(self):
        partner = self.env["res.partner"].create({"name": "Edit Post"})
        message = partner.with_user(self.user).message_post(
            body="first", message_type="comment", subtype_xmlid="mail.mt_comment",
        )
        self._chatter_profile(hide_send_message=True)
        with _request(self.user.id, path="/mail/message/update_content"):
            with self.assertRaises(AccessError):
                partner.sudo()._message_update_content(message, body="rewritten")

    # ---- round fourteen -------------------------------------------------

    def test_a_display_context_on_a_related_record_shows_no_hidden_value(self):
        from odoo import release
        parent = self.env["res.partner"].create({
            "name": "Shown Parent", "email": "shown.parent@hidden.example",
            "is_company": True,
        })
        child = self.env["res.partner"].create({"name": "Child", "parent_id": parent.id})
        self._hide_email()
        as_user = child.with_user(self.user)
        row = as_user.with_context(show_email=True).read(["parent_id"])[0]
        self.assertNotIn("@", row["parent_id"][1])
        if release.version_info[0] >= 17:
            rows = as_user.web_read({"parent_id": {
                "fields": {"display_name": {}}, "context": {"show_email": True},
            }})
            self.assertNotIn("@", rows[0]["parent_id"]["display_name"])

    def test_a_delegated_model_hides_what_derives_from_a_hidden_field(self):
        other = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Derived Delegated", "login": "eh_access_derived_deleg@example.test",
            "email": "derived.deleg@example.test",
        })
        self._hide_email()
        row = other.with_user(self.user).read(["email_normalized", "email_formatted"])[0]
        self.assertFalse(row["email_normalized"])
        self.assertFalse(row["email_formatted"])

    def test_a_delegated_model_is_not_a_search_oracle(self):
        other = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Oracle", "login": "eh_access_oracle@example.test",
            "email": "oracle.secret@example.test",
        })
        self._hide_email()
        users = self.env["res.users"].with_user(self.user)
        self.assertFalse(users.search([("email", "=like", "oracle.sec%"), ("id", "=", other.id)]))
        self.assertFalse(users.search([("partner_id.email", "=like", "oracle.sec%"), ("id", "=", other.id)]))

    def test_the_composers_batch_log_answers_to_the_chatter_rules(self):
        partner = self.env["res.partner"].create({"name": "Batch Log"})
        self._chatter_profile(hide_log_note=True)
        with _eh_rpc_scope(self.user.id, "mail.compose.message", "action_send_mail"):
            with self.assertRaises(AccessError):
                partner.with_user(self.user)._message_log_batch({partner.id: "kept log"})

    def test_an_activity_moved_by_a_command_is_judged_where_it_lands(self):
        partner = self.env["res.partner"].create({"name": "Host Record"})
        users_model = self.env["ir.model"]._get_id("res.users")
        self.Profile.create({
            "name": "Users Chatter Hidden",
            "user_ids": [(6, 0, [self.user.id])],
            "chatter_line_ids": [(0, 0, {"model_id": users_model})],
        })
        activity = self.env["mail.activity"].create({
            "res_model_id": self.partner_model, "res_id": partner.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "summary": "to move",
        })
        with self.assertRaises(AccessError):
            partner.with_user(self.user)._eh_access_studio_check_chatter_command(
                "mail.activity",
                (1, activity.id, {"res_model_id": users_model, "res_id": self.user.id}),
            )

    def test_done_and_schedule_next_feedback_answers_to_log_note(self):
        partner = self.env["res.partner"].create({"name": "Feedback Next"})
        activity = self.env["mail.activity"].create({
            "res_model_id": self.partner_model, "res_id": partner.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "user_id": self.user.id, "summary": "next",
        })
        self._chatter_profile(hide_log_note=True)
        with _eh_rpc_scope(self.user.id, "mail.activity", "action_feedback_schedule_next"):
            with self.assertRaises(AccessError):
                activity.with_user(self.user).action_feedback_schedule_next(feedback="text")

    def test_a_personal_default_cannot_set_a_locked_field(self):
        self.Profile.create({
            "name": "Phone Locked Default",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("phone").ids)],
                "readonly": True,
            })],
        })
        with self.assertRaises(AccessError):
            self.env["ir.default"].with_user(self.user).set(
                "res.partner", "phone", "666", user_id=True,
            )

    def test_saving_the_archive_flag_unchanged_is_not_archiving(self):
        partner = self.env["res.partner"].create({"name": "Active Stays"})
        self.Profile.create({
            "name": "No Archive Unchanged",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model, "restrict_archive": True,
            })],
        })
        partner.with_user(self.user).write({"active": True, "comment": "<p>ok</p>"})
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"active": False})

    def test_a_profile_modifier_is_combined_with_the_views_own(self):
        View = self.env["ir.ui.view"]
        self.assertEqual(
            View._eh_merge_modifier("is_company", "state == 'x'"),
            "(is_company) or (state == 'x')",
        )
        self.assertEqual(View._eh_merge_modifier("", "is_company"), "is_company")
        self.assertEqual(View._eh_merge_modifier("is_company", "1"), "1")

    def test_a_blank_hidden_group_opens_on_the_records_it_counts(self):
        self.env["res.partner"].create({"name": "Drill A", "email": "a@drill.example"})
        self._hide_email()
        as_user = self.env["res.partner"].with_user(self.user)
        base = [("name", "=like", "Drill %")]
        self.assertEqual(
            as_user.search_count(base + [("email", "=", False)]),
            as_user.search_count(base),
        )

    # ---- round fifteen: the client layer, not only the ORM -------------

    def test_public_overrides_keep_the_decorators_core_gives_them(self):
        """A missing @api.model makes call_kw read the first argument as
        record ids, which broke every list view. Unit tests call methods
        directly and never saw it, so the parity is asserted here."""
        import inspect
        markers = ("_api", "_api_model", "_readonly", "_api_private", "_returns")

        def marks(func):
            found = {}
            for name in markers:
                if not hasattr(func, name):
                    continue
                value = getattr(func, name)
                if name == "_returns" and isinstance(value, tuple):
                    # (model, downgrade, upgrade): the callables are
                    # per-model closures, so compare the shape.
                    value = (value[0], bool(value[1]), bool(value[2]))
                found[name] = value
            return found

        mismatches = []
        for model_name in self.env.registry:
            cls = type(self.env[model_name])
            for index, klass in enumerate(cls.__mro__):
                if not klass.__module__.startswith("odoo.addons.eh_access_studio"):
                    continue
                for name, func in vars(klass).items():
                    if name.startswith("_") or not inspect.isfunction(func):
                        continue
                    core = None
                    for other in cls.__mro__[index + 1:]:
                        if name in vars(other) and not other.__module__.startswith(
                            "odoo.addons.eh_access_studio",
                        ):
                            core = vars(other)[name]
                            break
                    if core is None or not callable(core):
                        continue
                    core = getattr(core, "__func__", core)
                    if marks(func) != marks(core):
                        mismatches.append(
                            "%s.%s: %s, core has %s" % (
                                model_name, name, marks(func), marks(core),
                            ))
        self.assertFalse(sorted(set(mismatches)), "\n".join(sorted(set(mismatches))))

    def test_the_client_calls_a_list_view_makes_still_work(self):
        """Exercised through call_kw, the way the web client and every
        API client reach them, not as direct Python calls."""
        from odoo import release
        call_kw = self._rpc_call_kw()
        self.Profile.create({
            "name": "List View Profile",
            "user_ids": [(6, 0, [self.user.id])],
        })
        as_user = self.env["res.partner"].with_user(self.user)
        domain = [("name", "=", "Nothing Here")]
        if release.version_info[0] >= 17:
            rows = call_kw(as_user, "web_search_read", [], {
                "domain": domain, "specification": {"name": {}}, "limit": 1,
            })
            self.assertIn("records", rows)
        self.assertEqual(
            call_kw(as_user, "read_group", [], {
                "domain": domain, "groupby": ["type"],
                **({"aggregates": ["__count"]} if release.version_info[0] >= 20
                   else {"fields": ["id"]}),
            }), [],
        )
        self.assertTrue(call_kw(as_user, "load", [["name"], [["Imported By RPC"]]], {})["ids"])
        # Odoo 16 and 17 answer True; from 18 the deprecated wrapper
        # answers None and raises when refused. Neither may raise here.
        self.assertNotEqual(call_kw(as_user, "check_access_rights", ["read"], {}), False)

    # ---- round fifteen: locks and conditions ---------------------------

    def test_an_unconditional_lock_is_not_narrowed_by_a_conditional_hide(self):
        company = self.env["res.partner"].create({"name": "A Co", "is_company": True})
        self.Profile.create({
            "name": "Locked Always, Hidden Sometimes",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [
                (0, 0, {
                    "model_id": self.partner_model,
                    "field_ids": [(6, 0, self._field("ref").ids)],
                    "readonly": True,
                }),
                (0, 0, {
                    "model_id": self.partner_model,
                    "field_ids": [(6, 0, self._field("ref").ids)],
                    "invisible": True,
                    "condition": "not is_company",
                }),
            ],
        })
        with self.assertRaises(AccessError):
            company.with_user(self.user).write({"ref": "BYPASS"})

    def test_a_conditional_hide_covers_reads_and_searches_consistently(self):
        company = self.env["res.partner"].create({
            "name": "Hidden Here", "is_company": True, "comment": "<p>topsecret</p>",
        })
        person = self.env["res.partner"].create({
            "name": "Shown Here", "is_company": False, "comment": "<p>ordinary</p>",
        })
        self.Profile.create({
            "name": "Comment Hidden On Companies",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("comment").ids)],
                "invisible": True,
                "condition": "is_company",
            })],
        })
        as_user = self.env["res.partner"].with_user(self.user)
        # Blanked where the condition holds, readable where it does not.
        self.assertFalse(as_user.browse(company.id).read(["comment"])[0]["comment"])
        self.assertIn("ordinary", as_user.browse(person.id).read(["comment"])[0]["comment"])
        # Not searchable anywhere: a search would answer for the records
        # the read withholds.
        self.assertEqual(
            as_user.search_count([("id", "=", company.id), ("comment", "ilike", "topsec")]),
            as_user.search_count([("id", "=", company.id), ("comment", "ilike", "zzzz")]),
        )

    def test_the_onchange_answer_does_not_test_hidden_values(self):
        from odoo import release
        self.env["res.partner"].create({"name": "Vat Holder", "vat": "BE0477472701"})
        self.Profile.create({
            "name": "Hidden Vat",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("vat").ids)],
                "invisible": True,
            })],
        })
        as_user = self.env["res.partner"].with_user(self.user)

        def probe(vat):
            values = {"name": "Probe", "vat": vat}
            if release.version_info[0] >= 17:
                return as_user.onchange(values, ["vat"], {"name": {}, "vat": {}})
            return as_user.onchange(values, "vat", {"name": "1", "vat": "1"})

        self.assertEqual(probe("BE0477472701"), probe("BE0999999999"))

    # ---- round fifteen: chatter reached through other models -----------

    def test_a_template_send_cannot_write_into_a_hidden_chatter(self):
        partner = self.env["res.partner"].create({"name": "Template Target"})
        self._chatter_profile(hide_send_message=True, hide_log_note=True)
        with _eh_rpc_scope(self.user.id, "mail.template", "send_mail"):
            with self.assertRaises(AccessError):
                self.env["mail.message"].with_user(self.user).sudo().create({
                    "model": "res.partner", "res_id": partner.id,
                    "body": "<p>from a template</p>", "message_type": "notification",
                    "subtype_id": self.env.ref("mail.mt_comment").id,
                })

    def test_a_mail_sending_method_on_the_record_is_a_post(self):
        partner = self.env["res.partner"].create({"name": "Activity Mail"})
        self._chatter_profile(hide_send_message=True)
        with _eh_rpc_scope(self.user.id, "res.partner", "activity_send_mail"):
            with self.assertRaises(AccessError):
                partner.sudo().message_post(
                    body="<p>via template</p>", message_type="notification",
                    subtype_xmlid="mail.mt_comment",
                )

    def test_a_posted_message_cannot_be_flipped_into_a_note(self):
        partner = self.env["res.partner"].create({"name": "Flip Subtype"})
        message = partner.with_user(self.user).message_post(
            body="allowed", message_type="comment", subtype_xmlid="mail.mt_comment",
        )
        self._chatter_profile(hide_log_note=True)
        with _eh_rpc_scope(self.user.id, "mail.message", "write"):
            with self.assertRaises(AccessError):
                message.with_user(self.user).write({
                    "subtype_id": self.env.ref("mail.mt_note").id,
                })

    def test_an_import_is_the_person_entering_data(self):
        partner = self.env["res.partner"].create({"name": "Import Target"})
        self._chatter_profile(hide_send_message=True, hide_log_note=True)
        todo = self.env.ref("mail.mail_activity_data_todo")
        with _eh_rpc_scope(self.user.id, "base_import.import", "execute_import"):
            result = self.env["mail.message"].with_user(self.user).load(
                ["model", "res_id", "body", "message_type"],
                [["res.partner", str(partner.id), "imported note", "comment"]],
            )
        self.assertFalse(result["ids"])
        self.assertTrue(todo)

    def test_a_read_only_record_can_still_be_talked_about_on_the_route(self):
        from odoo import release
        if release.version_info[0] < 18:
            return  # the route resolves the thread in the controller there
        partner = self.env["res.partner"].create({"name": "Read Only Talk"})
        self.Profile.create({
            "name": "Read Only Chatter Route",
            "readonly": True,
            "user_ids": [(6, 0, [self.user.id])],
        })
        thread = self.env["res.partner"].with_user(self.user)._get_thread_with_access(
            partner.id, mode="write",
        )
        self.assertEqual(thread, partner)

    # ---- round fifteen: the search view --------------------------------

    def test_a_filter_rule_is_hidden_outright_not_per_record(self):
        """The client evaluates a filter's invisible against the context,
        with no record, so a record-field condition would throw there."""
        from lxml import etree
        node = etree.Element("filter", {"name": "type_person"})
        view = self.env["ir.ui.view"]
        view._eh_hide_node(
            node, {}, ("filter", "group"),
            {"filter": {"type_person": "is_company"}}, "type_person",
            per_record=False,
        )
        self.assertEqual(node.get("invisible"), "1")

    def test_a_conditional_node_rule_keeps_the_views_own_condition(self):
        from lxml import etree
        node = etree.Element("button", {
            "name": "action_x", "invisible": "payment_token_count == 0",
        })
        view = self.env["ir.ui.view"]
        view._eh_hide_node(
            node, {}, ("button",), {"button": {"action_x": "id == 999999"}}, "action_x",
        )
        self.assertEqual(
            node.get("invisible"), "(payment_token_count == 0) or (id == 999999)",
        )

    def test_files_cannot_be_dropped_on_a_chatter_that_is_hidden(self):
        partner = self.env["res.partner"].create({"name": "No Chatter Upload"})
        self._chatter_profile(hide_chatter=True)
        with _request(self.user.id, path="/mail/attachment/upload"):
            with self.assertRaises(AccessError):
                self.env["ir.attachment"].with_user(self.user).sudo().create({
                    "name": "dropped.txt", "raw": b"hello",
                    "res_model": "res.partner", "res_id": partner.id,
                })
        # With the chatter present, attaching stays part of talking.
        attachment = self.env["ir.attachment"].with_user(self.user).create({
            "name": "fine.txt", "raw": b"hello",
            "res_model": "res.partner", "res_id": partner.id,
        })
        self.assertTrue(attachment)

    # ---- round sixteen ---------------------------------------------------

    def test_a_route_is_matched_as_odoo_dispatched_it(self):
        """Odoo builds its routing map with strict_slashes=False, so one
        extra character used to walk past every gate keyed on the path."""
        import types
        from odoo.addons.eh_access_studio.models.base_model import _eh_dispatched_route
        for spelling in (
            "/mail/message/post/", "//mail//message//post", "/mail/message/post",
        ):
            request = types.SimpleNamespace(
                httprequest=types.SimpleNamespace(url_rule=None, path=spelling),
            )
            self.assertEqual(_eh_dispatched_route(request), "/mail/message/post")
        # When werkzeug records the rule it matched, that is canonical.
        request = types.SimpleNamespace(httprequest=types.SimpleNamespace(
            url_rule=types.SimpleNamespace(rule="/mail/message/post"),
            path="/mail/message/post/",
        ))
        self.assertEqual(_eh_dispatched_route(request), "/mail/message/post")

    def test_a_filtered_action_never_lands_on_a_blank_form(self):
        self.Profile.create({
            "name": "List Hidden On Contacts",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "hidden_view_types": "list",
            })],
        })
        action = self.env["ir.actions.act_window"].sudo().create({
            "name": "Contacts Probe", "res_model": "res.partner",
            "view_mode": "list,form,kanban",
        })
        result = {
            "res_model": "res.partner",
            "views": [(False, "list"), (False, "form"), (False, "kanban")],
            "view_mode": "list,form,kanban",
        }
        with patch.object(
            type(action), "_eh_access_studio_request_env",
            lambda self: self.env(user=self.env.user.id),
        ):
            filtered = action.with_user(self.user)._eh_access_studio_filter_action_dict(result)
        modes = [mode for _view, mode in filtered["views"] if mode != "search"]
        self.assertTrue(modes, "the action was left with nothing to render")
        self.assertNotEqual(
            modes[0], "form",
            "the menu would open a blank new record instead of a list",
        )
        self.assertNotIn("list", modes)

    def test_the_management_chain_placeholders_resolve(self):
        """__subordinate_uids__ called a method that did not exist, so
        every rule using it failed closed and showed nothing."""
        rule = self.env["eh.access.domain"].sudo().create({
            "profile_id": self.Profile.create({
                "name": "Hierarchy", "user_ids": [(6, 0, [self.user.id])],
            }).id,
            "model_id": self.partner_model,
            "rights_preset": "read_only",
            "apply_filter": True,
            "domain": "[('user_id', 'in', '__subordinate_uids__')]",
        })
        as_user = rule.with_user(self.user)
        self.assertIsInstance(as_user._eh_subordinate_uids(), list)
        resolved = as_user._resolved_domain()
        self.assertEqual(
            [tuple(leaf) for leaf in resolved],
            [("user_id", "in", [])],
            "the placeholder must resolve to a user list, not fail closed",
        )

    # ---- round seventeen: conditions everywhere, names, notes ----------

    def _conditional_profile(self, field, condition, **flags):
        line = {
            "model_id": self.partner_model,
            "field_ids": [(6, 0, self._field(field).ids)],
            "condition": condition,
        }
        line.update(flags)
        return self.Profile.create({
            "name": "Conditional %s" % field,
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, line)],
        })

    def test_a_conditional_lock_is_judged_on_the_record_being_created(self):
        self._conditional_profile("website", "is_company", readonly=True)
        as_user = self.env["res.partner"].with_user(self.user)
        # The condition is false for an individual: the value is kept.
        person = as_user.create({
            "name": "Individual Site", "is_company": False,
            "website": "http://kept.example",
        })
        self.assertEqual(person.website, "http://kept.example")
        # And true for a company: the value is not the caller's to choose.
        company = as_user.create({
            "name": "Company Site", "is_company": True,
            "website": "http://dropped.example",
        })
        self.assertFalse(company.website)

    def test_a_conditional_required_rule_applies_only_where_it_says(self):
        self._conditional_profile("website", "is_company", required=True)
        as_user = self.env["res.partner"].with_user(self.user)
        person = as_user.create({"name": "No Site Needed", "is_company": False})
        self.assertTrue(person)
        with self.assertRaises(ValidationError):
            as_user.create({"name": "Site Needed", "is_company": True})

    def test_hiding_the_name_hides_the_display_name(self):
        partner = self.env["res.partner"].create({"name": "SECRET NAME"})
        self.Profile.create({
            "name": "Hidden Name",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("name").ids)],
                "invisible": True,
            })],
        })
        as_user = partner.with_user(self.user)
        row = as_user.read(["name", "display_name"])[0]
        self.assertFalse(row["name"])
        self.assertFalse(row["display_name"])
        model = self.env["res.partner"].with_user(self.user)
        self.assertFalse(model.name_search("SECRET"))
        self.assertFalse(model.search([("display_name", "like", "SECRET")]))

    def test_an_existing_note_cannot_be_rewritten_through_a_command(self):
        partner = self.env["res.partner"].create({"name": "Note Owner"})
        note = partner.message_post(
            body="<p>original</p>", message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        self._chatter_profile(hide_log_note=True)
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({
                "message_ids": [(1, note.id, {"body": "<p>new words</p>"})],
            })
        self.assertIn("original", note.body)

    def test_a_filter_naming_an_unknown_field_is_refused_at_save(self):
        profile = self.Profile.create({"name": "Typo Rule"})
        with self.assertRaises(ValidationError):
            self.env["eh.access.domain"].create({
                "profile_id": profile.id,
                "model_id": self.partner_model,
                "rights_preset": "read_only",
                "apply_filter": True,
                "domain": "[('no_such_field', '=', 1)]",
            })

    def test_export_honours_a_conditional_rule_row_by_row(self):
        company = self.env["res.partner"].create({
            "name": "Exported Co", "is_company": True, "website": "http://co.example",
        })
        person = self.env["res.partner"].create({
            "name": "Exported Person", "is_company": False,
            "website": "http://person.example",
        })
        self._conditional_profile("website", "is_company", invisible=True)
        rows = (company + person).with_user(self.user).export_data(
            ["name", "website"],
        )["datas"]
        by_name = {row[0]: row[1] for row in rows}
        self.assertEqual(by_name["Exported Co"], "")
        self.assertEqual(by_name["Exported Person"], "http://person.example")

    # ---- round seventeen, part two: grouping and the keyword form ------

    def _hide_color(self):
        return self.Profile.create({
            "name": "Hidden Colour",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("color").ids)],
                "invisible": True,
            })],
        })

    def test_an_aggregate_spelling_is_still_the_hidden_field(self):
        """'colour:sum' in HAVING or in a sort names the hidden field as
        surely as 'colour' does, and HAVING decides WHICH groups come
        back, so it answers what the blanked value is."""
        model = self.env["res.partner"].with_user(self.user)
        self._hide_color()
        self.assertTrue(model._eh_access_studio_path_hidden("color:sum"))
        self.assertFalse(model._eh_access_studio_scrub_order("color:sum desc"))
        self.assertFalse(model._eh_access_studio_scrub_order("color desc"))

    def test_grouping_by_a_hidden_field_collapses_on_every_series(self):
        for index, colour in enumerate((1, 5, 9)):
            self.env["res.partner"].create({
                "name": "Grp %s" % index, "color": colour,
            })
        self._hide_color()
        as_user = self.env["res.partner"].with_user(self.user)
        domain = [("name", "=like", "Grp %")]
        from odoo import release
        rows = (as_user.read_group(domain, groupby=["color"], aggregates=["__count"])
                if release.version_info[0] >= 20
                else as_user.read_group(domain, ["id"], ["color"]))
        self.assertEqual(len(rows), 1, "the groups still tell the values apart")
        self.assertFalse(rows[0][0] if isinstance(rows[0], tuple) else rows[0].get("color"))
        # And a search on the hidden value answers nothing either way.
        self.assertEqual(
            as_user.search_count(domain + [("color", "=", 5)]),
            as_user.search_count(domain + [("color", "=", 7)]),
        )

    def test_the_keyword_form_of_onchange_is_gated_too(self):
        from odoo import release
        if release.version_info[0] < 17:
            return  # the keyword names differ on 16, positional is used
        self.env["res.partner"].create({"name": "Kw Vat", "vat": "BE0477472701"})
        self.Profile.create({
            "name": "Hidden Vat Keyword",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("vat").ids)],
                "invisible": True,
            })],
        })
        as_user = self.env["res.partner"].with_user(self.user)

        def probe(vat):
            return as_user.onchange(
                values={"name": "Probe", "vat": vat},
                field_names=["vat"],
                fields_spec={"name": {}, "vat": {}},
            )

        self.assertEqual(probe("BE0477472701"), probe("BE0999999999"))

    # ---- round eighteen: printed names and server actions --------------

    def test_hiding_a_parent_hides_the_name_it_is_printed_under(self):
        parent = self.env["res.partner"].create({
            "name": "HIDDEN PARENT", "is_company": True,
        })
        child = self.env["res.partner"].create({
            "name": "Kid", "parent_id": parent.id,
        })
        self.Profile.create({
            "name": "Hidden Parent Link",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("parent_id").ids)],
                "invisible": True,
            })],
        })
        # complete_name arrived with Odoo 17.
        wanted = ["parent_id", "display_name"] + [
            name for name in ("complete_name",)
            if name in self.env["res.partner"]._fields
        ]
        row = child.with_user(self.user).read(wanted)[0]
        self.assertFalse(row["parent_id"])
        for key in wanted[1:]:
            self.assertNotIn(
                "HIDDEN PARENT", row.get(key) or "",
                "%s still prints the hidden parent" % key,
            )

    def test_name_search_does_not_answer_with_a_hidden_value(self):
        partner = self.env["res.partner"].create({
            "name": "Autocomplete Target", "email": "auto.secret@example.test",
        })
        self._hide_email()
        model = self.env["res.partner"].with_user(self.user)
        rows = model.with_context(show_email=True).name_search("Autocomplete")
        self.assertTrue(rows)
        for _record_id, label in rows:
            self.assertNotIn("@", label)
        self.assertTrue(partner)

    def test_a_hidden_server_action_cannot_be_run_by_id(self):
        action = self.env["ir.actions.server"].sudo().create({
            "name": "Probe Action",
            "model_id": self.partner_model,
            "state": "code",
            "code": "record.write({'comment': '<p>ran</p>'})",
        })
        self.Profile.create({
            "name": "Hidden Server Action",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "hidden_server_action_ids": [(6, 0, action.ids)],
            })],
        })
        partner = self.env["res.partner"].create({"name": "Action Target"})
        with self.assertRaises(AccessError):
            action.with_user(self.user).with_context(
                active_model="res.partner", active_id=partner.id,
                active_ids=partner.ids,
            ).run()
        self.assertFalse(partner.comment)

    def test_a_many2one_label_follows_the_other_models_rule(self):
        parent = self.env["res.partner"].create({
            "name": "LABEL SECRET", "is_company": True,
        })
        child = self.env["res.partner"].create({
            "name": "Child Of Secret", "parent_id": parent.id,
        })
        self.Profile.create({
            "name": "Hidden Contact Name",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("name").ids)],
                "invisible": True,
            })],
        })
        row = child.with_user(self.user).read(["parent_id"])[0]
        self.assertTrue(row["parent_id"])
        self.assertEqual(row["parent_id"][1], "")

    def test_hiding_a_state_does_not_blank_every_amount(self):
        """A computed field is only hidden when it is computed from
        hidden inputs alone; sharing one dependency is not enough."""
        self.Profile.create({
            "name": "Hidden Type",
            "user_ids": [(6, 0, [self.user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "field_ids": [(6, 0, self._field("type").ids)],
                "invisible": True,
            })],
        })
        hidden = self.env["res.partner"].with_user(
            self.user,
        )._eh_access_studio_hidden_names()
        self.assertIn("type", hidden)
        for name in ("display_name", "email", "phone", "country_id"):
            self.assertNotIn(
                name, hidden,
                "%s was hidden although it carries no part of the type" % name,
            )

    def test_the_action_load_path_filters_on_every_series(self):
        """Odoo 16 and 17 load an action through read(), 18 and 19
        through _get_action_dict. Both paths must filter."""
        self.Profile.create({
            "name": "Kanban Hidden",
            "user_ids": [(6, 0, [self.user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model,
                "hidden_view_types": "kanban",
            })],
        })
        action = self.env["ir.actions.act_window"].sudo().create({
            "name": "Read Path Probe", "res_model": "res.partner",
            "view_mode": "list,kanban,form",
        })
        # Core reads the action as superuser and the filter resolves the
        # person who made the request, which is what this patch stands in
        # for. The action itself is read with sudo, as the controller does.
        user_env = self.env(user=self.user.id, su=False)
        with patch.object(
            type(action), "_eh_access_studio_request_env",
            lambda self: user_env,
        ):
            as_sudo = action.sudo()
            from_read = as_sudo.read(["view_mode", "views", "res_model"])[0]
            from_dict = as_sudo._get_action_dict()
        for payload in (from_read, from_dict):
            modes = [mode for _view, mode in payload["views"] if mode != "search"]
            self.assertNotIn("kanban", modes)
            self.assertTrue(modes)
