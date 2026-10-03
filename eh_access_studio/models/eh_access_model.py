# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: per-model access rule line.

A model line attaches to one access profile and one model and bundles
the model-level toggles: hide create / edit / delete / archive /
duplicate / import / export / spreadsheet / add property, plus three
many2many lists for hiding view types, report actions and server
actions through the standard toolbar.

This model is the source of truth for everything that gets injected
into the view arch attributes (create, delete, edit, export_xlsx,
import) at render time, and for everything that gets dropped from the
toolbar dictionary (print actions, server actions, alternative view
types).

Two tiers, deliberately. restrict_create, restrict_edit, restrict_delete,
restrict_archive and restrict_duplicate are enforced at the ORM gate
(see models/base_model.py: _check_access, write and the archive
overrides), so they hold over RPC and for server code running as the
user. restrict_import and restrict_export are enforced on load() and
export_data(). restrict_add_property and hidden_view_types shape the
interface only, and restrict_spreadsheet is retired in favour of the
profile-wide switch.
Use eh.access.domain to restrict WHICH records a user may reach.
"""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


# View types we know how to suppress on the front-end.
SUPPRESSIBLE_VIEW_TYPES = (
    "list",
    "kanban",
    "form",
    "calendar",
    "pivot",
    "graph",
    "gantt",
    "activity",
    "cohort",
    "grid",
    "map",
    "hierarchy",
    "search",
)


class EhAccessModel(models.Model):
    _name = "eh.access.model"
    _inherit = ["eh.access.line.mixin"]
    _description = "User Access Studio Model Rule"
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

    restrict_create = fields.Boolean(string="Hide Create")
    restrict_edit = fields.Boolean(string="Hide Edit")
    restrict_delete = fields.Boolean(string="Hide Delete")
    restrict_archive = fields.Boolean(string="Hide Archive")
    restrict_duplicate = fields.Boolean(string="Hide Duplicate")
    restrict_import = fields.Boolean(string="Hide Import")
    restrict_export = fields.Boolean(string="Hide Export")
    restrict_spreadsheet = fields.Boolean(
        string="Hide Insert in Spreadsheet",
        help=(
            "Retired. Spreadsheet insertion is a single session-wide"
            " capability flag in the web client, so it cannot be turned"
            " off for one model. Use 'Hide Insert in Spreadsheet"
            " (everywhere)' on the Global tab. The column is kept so"
            " existing data and YAML files still load."
        ),
    )
    restrict_add_property = fields.Boolean(string="Hide Add Property")

    hide_search_panel = fields.Boolean(
        string="Hide Search Panel",
        help=(
            "Remove the left-hand search panel from this model's list"
            " and kanban views."
        ),
    )

    hidden_view_types = fields.Char(
        string="Hidden View Types",
        help=(
            "Comma-separated list of view types to remove from action"
            " responses. Recognised values: list, kanban, form, calendar,"
            " pivot, graph, gantt, activity, cohort, grid, map,"
            " hierarchy. 'search' is not a switchable mode and is"
            " ignored."
        ),
    )

    hidden_report_action_ids = fields.Many2many(
        "ir.actions.report",
        "eh_access_model_report_rel",
        "line_id",
        "report_id",
        string="Hidden Reports",
        domain="[('binding_model_id', '=', model_id)]",
    )
    hidden_server_action_ids = fields.Many2many(
        "ir.actions.server",
        "eh_access_model_server_rel",
        "line_id",
        "server_id",
        string="Hidden Server Actions",
        domain="[('binding_model_id', '=', model_id)]",
    )

    @api.constrains("hidden_view_types")
    def _check_hidden_view_types(self):
        for line in self:
            if not line.hidden_view_types:
                continue
            invalid = []
            for token in (t.strip() for t in line.hidden_view_types.split(",")):
                if not token:
                    continue
                if token not in SUPPRESSIBLE_VIEW_TYPES:
                    invalid.append(token)
            if invalid:
                raise ValidationError(_(
                    "Access Studio: unknown view type(s) %(types)s."
                    " Allowed: %(allowed)s.",
                    types=", ".join(invalid),
                    allowed=", ".join(SUPPRESSIBLE_VIEW_TYPES),
                ))

    def _hidden_view_type_set(self):
        if not self.hidden_view_types:
            return set()
        return {t.strip() for t in self.hidden_view_types.split(",") if t.strip()}
