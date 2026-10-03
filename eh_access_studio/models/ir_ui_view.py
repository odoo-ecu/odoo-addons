# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: view-arch overlay.

Per-tag post-processors hide individual nodes when an active rule
matches. Tags handled here:

* field   (invisible / readonly / required)
* label   (companion to a field that is invisible)
* button  (object / action)
* page    (notebook tab)
* a       (kanban link)
* filter  (search filter)
* group   (search group-by inside <group>)

Heavy view-arch attribute injection (create / edit / delete /
import / export_xlsx) and toolbar pruning happen on `base` directly
in models/base_model.py because they apply to the response shape, not
to a per-tag walk.

Caching: the per-tag walkers all call _eh_access_field_rules and
_eh_access_node_rules. Both are wrapped with tools.ormcache keyed on
(uid, cid, today_iso, model_name) so a form view with N field tags
issues one DB lookup, not N. The cache is invalidated on profile / line
change via eh.access.profile._invalidate_active_cache.
"""
import ast
import logging

from odoo import api, fields, models, tools

_logger = logging.getLogger(__name__)

_RELATIONAL_TTYPES = frozenset((
    "many2one", "many2many", "one2many",
))


def _safe_call(self, fetcher, label, *args):
    """Run a rule-fetcher with logged exceptions instead of raising."""
    try:
        return fetcher(*args)
    except Exception:
        _logger.exception(
            "Access Studio: %s rules lookup failed", label,
        )
        return None


class IrUiView(models.Model):
    _inherit = "ir.ui.view"

    # ---- field rules (cached) -----------------------------------------

    @api.model
    @tools.ormcache("uid", "cid", "today_iso", "model_name")
    def _eh_access_field_rules(self, uid, cid, today_iso, model_name):
        """Return {field_name: {invisible/readonly/required}} for the
        active profile set under (uid, cid, today_iso) on `model_name`.

        Caches on the four key dimensions so a form-render walks the DB
        once per (user, company, day, model).
        """
        Profile = self.env["eh.access.profile"].sudo()
        profile_ids = Profile._get_active_profile_ids(uid, cid, today_iso)
        if not profile_ids:
            return {}
        lines = self.env["eh.access.field"].sudo().search([
            ("profile_id", "in", list(profile_ids)),
            ("model_name", "=", model_name),
        ])
        rules = {}
        for line in lines:
            for field in line.field_ids:
                bucket = rules.setdefault(field.name, {
                    "invisible": False,
                    "readonly": False,
                    "required": False,
                    "hide_external_link": False,
                    "ttype": field.ttype,
                    "conditions": {},
                })
                condition = (line.condition or "").strip()
                for flag in ("invisible", "readonly", "required",
                             "hide_external_link"):
                    if not line[flag]:
                        continue
                    bucket[flag] = True
                    conditions = bucket["conditions"]
                    if flag in conditions and conditions[flag] is None:
                        # An unconditional line already applies this
                        # modifier to every record. A later conditional
                        # line must not narrow it again, or the lines'
                        # ORDER decides whether the field is locked.
                        continue
                    if condition:
                        # Several rule lines can target the same field
                        # with different conditions. Any of them firing
                        # should apply the modifier, so they are OR-ed.
                        previous = conditions.get(flag)
                        conditions[flag] = (
                            "(%s) or (%s)" % (previous, condition)
                            if previous else condition
                        )
                    else:
                        # An unconditional line wins outright: the
                        # modifier applies to every record.
                        conditions[flag] = None
        return rules

    @api.model
    @tools.ormcache("uid", "cid", "today_iso", "model_name")
    def _eh_access_node_rules(self, uid, cid, today_iso, model_name):
        """Return {kind: frozenset(target_name)} for the active set."""
        Profile = self.env["eh.access.profile"].sudo()
        profile_ids = Profile._get_active_profile_ids(uid, cid, today_iso)
        if not profile_ids:
            return {}
        lines = self.env["eh.access.node"].sudo().search([
            ("profile_id", "in", list(profile_ids)),
            ("model_name", "=", model_name),
        ])
        bucket = {}
        for line in lines:
            conditions = bucket.setdefault(line.kind, {})
            condition = (line.condition or "").strip()
            if line.target_name in conditions and conditions[line.target_name] is None:
                continue
            if not condition:
                conditions[line.target_name] = None
            else:
                previous = conditions.get(line.target_name)
                conditions[line.target_name] = (
                    "(%s) or (%s)" % (previous, condition)
                    if previous else condition
                )
        return bucket

    # ---- helpers used by the post-processors --------------------------

    def _eh_args(self):
        """Return (uid, cid, today_iso) for cache lookups."""
        Profile = self.env["eh.access.profile"].sudo()
        return (
            self.env.user.id,
            Profile._eh_safe_company_id(),
            Profile._today_for_cache_key(),
        )

    def _eh_field_rules(self, model_name):
        uid, cid, today_iso = self._eh_args()
        return _safe_call(
            self, self._eh_access_field_rules, "field",
            uid, cid, today_iso, model_name,
        )

    def _eh_node_rules(self, model_name):
        uid, cid, today_iso = self._eh_args()
        return _safe_call(
            self, self._eh_access_node_rules, "node",
            uid, cid, today_iso, model_name,
        )

    # ---- field nodes --------------------------------------------------

    def _postprocess_tag_field(self, node, name_manager, node_info):
        super()._postprocess_tag_field(node, name_manager, node_info)
        rules = self._eh_field_rules(name_manager.model._name)
        self._eh_hide_derived_field(node, name_manager, node_info)
        if not rules:
            return
        bucket = rules.get(node.get("name"))
        if not bucket:
            return
        # Odoo 17 and later read the modifier straight off the node
        # attribute. Odoo 16 builds a `modifiers` dict on node_info and
        # serialises it afterwards (transfer_modifiers_to_node), so the
        # attribute alone is ignored there. Writing both keeps one
        # source correct on every series.
        modifiers = node_info.get("modifiers")
        conditions = bucket.get("conditions") or {}
        if not self._eh_condition_supported(node_info):
            # See _eh_condition_supported: this dialect cannot carry a
            # condition, so a conditional rule is left to the server gate
            # rather than stamped as an unconditional hide, which would
            # restrict records the rule was never meant to touch.
            conditions = {
                key: value for key, value in conditions.items()
                if value is None
            }
            bucket = dict(bucket)
            for flag in ("invisible", "readonly", "required"):
                if bucket.get(flag) and flag not in conditions:
                    bucket[flag] = False
        if bucket["invisible"]:
            expression = self._eh_modifier_value(conditions.get("invisible"))
            node.set("invisible", self._eh_merge_modifier(node.get("invisible"), expression))
            if expression == "1":
                # A column can only be dropped outright when the rule
                # applies to every row; a per-record condition has to
                # stay a per-record decision.
                node.set("column_invisible", "True")
            node_info["invisible"] = True
            if isinstance(modifiers, dict):
                modifiers["invisible"] = True
                if expression == "1":
                    modifiers["column_invisible"] = True
        if bucket["readonly"]:
            node.set("readonly", self._eh_merge_modifier(
                node.get("readonly"),
                self._eh_modifier_value(conditions.get("readonly")),
            ))
            # NOTE: do NOT set force_save here. The web client strips a
            # readonly field from the write payload only when forceSave
            # is false (addons/web/static/src/model/relational_model/
            # record.js). Setting it would push the locked field back
            # into every save, which is the opposite of the rule, and
            # would then collide with the server-side field gate in
            # models/base_model.py.
            node_info["readonly"] = True
            if isinstance(modifiers, dict):
                modifiers["readonly"] = True
        if bucket["required"]:
            node.set("required", self._eh_merge_modifier(
                node.get("required"),
                self._eh_modifier_value(conditions.get("required")),
            ))
            node_info["required"] = True
            if isinstance(modifiers, dict):
                modifiers["required"] = True
        if bucket["hide_external_link"] and bucket["ttype"] in _RELATIONAL_TTYPES:
            self._eh_inject_no_link_options(node)

    @staticmethod
    def _eh_condition_supported(node_info):
        """True when this arch dialect can carry a per-record condition.

        Odoo 17 and later evaluate a python expression in a modifier, so
        the condition can be handed straight to the client. Odoo 16
        serialises modifiers into a dictionary of DOMAINS, which cannot
        express an arbitrary expression, and it raises at view
        validation if one is stamped. On that series a conditional rule
        is therefore not applied to the arch at all; it is still
        enforced on the data path, where the condition is evaluated per
        record in Python.
        """
        return not isinstance(node_info.get("modifiers"), dict)

    @staticmethod
    def _eh_modifier_value(condition):
        """The value to stamp on a modifier attribute.

        Without a condition the modifier is unconditional, which Odoo
        writes as "1". With one, the condition itself is the modifier:
        the client evaluates it per record, which is exactly how core
        expresses invisible="state == 'posted'". The view arch stays
        cached per profile, and the decision stays per record.
        """
        return condition or "1"

    def _eh_hide_derived_field(self, node, name_manager, node_info):
        """Hide the fields that CARRY a hidden value, not only the named
        ones.

        A related field mirrors the hidden one, so the data layer blanks
        it. Leaving it on the form showed an ordinary, editable field
        that was always empty, which invites the user to type into
        something the server will not keep.
        """
        name = node.get("name")
        if not name:
            return
        try:
            model = name_manager.model
            if name in (self._eh_field_rules(model._name) or {}):
                return  # a named rule decides, with its own condition
            if name not in model._eh_access_studio_hidden_names():
                return
        except Exception:
            _logger.exception(
                "Access Studio: derived-field view lookup failed",
            )
            return
        node.set("invisible", "1")
        node.set("column_invisible", "True")
        node_info["invisible"] = True
        modifiers = node_info.get("modifiers")
        if isinstance(modifiers, dict):
            modifiers["invisible"] = True
            modifiers["column_invisible"] = True

    @staticmethod
    def _eh_merge_modifier(existing, expression):
        """Combine the profile's modifier with the one the view already
        has. Replacing it threw away core's own condition, so a field or
        label core hides on some records showed there once a profile
        added a conditional rule of its own."""
        existing = (existing or "").strip()
        if expression == "1" or existing in ("", "0", "False", "false"):
            return expression
        if existing in ("1", "True", "true"):
            return existing
        return "(%s) or (%s)" % (existing, expression)

    @staticmethod
    def _eh_inject_no_link_options(node):
        """Merge the relational widget options that remove the internal
        link and the quick-create entries. Preserves any existing keys.

        The recognised option names are no_open, no_create,
        no_create_edit and no_quick_create (addons/web/static/src/views/
        fields/many2one/many2one_field.js). 'no_edit' is NOT one of
        them, so it is not emitted here.
        """
        raw = node.get("options") or ""
        existing = {}
        if raw:
            try:
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, dict):
                    existing = parsed
            except (SyntaxError, ValueError):
                # Malformed options string; leave it alone rather than
                # rewrite something we cannot parse.
                return
        existing.setdefault("no_open", True)
        existing.setdefault("no_create", True)
        existing.setdefault("no_create_edit", True)
        existing.setdefault("no_quick_create", True)
        node.set("options", str(existing))

    def _postprocess_tag_label(self, node, name_manager, node_info):
        super()._postprocess_tag_label(node, name_manager, node_info)
        target = node.get("for")
        if not target:
            return
        rules = self._eh_field_rules(name_manager.model._name)
        bucket = rules.get(target) if rules else None
        if bucket and bucket.get("invisible"):
            # The label follows its field: the same condition, combined
            # with the label's own, never an unconditional hide for a
            # rule that applies to some records only.
            condition = (bucket.get("conditions") or {}).get("invisible")
            if condition and not self._eh_condition_supported(node_info):
                return
            expression = self._eh_modifier_value(condition)
            node.set("invisible", self._eh_merge_modifier(node.get("invisible"), expression))
            if expression == "1":
                node_info["invisible"] = True

    # ---- button / page / link / filter / group ------------------------
    #
    # Odoo 19's ir.ui.view does NOT define post-processors for these
    # tags by default (only for field, label, form, list, search,
    # calendar, groupby). The framework's tag dispatcher
    # (ir_ui_view._postprocess_view, the
    # `getattr(self, f"_postprocess_tag_{tag}")` lookup) picks our
    # methods up automatically because we name them correctly. We do
    # NOT call super() because the parent has no method with these
    # names; calling super raises AttributeError.

    def _eh_hide_node(self, node, node_info, kinds, rules, target, per_record=True):
        """Hide a node, conditionally when the rule carries a condition.

        per_record is False for search-view entries: the client
        evaluates a filter's invisible against the CONTEXT, with no
        record in hand, so a condition written over the record's fields
        raises there. Those entries are hidden outright, as the
        configuration screen says.
        """
        if not target:
            return
        condition = None
        matched = False
        for kind in kinds:
            bucket = rules.get(kind) or {}
            if target not in bucket:
                continue
            matched = True
            value = bucket[target]
            if value is None:
                condition = None
                break
            condition = (
                "(%s) or (%s)" % (condition, value) if condition else value
            )
        if not matched:
            return
        if not per_record:
            condition = None
        if condition is not None and not self._eh_condition_supported(node_info):
            # Conditional node rules are a 17-and-later capability, for
            # the reason documented on _eh_condition_supported.
            return
        expression = self._eh_modifier_value(condition)
        # Combined with the node's own modifier, never replacing it: a
        # conditional rule used to un-hide a button core hides, because
        # the profile's expression overwrote core's.
        node.set("invisible", self._eh_merge_modifier(node.get("invisible"), expression))
        if expression == "1":
            node_info["invisible"] = True
        modifiers = node_info.get("modifiers")
        if isinstance(modifiers, dict):
            # Odoo 16 keeps modifiers in a dict and serialises it later.
            # A condition there is a domain, not a python expression, so
            # an unconditional hide is all that dialect can carry; the
            # conditional half is a 17+ capability and the listing says
            # so for the older series.
            modifiers["invisible"] = True

    def _postprocess_tag_button(self, node, name_manager, node_info):
        rules = self._eh_node_rules(name_manager.model._name)
        if not rules:
            return
        self._eh_hide_node(
            node, node_info, ("button",), rules, node.get("name"),
        )

    def _postprocess_tag_page(self, node, name_manager, node_info):
        rules = self._eh_node_rules(name_manager.model._name)
        if not rules:
            return
        self._eh_hide_node(
            node, node_info, ("page",), rules, node.get("name"),
        )

    def _postprocess_tag_a(self, node, name_manager, node_info):
        rules = self._eh_node_rules(name_manager.model._name)
        if not rules:
            return
        self._eh_hide_node(
            node, node_info, ("link",), rules,
            node.get("name") or node.get("data-name"),
        )

    def _postprocess_tag_filter(self, node, name_manager, node_info):
        rules = self._eh_node_rules(name_manager.model._name)
        if not rules:
            return
        # A search filter has no record to test a condition against, so
        # any condition on a filter rule is ignored by design and the
        # entry is simply hidden. Stamping the condition into the search
        # arch made the client throw when it built the dropdown.
        self._eh_hide_node(
            node, node_info, ("filter", "group"), rules, node.get("name"),
            per_record=False,
        )

