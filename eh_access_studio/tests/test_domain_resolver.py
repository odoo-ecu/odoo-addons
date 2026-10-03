# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
Odoo test wrapper for the standalone domain_resolver suite.

The full suite runs without Odoo via tools/test_domain_resolver.py. This
wrapper imports the same module and re-runs the cases inside the Odoo
test harness so CI runs both the plain-Python and Odoo paths.
"""
from odoo.addons.eh_access_studio.tools import domain_resolver as dr
from odoo.tests.common import TransactionCase
import datetime as dt


REFERENCE_DATE = dt.date(2026, 5, 7)


class TestDomainResolverInsideOdoo(TransactionCase):

    def test_uid_substitution(self):
        ctx = {
            "uid": self.env.user.id,
            "cid": self.env.company.id,
            "company_ids": self.env.companies.ids,
            "today": REFERENCE_DATE,
        }
        result = dr.resolve([("user_id", "=", "__uid__")], ctx)
        self.assertEqual(result, [("user_id", "=", self.env.user.id)])

    def test_today_resolves(self):
        ctx = {
            "uid": self.env.user.id,
            "cid": self.env.company.id,
            "company_ids": self.env.companies.ids,
            "today": REFERENCE_DATE,
        }
        result = dr.resolve([("date", "=", "__today__")], ctx)
        self.assertEqual(result, [("date", "=", "2026-05-07")])

    def test_unknown_string_passes_through(self):
        ctx = {
            "uid": self.env.user.id,
            "cid": self.env.company.id,
            "company_ids": self.env.companies.ids,
            "today": REFERENCE_DATE,
        }
        domain = [("name", "=", "any literal")]
        self.assertEqual(dr.resolve(domain, ctx), domain)
