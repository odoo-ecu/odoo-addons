# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: fail-closed behaviour test.

The Domain Access tab is the strict gate. A misconfigured rule must
block all access (fail closed), not silently grant access. This test
exercises the parse-failure and not-a-list paths on
eh.access.domain._resolved_domain.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFailClosed(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Domain = cls.env["eh.access.domain"]
        cls.Profile = cls.env["eh.access.profile"]
        cls.IrModel = cls.env["ir.model"]

    def _make_domain_rule(self):
        profile = self.Profile.create({"name": "Fail-closed test"})
        return self.Domain.create({
            "profile_id": profile.id,
            "model_id": self.IrModel._get("res.partner").id,
            "read_right": True,
            "apply_filter": True,
            "domain": "[]",
        })

    def test_apply_filter_off_returns_empty(self):
        rule = self._make_domain_rule()
        rule.apply_filter = False
        self.assertEqual(rule._resolved_domain(), [])

    def test_empty_domain_with_filter_on_blocks_all(self):
        rule = self._make_domain_rule()
        # Bypass the constraint by writing the value directly via SQL
        # to simulate a corrupted import / migration. We then call the
        # resolver and assert it returns BLOCK_ALL.
        self.env.cr.execute(
            "UPDATE eh_access_domain SET domain=NULL WHERE id=%s",
            (rule.id,),
        )
        rule.invalidate_recordset(["domain"])
        self.assertEqual(rule._resolved_domain(), [("id", "=", False)])

    def test_unparseable_domain_blocks_all(self):
        rule = self._make_domain_rule()
        self.env.cr.execute(
            "UPDATE eh_access_domain SET domain=%s WHERE id=%s",
            ("[(garbage", rule.id),
        )
        rule.invalidate_recordset(["domain"])
        self.assertEqual(rule._resolved_domain(), [("id", "=", False)])

    def test_non_list_domain_blocks_all(self):
        rule = self._make_domain_rule()
        self.env.cr.execute(
            "UPDATE eh_access_domain SET domain=%s WHERE id=%s",
            ("{'not': 'a list'}", rule.id),
        )
        rule.invalidate_recordset(["domain"])
        self.assertEqual(rule._resolved_domain(), [("id", "=", False)])

    def test_valid_domain_resolves_normally(self):
        rule = self._make_domain_rule()
        rule.domain = '[("user_id", "=", "__uid__")]'
        resolved = rule._resolved_domain()
        self.assertEqual(resolved, [("user_id", "=", self.env.user.id)])
