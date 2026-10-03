# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
Standalone runner for domain_resolver. Runs without Odoo loaded.

Usage:
    python3 tools/test_domain_resolver.py

The test suite covers:
  * passthrough on empty / no-sentinel domains
  * uid / cid / company_ids substitution
  * each date sentinel resolves to the expected date string
  * unrecognised strings pass through unchanged
  * boolean operators ('&', '|', '!') survive
  * leaf right-values that are lists or tuples have sentinels resolved
  * missing context entries raise KeyError
"""
import datetime as dt
import sys
import unittest

import domain_resolver as dr


REFERENCE_DATE = dt.date(2026, 5, 7)  # Thursday


def make_context(uid=42, cid=7, company_ids=(7, 8), today=REFERENCE_DATE):
    return {
        "uid": uid,
        "cid": cid,
        "company_ids": list(company_ids),
        "today": today,
    }


class PassthroughTests(unittest.TestCase):

    def test_empty_domain(self):
        self.assertEqual(dr.resolve([], make_context()), [])

    def test_none_domain_returns_empty_list(self):
        self.assertEqual(dr.resolve(None, make_context()), [])

    def test_plain_domain_unchanged(self):
        domain = [("partner_id", "=", 5), ("amount", ">", 100)]
        self.assertEqual(dr.resolve(domain, make_context()), domain)

    def test_unknown_string_value_unchanged(self):
        domain = [("name", "=", "anything")]
        self.assertEqual(dr.resolve(domain, make_context()), domain)


class UserAndCompanyTests(unittest.TestCase):

    def test_uid_substitution(self):
        domain = [("user_id", "=", "__uid__")]
        result = dr.resolve(domain, make_context(uid=99))
        self.assertEqual(result, [("user_id", "=", 99)])

    def test_cid_substitution(self):
        domain = [("company_id", "=", "__cid__")]
        result = dr.resolve(domain, make_context(cid=3))
        self.assertEqual(result, [("company_id", "=", 3)])

    def test_company_ids_substitution(self):
        domain = [("company_id", "in", "__company_ids__")]
        result = dr.resolve(domain, make_context(company_ids=[1, 2, 3]))
        self.assertEqual(result, [("company_id", "in", [1, 2, 3])])

    def test_uid_inside_list_value(self):
        domain = [("user_id", "in", ["__uid__", 5])]
        result = dr.resolve(domain, make_context(uid=10))
        self.assertEqual(result, [("user_id", "in", [10, 5])])


class DateSentinelTests(unittest.TestCase):

    def test_today(self):
        domain = [("date", "=", "__today__")]
        result = dr.resolve(domain, make_context())
        self.assertEqual(result, [("date", "=", "2026-05-07")])

    def test_yesterday_and_tomorrow(self):
        ctx = make_context()
        self.assertEqual(
            dr.resolve([("date", "=", "__yesterday__")], ctx),
            [("date", "=", "2026-05-06")],
        )
        self.assertEqual(
            dr.resolve([("date", "=", "__tomorrow__")], ctx),
            [("date", "=", "2026-05-08")],
        )

    def test_week_bounds(self):
        # 2026-05-07 is Thursday. Week start (Mon) 2026-05-04, end (Sun) 2026-05-10.
        ctx = make_context()
        self.assertEqual(
            dr.resolve([("date", ">=", "__week_start__")], ctx),
            [("date", ">=", "2026-05-04")],
        )
        self.assertEqual(
            dr.resolve([("date", "<=", "__week_end__")], ctx),
            [("date", "<=", "2026-05-10")],
        )

    def test_month_bounds(self):
        ctx = make_context()
        self.assertEqual(
            dr.resolve([("date", ">=", "__month_start__")], ctx),
            [("date", ">=", "2026-05-01")],
        )
        self.assertEqual(
            dr.resolve([("date", "<=", "__month_end__")], ctx),
            [("date", "<=", "2026-05-31")],
        )

    def test_quarter_bounds(self):
        # May is in Q2 (Apr..Jun).
        ctx = make_context()
        self.assertEqual(
            dr.resolve([("date", ">=", "__quarter_start__")], ctx),
            [("date", ">=", "2026-04-01")],
        )
        self.assertEqual(
            dr.resolve([("date", "<=", "__quarter_end__")], ctx),
            [("date", "<=", "2026-06-30")],
        )

    def test_year_bounds(self):
        ctx = make_context()
        self.assertEqual(
            dr.resolve([("date", ">=", "__year_start__")], ctx),
            [("date", ">=", "2026-01-01")],
        )
        self.assertEqual(
            dr.resolve([("date", "<=", "__year_end__")], ctx),
            [("date", "<=", "2026-12-31")],
        )

    def test_relative_windows(self):
        ctx = make_context()
        self.assertEqual(
            dr.resolve([("date", ">=", "__last_7_days__")], ctx),
            [("date", ">=", "2026-04-30")],
        )
        self.assertEqual(
            dr.resolve([("date", ">=", "__last_30_days__")], ctx),
            [("date", ">=", "2026-04-07")],
        )
        self.assertEqual(
            dr.resolve([("date", ">=", "__last_90_days__")], ctx),
            [("date", ">=", "2026-02-06")],
        )
        self.assertEqual(
            dr.resolve([("date", ">=", "__last_365_days__")], ctx),
            [("date", ">=", "2025-05-07")],
        )


class BooleanOperatorTests(unittest.TestCase):

    def test_and_or_not_pass_through(self):
        domain = [
            "&",
            ("user_id", "=", "__uid__"),
            "|",
            ("company_id", "=", "__cid__"),
            "!",
            ("amount", ">", 0),
        ]
        result = dr.resolve(domain, make_context(uid=11, cid=22))
        self.assertEqual(result, [
            "&",
            ("user_id", "=", 11),
            "|",
            ("company_id", "=", 22),
            "!",
            ("amount", ">", 0),
        ])


class MissingContextTests(unittest.TestCase):

    def test_missing_uid_raises(self):
        with self.assertRaises(KeyError):
            dr.resolve([("user_id", "=", "__uid__")], {"cid": 1, "today": REFERENCE_DATE})

    def test_missing_today_raises(self):
        with self.assertRaises(KeyError):
            dr.resolve([("date", "=", "__today__")], {"uid": 1, "cid": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
