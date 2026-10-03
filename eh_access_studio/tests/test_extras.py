# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: integration tests for v1.0 finishing-touch
features.

Covers:
  * eh.access.field.hide_external_link stamps the recognised widget
    options (no_open, no_create, no_create_edit, no_quick_create) onto a
    relational field's options attribute.
  * eh.access.model.restrict_duplicate stamps duplicate="false" on the
    root <form> arch node.
  * eh.access.model.restrict_archive raises AccessError when an affected
    user calls toggle_active.
  * eh.access.model.restrict_duplicate raises AccessError when an
    affected user calls copy().
"""
import ast

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestExtras(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.IrModel = cls.env["ir.model"]
        cls.IrField = cls.env["ir.model.fields"]
        cls.Profile = cls.env["eh.access.profile"]

        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "Extras Demo",
            "login": "eh_access_extras_demo@example.test",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.partner_model = cls.IrModel._get("res.partner")
        # parent_id is a relational (many2one) field present on every
        # standard res.partner install.
        cls.parent_field = cls.IrField.search([
            ("model", "=", "res.partner"),
            ("name", "=", "parent_id"),
        ], limit=1)

    # ---- external-link stripping --------------------------------------

    def test_hide_external_link_injects_options(self):
        self.Profile.create({
            "name": "No external link",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "field_ids": [(6, 0, [self.parent_field.id])],
                "hide_external_link": True,
            })],
        })
        arch = (
            self.env["res.partner"]
                .with_user(self.demo_user)
                .get_view(view_type="form")["arch"]
        )
        tree = etree.fromstring(arch)
        nodes = tree.xpath("//field[@name='parent_id']")
        self.assertTrue(nodes, "parent_id should still be in the arch")
        for node in nodes:
            raw = node.get("options") or "{}"
            parsed = ast.literal_eval(raw)
            self.assertIs(parsed.get("no_open"), True)
            self.assertIs(parsed.get("no_create"), True)
            # no_create_edit / no_quick_create are the option names the
            # many2one widget actually reads. 'no_edit' is not one of
            # them and must not be emitted.
            self.assertIs(parsed.get("no_create_edit"), True)
            self.assertIs(parsed.get("no_quick_create"), True)
            self.assertNotIn("no_edit", parsed)

    def test_hide_external_link_only(self):
        # A rule that ONLY sets hide_external_link should be valid: no
        # invisible/readonly/required required.
        profile = self.Profile.create({
            "name": "Link-only rule",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "field_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "field_ids": [(6, 0, [self.parent_field.id])],
                "hide_external_link": True,
            })],
        })
        self.assertTrue(profile.field_line_ids)

    # ---- duplicate via arch attr --------------------------------------

    def test_restrict_duplicate_stamps_arch(self):
        self.Profile.create({
            "name": "No duplicate",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "restrict_duplicate": True,
            })],
        })
        arch = (
            self.env["res.partner"]
                .with_user(self.demo_user)
                .get_view(view_type="form")["arch"]
        )
        tree = etree.fromstring(arch)
        self.assertEqual(tree.get("duplicate"), "false")

    def test_restrict_duplicate_unaffected_user(self):
        self.Profile.create({
            "name": "No duplicate",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "restrict_duplicate": True,
            })],
        })
        other = self.User.with_context(no_reset_password=True).create({
            "name": "Unaffected Extras",
            "login": "eh_access_extras_other@example.test",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        arch = (
            self.env["res.partner"]
                .with_user(other)
                .get_view(view_type="form")["arch"]
        )
        tree = etree.fromstring(arch)
        self.assertNotEqual(tree.get("duplicate"), "false")

    # ---- archive / copy ORM guard -------------------------------------

    def test_restrict_archive_blocks_toggle_active(self):
        self.Profile.create({
            "name": "No archive",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "restrict_archive": True,
            })],
        })
        partner = self.env["res.partner"].create({"name": "Archive Test"})
        with self.assertRaises(AccessError):
            partner.with_user(self.demo_user).toggle_active()

    def test_restrict_duplicate_blocks_copy(self):
        self.Profile.create({
            "name": "No copy",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "model_line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "restrict_duplicate": True,
            })],
        })
        partner = self.env["res.partner"].create({"name": "Copy Test"})
        with self.assertRaises(AccessError):
            partner.with_user(self.demo_user).copy()
