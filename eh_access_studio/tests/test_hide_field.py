# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: hide-field integration tests.

Covers:
  * field marked invisible in arch when targeted by an active rule
  * field marked readonly, and NOT force_save, when targeted
  * field marked required when targeted
  * mutually exclusive: invisible + required raises
  * unrelated user's view arch is unaffected
"""
import json

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


def _has_modifier(node, name):
    """True when `name` is set on the node, on either arch dialect.

    Odoo 17 and later leave the modifier on the node as an attribute.
    Odoo 16 moves it into a serialised `modifiers` dictionary and
    removes the plain attribute, so a test that only reads the
    attribute would report a working rule as broken on that series.
    """
    if node.get(name) in ("1", "True", "true"):
        return True
    raw = node.get("modifiers")
    if not raw:
        return False
    try:
        return bool(json.loads(raw).get(name))
    except ValueError:
        return False


@tagged("post_install", "-at_install")
class TestHideField(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.User = cls.env["res.users"]
        cls.IrModel = cls.env["ir.model"]
        cls.IrField = cls.env["ir.model.fields"]
        cls.Profile = cls.env["eh.access.profile"]

        cls.demo_user = cls.User.with_context(no_reset_password=True).create({
            "name": "Field Demo",
            "login": "eh_access_field_demo@example.test",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.partner_model = cls.IrModel._get("res.partner")
        cls.comment_field = cls.IrField.search([
            ("model", "=", "res.partner"),
            ("name", "=", "comment"),
        ], limit=1)

    def _make_profile(self, **field_line_overrides):
        profile = self.Profile.create({
            "name": "Hide partner comment",
            "user_ids": [(6, 0, [self.demo_user.id])],
            "field_line_ids": [(0, 0, dict({
                "model_id": self.partner_model.id,
                "field_ids": [(6, 0, [self.comment_field.id])],
                "invisible": True,
            }, **field_line_overrides))],
        })
        return profile

    def _partner_form_arch(self, user):
        return self.env["res.partner"].with_user(user).get_view(view_type="form")["arch"]

    def test_field_invisible_in_arch_for_target_user(self):
        self._make_profile()
        arch = self._partner_form_arch(self.demo_user)
        tree = etree.fromstring(arch)
        nodes = tree.xpath("//field[@name='comment']")
        self.assertTrue(nodes, "comment field should still appear in the arch")
        for node in nodes:
            self.assertTrue(_has_modifier(node, "invisible"))

    def test_field_arch_untouched_for_unaffected_user(self):
        self._make_profile()
        unaffected = self.User.with_context(no_reset_password=True).create({
            "name": "Unaffected Field User",
            "login": "eh_access_field_unaffected@example.test",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        arch = self._partner_form_arch(unaffected)
        tree = etree.fromstring(arch)
        nodes = tree.xpath("//field[@name='comment']")
        for node in nodes:
            self.assertFalse(_has_modifier(node, "invisible"))

    def test_readonly_does_not_force_save(self):
        """A profile-readonly field must be dropped from the write
        payload, not pushed back into it.

        The web client strips readonly fields from a save only when
        force_save is absent, so stamping it would send the locked field
        on every write and defeat the rule.
        """
        self._make_profile(invisible=False, readonly=True)
        arch = self._partner_form_arch(self.demo_user)
        tree = etree.fromstring(arch)
        nodes = tree.xpath("//field[@name='comment']")
        self.assertTrue(nodes)
        for node in nodes:
            self.assertTrue(_has_modifier(node, "readonly"))
            self.assertIsNone(node.get("force_save"))

    def test_required_attr_set(self):
        self._make_profile(invisible=False, required=True)
        arch = self._partner_form_arch(self.demo_user)
        tree = etree.fromstring(arch)
        nodes = tree.xpath("//field[@name='comment']")
        for node in nodes:
            self.assertTrue(_has_modifier(node, "required"))

    def test_invisible_and_required_rejected(self):
        with self.assertRaises(ValidationError):
            self._make_profile(invisible=True, required=True)

    def test_at_least_one_effect_required(self):
        # @api.constrains only fires when one of the named fields is
        # explicitly in vals. Set the effect flags explicitly so the
        # constraint runs and rejects the empty rule.
        with self.assertRaises(ValidationError):
            self.Profile.create({
                "name": "Empty rule",
                "field_line_ids": [(0, 0, {
                    "model_id": self.partner_model.id,
                    "field_ids": [(6, 0, [self.comment_field.id])],
                    "invisible": False,
                    "readonly": False,
                    "required": False,
                    "hide_external_link": False,
                })],
            })
