from lxml import etree

from odoo import api, fields, models


class AccessHideViewNode(models.Model):
    # ------------------------------------------------------------------
    # 1. PRIVATE ATTRIBUTES
    # ------------------------------------------------------------------

    _name = "access.hide.view.node"
    _description = "Access Hide View Nodes"

    # ------------------------------------------------------------------
    # 2. DEFAULT METHODS AND default_get
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. FIELD DECLARATIONS
    # ------------------------------------------------------------------

    access_rule_id = fields.Many2one("access.rule", "Access Rule", ondelete="cascade")
    model_id = fields.Many2one("ir.model", "Model", required=True, ondelete="cascade")
    view_node_id = fields.Many2one("view.node", "View Node")
    is_smart_button = fields.Boolean(
        "Is Smart Button?", related="view_node_id.is_smart_button"
    )

    access_rule_btn_id = fields.Many2one(
        "access.rule", "Hide Button Rule", ondelete="cascade"
    )
    access_rule_page_id = fields.Many2one(
        "access.rule", "Hide Page Rule", ondelete="cascade"
    )

    # ------------------------------------------------------------------
    # 4. COMPUTE, INVERSE AND SEARCH METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 5. SELECTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 6. CONSTRAINS METHODS AND ONCHANGE METHODS
    # ------------------------------------------------------------------

    @api.model
    @api.onchange("model_id", "access_rule_id")
    def _get_button(self):
        View_node = self.env["view.node"]
        view_obj = self.env["ir.ui.view"]

        if self.model_id:

            view_list = ["form", "list", "kanban"]
            for view in view_list:
                for views in view_obj.search(
                    [("model", "=", self.model_id.model), ("type", "=", view)]
                ):
                    res = (
                        self.env[self.model_id.model]
                        .sudo()
                        .get_view(view_id=views.id, view_type=view)
                    )
                    arch = etree.XML(res["arch"])

                    object_link = arch.xpath("//a")
                    for btn in object_link:
                        if (
                            btn.text
                            and "\n" not in btn.text
                            and "type" in btn.attrib.keys()
                            and btn.attrib["type"]
                            and "name" in btn.attrib.keys()
                            and btn.attrib["name"]
                        ):
                            domain = [
                                ("button_type", "=", btn.get("type")),
                                ("node_string", "=", btn.text),
                                ("name", "=", btn.get("name")),
                                ("model_id", "=", self.model_id.id),
                                ("node_option", "=", "link"),
                            ]
                            if not View_node.search(domain):
                                View_node.create(
                                    {
                                        "model_id": self.model_id.id,
                                        "node_option": "link",
                                        "name": btn.get("name"),
                                        "node_string": btn.text,
                                        "button_type": btn.get("type"),
                                        "lang_code": self.env.lang,
                                    }
                                )

                    object_button = arch.xpath("//button[@type='object']")
                    for btn in object_button:
                        string_value = btn.get("string")
                        if view == "kanban" and not string_value:
                            try:
                                string_value = (
                                    btn.text if not btn.text.startswith("\n") else False
                                )
                            except:
                                pass

                        if not string_value:
                            fields = btn.findall(".//*[@class='o_stat_text']")
                            if fields:
                                string_value = ""
                            for f in fields:
                                string_value += " " + f.text

                        if btn.get("name") and string_value:
                            domain = [
                                ("button_type", "=", btn.get("type")),
                                ("node_string", "=", string_value),
                                ("name", "=", btn.get("name")),
                                ("model_id", "=", self.model_id.id),
                                ("node_option", "=", "button"),
                            ]
                            if not View_node.search(domain):
                                self.with_context(
                                    string_value=string_value
                                )._store_btn_data(btn)

                    action_button = arch.xpath("//button[@type='action']")
                    for btn in action_button:
                        string_value = btn.get("string")
                        if view == "kanban" and not string_value:
                            try:
                                string_value = (
                                    btn.text if not btn.text.startswith("\n") else False
                                )
                            except:
                                pass
                        if btn.get("name") and string_value:
                            domain = [
                                ("button_type", "=", btn.get("type")),
                                ("node_string", "=", string_value),
                                ("name", "=", btn.get("name")),
                                ("model_id", "=", self.model_id.id),
                                ("node_option", "=", "button"),
                            ]
                            if not View_node.search(domain):
                                self.with_context(
                                    string_value=string_value
                                )._store_btn_data(btn)

                    if view == "form":
                        ## Smart Buttons Extraction
                        smt_button_division = arch.xpath(
                            "//div[@class='oe_button_box']"
                        )
                        if smt_button_division:
                            smt_button_division = etree.tostring(smt_button_division[0])
                            smt_button_division = etree.XML(smt_button_division)

                            smt_object_button = smt_button_division.xpath(
                                "//button[@type='object']"
                            )
                            self._get_smart_btn_string(smt_object_button, type="object")

                            smt_action_button = smt_button_division.xpath(
                                "//button[@type='action']"
                            )
                            self._get_smart_btn_string(smt_action_button, type="action")

                        ## Tab Extraction
                        page_list = arch.xpath("//page")
                        if page_list:
                            for page in page_list:
                                if page.get("string"):
                                    domain = [
                                        (
                                            "node_string",
                                            "=",
                                            page.get("string"),
                                        ),
                                        ("model_id", "=", self.model_id.id),
                                        ("node_option", "=", "page"),
                                    ]
                                    if page.get("name"):
                                        domain += [("name", "=", page.get("name"))]
                                    store_model_nodes_id = View_node.search(
                                        domain, limit=1
                                    )
                                    if not store_model_nodes_id:
                                        View_node.create(
                                            {
                                                "model_id": self.model_id.id,
                                                "name": page.get("name"),
                                                "node_string": page.get("string"),
                                                "node_option": "page",
                                                "lang_code": self.env.lang,
                                            }
                                        )
                        if self.model_id.model == "res.config.settings":
                            for setting_page in arch.xpath("//app"):
                                if setting_page.get("string"):
                                    domain = [
                                        (
                                            "node_string",
                                            "=",
                                            setting_page.get("string"),
                                        ),
                                        ("model_id", "=", self.model_id.id),
                                        ("node_option", "=", "page"),
                                    ]
                                    if setting_page.get("name"):
                                        domain += [
                                            (
                                                "name",
                                                "=",
                                                setting_page.get("name"),
                                            )
                                        ]
                                    store_model_nodes_id = View_node.search(
                                        domain, limit=1
                                    )
                                    if not store_model_nodes_id:
                                        View_node.create(
                                            {
                                                "model_id": self.model_id.id,
                                                "name": setting_page.get("name") or "",
                                                "node_string": setting_page.get(
                                                    "string"
                                                ),
                                                "node_option": "page",
                                                "lang_code": self.env.lang,
                                            }
                                        )

    # ------------------------------------------------------------------
    # 7. CRUD METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 8. ACTION METHODS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 9. BUSINESS METHODS
    # ------------------------------------------------------------------

    def _store_btn_data(self, btn, smart_button=False, smart_button_string=False):
        # string_value is used in case of kanban view button store,
        string_value = (
            "string_value" in self._context.keys()
            and self._context["string_value"]
            or False
        )

        View_node = self.env["view.node"]
        name = btn.get("string") or string_value
        if smart_button:
            name = smart_button_string

        View_node.create(
            {
                "model_id": self.model_id.id,
                "node_option": "button",
                "name": btn.get("name"),
                "node_string": name,
                "button_type": btn.get("type"),
                "is_smart_button": smart_button,
                "lang_code": self.env.lang,
            }
        )

    def _get_smart_btn_string(self, btn_list, type=False):
        store_model_button_obj = self.env["view.node"]

        def _get_span_text(span_list):
            name = ""
            for sp in span_list:
                if sp.text:
                    name = name + " " + sp.text
            name = name.strip()
            return name

        for btn in btn_list:
            name = ""
            field_list = btn.findall("field")
            if field_list:
                name = field_list[0].get("string")
            else:
                span_list = btn.findall("span")
                if span_list:
                    name = _get_span_text(span_list)
                else:
                    div_list = btn.findall("div")
                    if div_list:
                        span_list = div_list[0].findall("span")
                        if span_list:
                            name = _get_span_text(span_list)
            if not name:
                try:
                    name = btn.get("string")
                except:
                    pass
            if name and (type == "object" or type == "action"):
                domain = [
                    ("button_type", "=", btn.get("type")),
                    ("node_string", "=", name),
                    ("model_id", "=", self.model_id.id),
                    ("node_option", "=", "button"),
                ]
                if type == "object":
                    domain += [("name", "=", btn.get("name"))]
                if type == "action":
                    domain += [("name", "=", btn.get("name"))]
                smart_button_id = store_model_button_obj.search(domain)
                if not smart_button_id:
                    self._store_btn_data(
                        btn, smart_button=True, smart_button_string=name
                    )
                else:
                    smart_button_id[0].is_smart_button = True
