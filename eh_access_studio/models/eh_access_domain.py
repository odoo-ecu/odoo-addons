# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: per-model record-rule overlay.

A domain rule attaches to a profile and a model and answers four
questions for users in that profile:

  * read_right    can the user read records of this model?
  * create_right  can the user create new records?
  * write_right   can the user edit existing records?
  * delete_right  can the user delete records?

When `apply_filter` is enabled, the `domain` field narrows the set of
records to which read / write / delete apply. The domain may include
smart placeholder sentinels that are resolved at evaluation time:

  __uid__              current user id
  __cid__              current company id
  __company_ids__      list of allowed company ids
  __today__            today (date)
  __week_start__ ...   plus the full date sentinel set documented in
                       tools/domain_resolver.py.

Resolution is delegated to tools.domain_resolver, which is a plain
Python module testable without Odoo loaded.

Audit trail: there is deliberately no per-operation log here. A rule is
evaluated on every search, read, write and unlink, so writing a chatter
message per blocked operation would put an unbounded write load on the
read path. Blocked operations surface as Odoo's standard access error
plus a server log line. What IS recorded on the profile chatter is the
low-volume, high-value pair: denied sign-ins and automatic expiry.
"""
import ast
import logging

from odoo import api, fields, models, tools, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class EhAccessDomain(models.Model):
    _name = "eh.access.domain"
    _inherit = ["eh.access.line.mixin"]
    _description = "User Access Studio Domain Rule"
    _order = "model_name, id"

    profile_id = fields.Many2one(
        "eh.access.profile",
        string="Profile",
        required=True,
        ondelete="cascade",
        index=True,
    )
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        required=True,
        ondelete="cascade",
        index=True,
    )
    model_name = fields.Char(
        string="Model Technical Name",
        related="model_id.model",
        store=True,
        readonly=True,
        index=True,
    )

    read_right = fields.Boolean(string="Read", default=True)
    create_right = fields.Boolean(string="Create")
    write_right = fields.Boolean(string="Edit")
    delete_right = fields.Boolean(string="Delete")

    rights_preset = fields.Selection(
        [
            ("read_only", "Read only"),
            ("read_create", "Read + Create"),
            ("read_write", "Read + Edit"),
            ("read_create_write", "Read + Create + Edit"),
            ("full", "Full access (Read + Create + Edit + Delete)"),
            ("none", "No access"),
            ("custom", "Custom"),
        ],
        string="Access level",
        compute="_compute_rights_preset",
        inverse="_inverse_rights_preset",
        store=True,
        help=(
            "Pick a common combination. Switch to 'Custom' to set"
            " individual rights checkboxes."
        ),
    )

    @api.depends("read_right", "create_right", "write_right", "delete_right")
    def _compute_rights_preset(self):
        # Map (read, create, write, delete) tuples to preset names.
        presets = {
            (False, False, False, False): "none",
            (True, False, False, False): "read_only",
            (True, True, False, False): "read_create",
            (True, False, True, False): "read_write",
            (True, True, True, False): "read_create_write",
            (True, True, True, True): "full",
        }
        for record in self:
            key = (
                record.read_right,
                record.create_right,
                record.write_right,
                record.delete_right,
            )
            record.rights_preset = presets.get(key, "custom")

    def _inverse_rights_preset(self):
        mapping = {
            "none": (False, False, False, False),
            "read_only": (True, False, False, False),
            "read_create": (True, True, False, False),
            "read_write": (True, False, True, False),
            "read_create_write": (True, True, True, False),
            "full": (True, True, True, True),
        }
        for record in self:
            if record.rights_preset == "custom" or not record.rights_preset:
                continue
            r, c, w, d = mapping[record.rights_preset]
            record.write({
                "read_right": r,
                "create_right": c,
                "write_right": w,
                "delete_right": d,
            })

    soft_restrict = fields.Boolean(
        string="Soft Restriction",
        help=(
            "Show the records outside the filter instead of hiding"
            " them, but do not let this user change them. The filter"
            " then applies to Edit and Delete only, and reading is left"
            " alone. Use it when a role needs the context around its"
            " own records without being able to touch it."
        ),
    )

    apply_filter = fields.Boolean(
        string="Apply Filter",
        help=(
            "When enabled, the rule limits the set of records to which"
            " the chosen rights apply. Without a filter, the rights"
            " apply to all records of the model."
        ),
    )
    domain = fields.Text(
        string="Filter",
        default="[]",
        help=(
            "Python list of domain leaves. Smart placeholders are"
            " supported. Example: [(\"company_id\", \"in\","
            " \"__company_ids__\"), (\"create_date\", \">=\","
            " \"__month_start__\")]."
        ),
    )

    @api.constrains("domain", "apply_filter")
    def _check_domain_parses(self):
        for record in self:
            if not record.apply_filter:
                continue
            try:
                parsed = ast.literal_eval(record.domain or "[]")
            except (ValueError, SyntaxError) as err:
                raise ValidationError(_(
                    "Access Studio: filter cannot be parsed (%(error)s)."
                    " Use a Python list of domain leaves.",
                    error=err,
                ))
            if not isinstance(parsed, list):
                raise ValidationError(_(
                    "Access Studio: filter must be a Python list."
                ))
            record._check_domain_fields(parsed)

    def _check_domain_fields(self, parsed):
        """Refuse a filter that names a field the model does not have.

        A typo used to save cleanly and then every read of that model, by
        every user on the profile, died with a raw ValueError from the
        ORM instead of an access denial. It is caught here, while the
        administrator is looking at the rule.
        """
        model_name = self.model_name or (self.model_id and self.model_id.model)
        if not model_name or model_name not in self.env:
            return
        unknown = []
        for leaf in parsed:
            if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
                continue
            path = leaf[0]
            if not isinstance(path, str):
                continue
            model = self.env[model_name]
            for index, segment in enumerate(path.split(".")):
                # A placeholder is resolved before the domain is used.
                if segment.startswith("__") and segment.endswith("__"):
                    break
                field = model._fields.get(segment)
                if field is None:
                    unknown.append(path)
                    break
                if getattr(field, "comodel_name", None) and getattr(field, "comodel_name", None) in self.env:
                    model = self.env[getattr(field, "comodel_name", None)]
                elif index < len(path.split(".")) - 1:
                    unknown.append(path)
                    break
        if unknown:
            raise ValidationError(_(
                "Access Studio: the filter names %(fields)s, which"
                " %(model)s does not have. Correct the field name, or"
                " every read of this model would fail for the users on"
                " this profile.",
                fields=", ".join(sorted(set(unknown))),
                model=model_name,
            ))

    # Sentinel returned by _resolved_domain when the rule is meant to
    # apply but the stored domain cannot be parsed. The ir.rule
    # extension treats this as "block all records" so a misconfiguration
    # fails closed instead of silently granting access.
    _BLOCK_ALL_DOMAIN = [("id", "=", False)]

    def _eh_subordinate_uids(self):
        """User ids below this user in the management chain, any depth.

        Read from the HR app, which is where Odoo keeps the chain
        (hr.employee.parent_id). Without HR installed there is no chain
        to walk, so the answer is empty and a filter written against it
        matches nothing rather than everything. The walk is breadth
        first with a visited set, so a loop in the data cannot hang it.
        """
        if "hr.employee" not in self.env:
            return []
        Employee = self.env["hr.employee"].sudo()
        try:
            frontier = Employee.search([("user_id", "=", self.env.user.id)])
        except Exception:
            _logger.exception(
                "Access Studio: could not read the management chain",
            )
            return []
        seen = set(frontier.ids)
        found = set()
        while frontier:
            children = Employee.search([("parent_id", "in", frontier.ids)])
            children = children.filtered(lambda child: child.id not in seen)
            if not children:
                break
            seen.update(children.ids)
            found.update(uid for uid in children.mapped("user_id").ids if uid)
            frontier = children
        return sorted(found)

    def _resolved_domain(self, today=None):
        """Return the domain with placeholder sentinels resolved.

        Return-value contract:
          * apply_filter is False: return [] (no narrowing)
          * apply_filter is True, parse succeeds: resolved domain
          * apply_filter is True, parse fails: BLOCK_ALL (fail closed)
        """
        from odoo.addons.eh_access_studio.tools import domain_resolver
        if not self.apply_filter:
            return []
        if not self.domain:
            # apply_filter on but no domain stored: configuration error,
            # fail closed.
            _logger.warning(
                "Access Studio: rule %s has apply_filter on but no"
                " domain. Blocking access.", self.id,
            )
            return list(self._BLOCK_ALL_DOMAIN)
        try:
            raw = ast.literal_eval(self.domain)
        except (ValueError, SyntaxError):
            _logger.exception(
                "Access Studio: invalid domain on rule %s, blocking"
                " access (fail closed).", self.id,
            )
            return list(self._BLOCK_ALL_DOMAIN)
        if not isinstance(raw, list):
            _logger.warning(
                "Access Studio: rule %s domain is not a list (got %s),"
                " blocking access.", self.id, type(raw).__name__,
            )
            return list(self._BLOCK_ALL_DOMAIN)
        ctx = {
            "uid": self.env.user.id,
            "cid": self.env["eh.access.profile"].sudo()._eh_safe_company_id(),
            "company_ids": self.env["eh.access.profile"].sudo()._eh_safe_company_ids(),
            # Not fields.Date.context_today: its timezone comes from the
            # RPC context, so any caller could widen their own record
            # rule window by a day with one key.
            "today": today or fields.Date.from_string(
                self.env["eh.access.profile"].sudo()._today_for_cache_key()
            ),
        }
        # Only pay for the hierarchy walk when a filter asks for it.
        if "__subordinate_uids__" in self.domain or "__team_uids__" in self.domain:
            subordinates = self._eh_subordinate_uids()
            ctx["subordinate_uids"] = subordinates
            ctx["team_uids"] = sorted(set(subordinates) | {self.env.user.id})
        try:
            return domain_resolver.resolve(raw, ctx)
        except Exception:
            _logger.exception(
                "Access Studio: domain resolver failed on rule %s,"
                " blocking access.", self.id,
            )
            return list(self._BLOCK_ALL_DOMAIN)
