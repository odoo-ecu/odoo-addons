# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Discover named interface elements from installed, combined views."""
import logging

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models.eh_access_node import NODE_KINDS

_logger = logging.getLogger(__name__)


class EhAccessElementWizard(models.TransientModel):
    _name = "eh.access.element.wizard"
    _description = "Access Studio Interface Element Picker"

    profile_id = fields.Many2one("eh.access.profile", required=True, ondelete="cascade")
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade", string="Document type")
    kind = fields.Selection(NODE_KINDS, required=True, default="button", string="Element type")
    choice_ids = fields.One2many("eh.access.element.choice", "wizard_id", readonly=True)
    choice_id = fields.Many2one("eh.access.element.choice", string="Element", domain="[('wizard_id', '=', id)]")
    discovery_notes = fields.Text(readonly=True)

    def _require_manager(self):
        if not self.env.su and not self.env.user.has_group("eh_access_studio.group_eh_access_manager"):
            raise AccessError(_("Only Access Studio managers may add interface rules."))

    @api.onchange("model_id", "kind")
    def _onchange_selection(self):
        self.choice_id = False
        self.discovery_notes = False

    def _discover(self):
        self.ensure_one()
        self._require_manager()
        if not self.model_id or self.model_id.model not in self.env:
            raise UserError(_("Choose an installed document type."))
        view_types = ("search",) if self.kind in ("filter", "group") else ("form", "list", "tree", "kanban")
        views = self.env["ir.ui.view"].sudo().search([
            ("active", "=", True), ("model", "=", self.model_id.model),
            ("type", "in", view_types), ("mode", "=", "primary"),
        ])
        found, failed = {}, []
        tag = {"button": "button", "page": "page", "link": "a", "filter": "filter", "group": "filter"}[self.kind]
        for view in views:
            try:
                arch = view._get_combined_arch()
                if isinstance(arch, (str, bytes)):
                    arch = etree.fromstring(arch.encode() if isinstance(arch, str) else arch)
                for node in arch.iter(tag):
                    # Embedded one2many views belong to their comodel, not to
                    # the selected parent model.
                    if any(parent.tag == "field" for parent in node.iterancestors()):
                        continue
                    name = node.get("name") or (node.get("data-name") if self.kind == "link" else None)
                    if not name:
                        continue
                    is_group = "group_by" in (node.get("context") or "")
                    if self.kind in ("filter", "group") and is_group != (self.kind == "group"):
                        continue
                    label = node.get("string") or node.get("title") or node.get("aria-label") or " ".join(node.itertext()).strip() or name
                    entry = found.setdefault(name, {"target_name": name, "label": label, "view_names": set()})
                    entry["view_names"].add(view.name)
            except Exception:
                _logger.exception("Access Studio could not inspect view %s", view.id)
                failed.append(view.name)
        return sorted(found.values(), key=lambda item: (item["label"], item["target_name"])), failed

    def action_discover(self):
        self.ensure_one()
        choices, failed = self._discover()
        self.choice_id = False
        self.choice_ids.unlink()
        for choice in choices:
            self.env["eh.access.element.choice"].create({
                "wizard_id": self.id, "target_name": choice["target_name"],
                "name": choice["label"], "view_names": ", ".join(sorted(choice["view_names"])),
            })
        self.discovery_notes = _(
            "%(count)s named elements found. Hiding an element changes the interface; it does not authorize or forbid its business operation. "
            "The rule affects matching named elements in this model's views.", count=len(choices),
        )
        if failed:
            self.discovery_notes += _("\nSome views could not be read: %s", ", ".join(failed))
        if not choices:
            self.discovery_notes += _("\nNo supported named elements were found. Elements without stable names require a view change before this picker can target them.")
        return self._action()

    def _action(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "views": [(False, "form")], "target": "new"}

    def action_apply(self):
        self.ensure_one()
        self._require_manager()
        if not self.profile_id or not self.choice_id or self.choice_id.wizard_id != self:
            raise UserError(_("Discover elements, then choose an element from this wizard."))
        if self.profile_id.active:
            raise UserError(_("Use an inactive draft copy for this change and keep the live profile active while reviewing it. This picker does not edit an active policy."))
        current, _failed = self._discover()
        valid = {item["target_name"] for item in current}
        if self.choice_id.target_name not in valid:
            raise UserError(_("This element is no longer present. Discover elements again."))
        duplicate = self.env["eh.access.node"].search([
            ("profile_id", "=", self.profile_id.id), ("model_id", "=", self.model_id.id),
            ("kind", "=", self.kind), ("target_name", "=", self.choice_id.target_name), ("condition", "in", [False, ""]),
        ], limit=1)
        if not duplicate:
            self.env["eh.access.node"].create({"profile_id": self.profile_id.id,
                "model_id": self.model_id.id, "kind": self.kind,
                "target_name": self.choice_id.target_name, "target_label": self.choice_id.name})
        return {"type": "ir.actions.act_window", "res_model": "eh.access.profile", "res_id": self.profile_id.id,
                "view_mode": "form", "views": [(False, "form")], "target": "current"}


class EhAccessElementChoice(models.TransientModel):
    _name = "eh.access.element.choice"
    _description = "Discovered Access Studio Interface Element"
    _order = "name, id"

    wizard_id = fields.Many2one("eh.access.element.wizard", required=True, ondelete="cascade")
    name = fields.Char(required=True, readonly=True)
    target_name = fields.Char(required=True, readonly=True)
    view_names = fields.Char(readonly=True, string="Found in views")
