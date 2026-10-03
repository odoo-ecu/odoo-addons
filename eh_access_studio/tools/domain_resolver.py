# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
Domain placeholder resolver.

Pure Python utility used by User Access Studio to resolve smart
placeholder sentinels in stored access domains. Independent of Odoo so
that the resolver can be unit-tested in isolation.

Sentinels recognised in domain right-values:
    "__uid__"            current user id
    "__cid__"            current company id
    "__company_ids__"    list of allowed company ids for current user
    "__subordinate_uids__"  ids of the users below this one in the
                         management chain, at any depth
    "__team_uids__"      the same set plus this user

Sentinels recognised in date / datetime right-values (string form):
    "__today__"          today
    "__yesterday__"      today minus one day
    "__tomorrow__"       today plus one day
    "__week_start__"     start of current ISO week (Monday)
    "__week_end__"       end of current ISO week (Sunday inclusive)
    "__month_start__"    first day of current month
    "__month_end__"      last day of current month
    "__quarter_start__"  first day of current quarter
    "__quarter_end__"    last day of current quarter
    "__year_start__"     first day of current year
    "__year_end__"       last day of current year
    "__last_7_days__"    today minus 7 days
    "__last_30_days__"   today minus 30 days
    "__last_90_days__"   today minus 90 days
    "__last_365_days__"  today minus 365 days

Resolution strategy: walk the domain depth-first, and for every leaf
tuple replace any matching string sentinel in the right-value with its
resolved value. Operator and field name are preserved as-is. Boolean
operators ("&", "|", "!") pass through unchanged.

Inputs:
    domain  ........ Odoo domain (list of leaves and operators)
    context  ....... dict with keys: uid (int), cid (int),
                     company_ids (list of int), today (date),
                     tz (zoneinfo.ZoneInfo or compatible)

Returns: a new domain list with sentinels replaced.

Unrecognised string right-values are passed through untouched. This
keeps the resolver forward-compatible with future sentinels and lets
domains contain ordinary string values without surprise rewrites.
"""

from __future__ import annotations

import calendar
import datetime as _dt
from typing import Any

USER_SENTINELS = {
    "__uid__": "uid",
    "__cid__": "cid",
    "__company_ids__": "company_ids",
    # Hierarchy. Resolved by the caller, which needs the database to
    # walk the management chain, and passed in like any other value so
    # this module stays pure Python and testable without Odoo.
    "__subordinate_uids__": "subordinate_uids",
    "__team_uids__": "team_uids",
}

# Each entry: callable(today: date) -> date
DATE_SENTINELS = {
    "__today__": lambda d: d,
    "__yesterday__": lambda d: d - _dt.timedelta(days=1),
    "__tomorrow__": lambda d: d + _dt.timedelta(days=1),
    "__week_start__": lambda d: d - _dt.timedelta(days=d.weekday()),
    "__week_end__": lambda d: d - _dt.timedelta(days=d.weekday()) + _dt.timedelta(days=6),
    "__month_start__": lambda d: d.replace(day=1),
    "__month_end__": lambda d: d.replace(day=calendar.monthrange(d.year, d.month)[1]),
    "__quarter_start__": lambda d: d.replace(month=((d.month - 1) // 3) * 3 + 1, day=1),
    "__quarter_end__": (
        lambda d: _quarter_end(d)
    ),
    "__year_start__": lambda d: d.replace(month=1, day=1),
    "__year_end__": lambda d: d.replace(month=12, day=31),
    "__last_7_days__": lambda d: d - _dt.timedelta(days=7),
    "__last_30_days__": lambda d: d - _dt.timedelta(days=30),
    "__last_90_days__": lambda d: d - _dt.timedelta(days=90),
    "__last_365_days__": lambda d: d - _dt.timedelta(days=365),
}

OPERATORS = {"&", "|", "!"}


def _quarter_end(d: _dt.date) -> _dt.date:
    start_month = ((d.month - 1) // 3) * 3 + 1
    end_month = start_month + 2
    last_day = calendar.monthrange(d.year, end_month)[1]
    return d.replace(month=end_month, day=last_day)


def resolve(domain: list, context: dict) -> list:
    """Return a new domain with sentinels resolved using context."""
    if not domain:
        return list(domain or [])
    out = []
    for token in domain:
        if isinstance(token, str) and token in OPERATORS:
            out.append(token)
            continue
        if isinstance(token, (list, tuple)) and len(token) == 3:
            out.append(_resolve_leaf(tuple(token), context))
            continue
        out.append(token)
    return out


def _resolve_leaf(leaf: tuple, context: dict) -> tuple:
    field, op, value = leaf
    return (field, op, _resolve_value(value, context))


def _resolve_value(value: Any, context: dict) -> Any:
    if isinstance(value, str):
        return _resolve_string(value, context)
    if isinstance(value, (list, tuple)):
        cls = type(value)
        return cls(_resolve_value(item, context) for item in value)
    return value


def _resolve_string(value: str, context: dict) -> Any:
    if value in USER_SENTINELS:
        key = USER_SENTINELS[value]
        if key not in context:
            raise KeyError(
                "Access Studio: domain references {0} but {1} is missing"
                " from the resolution context".format(value, key)
            )
        return context[key]
    if value in DATE_SENTINELS:
        today = context.get("today")
        if today is None:
            raise KeyError(
                "Access Studio: domain references {0} but 'today' is"
                " missing from the resolution context".format(value)
            )
        resolved = DATE_SENTINELS[value](today)
        return resolved.isoformat()
    return value
