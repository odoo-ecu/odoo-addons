from lxml import etree

from odoo import api, models
from odoo.http import request


class Base(models.AbstractModel):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _inherit = "base"

    # ------------------------------------------------------------------
    # 2. DEFAULT METHODS AND default_get
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. FIELD DECLARATIONS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 4. COMPUTE, INVERSE AND SEARCH METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 5. SELECTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 6. CONSTRAINS METHODS AND ONCHANGE METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 7. CRUD METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 8. ACTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 9. BUSINESS METHODS
    # ------------------------------------------------------------------

    @api.model
    @api.readonly
    def get_views(self, views, options=None):
        result = super(Base, self).get_views(views, options)

        # Check if any rule is defined for readonly
        model_rules = self.env["access.rule"].get_model_rules(self._name)
        if any(model_rules.mapped("readonly")):
            for view_type, view in result["views"].items():
                arch = etree.fromstring(view.get("arch"))
                arch.attrib.update(
                    {"edit": "False", "create": "False", "delete": "False"}
                )
                view["arch"] = etree.tostring(arch)

        # Record rules: control New/Edit/Delete buttons per model based on perm checkboxes
        if not any(model_rules.mapped("readonly")):
            record_rules = model_rules.record_rule_ids.filtered(
                lambda r: r.model_id.model == self._name
            )
            if record_rules:
                allow_create = any(r.perm_create for r in record_rules)
                allow_write = any(r.perm_write for r in record_rules)
                allow_unlink = any(r.perm_unlink for r in record_rules)
                for view_type, view in result["views"].items():
                    arch = etree.fromstring(view.get("arch"))
                    modified = False
                    if not allow_create:
                        arch.attrib["create"] = "False"
                        modified = True
                    if not allow_write:
                        arch.attrib["edit"] = "False"
                        modified = True
                    if not allow_unlink:
                        arch.attrib["delete"] = "False"
                        modified = True
                    if modified:
                        view["arch"] = etree.tostring(arch)

        # Restrict debug mode
        if any(model_rules.mapped("restrict_debug_mode")):
            request.session.debug = ""

        return result

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super(Base, self)._get_view(view_id, view_type, **options)

        # Fetch rules for the model
        model_rules = self.env["access.rule"].get_model_rules(self._name).sudo()

        # Filter field access by current model
        field_access_ids = model_rules.field_access_ids.filtered(
            lambda fa: fa.model_id.model == self._name
        )
        if field_access_ids:
            field_access_map = dict(
                [(fa.field_id.name, fa.access) for fa in field_access_ids]
            )
            # Make fields readonly/hidden
            for field, access in field_access_map.items():
                for f in arch.xpath(f"//field[@name='{field}']"):
                    if view_type == "list" and access == "invisible":
                        access = "column_invisible"

                    f.attrib[access] = "1"

        # Check if any button, page, or link is hidden (filtered by current model)
        nodes_to_hide = (
            model_rules.hide_link_ids.filtered(lambda r: r.model_id.model == self._name)
            | model_rules.hide_page_ids.filtered(
                lambda r: r.model_id.model == self._name
            )
            | model_rules.hide_button_ids.filtered(
                lambda r: r.model_id.model == self._name
            )
        ).view_node_id
        for node in nodes_to_hide:
            if node.node_option == "button":
                xpath = f"//button[@name='{node.name}']"

                for button in arch.xpath(xpath):
                    button.attrib["invisible"] = "1"
            elif node.node_option == "page":
                for page in arch.xpath(f"//page[@name='{node.name}']"):
                    page.attrib["invisible"] = "1"
            elif node.node_option == "link":
                for link in arch.xpath(f"//"):
                    link.attrib["invisible"] = "1"

        if view_type in [
            "kanban",
            "list",
        ] and not self.env.user.check_export_enable(self._name):
            for list in arch.xpath("//list"):
                list.attrib["export_xlsx"] = "0"

        # Hide Import records
        if self.env.user.check_import_enabled(self._name):
            arch.attrib["import"] = "0"

        # Record rules: control New/Edit/Delete per model (when called directly)
        if not any(model_rules.mapped("readonly")):
            record_rules = model_rules.record_rule_ids.filtered(
                lambda r: r.model_id.model == self._name
            )
            if record_rules:
                if not any(r.perm_create for r in record_rules):
                    arch.attrib["create"] = "False"
                if not any(r.perm_write for r in record_rules):
                    arch.attrib["edit"] = "False"
                if not any(r.perm_unlink for r in record_rules):
                    arch.attrib["delete"] = "False"

        return arch, view
