# -*- coding: utf-8 -*-
# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
User Access Studio: browser smoke test.

Every other test in this suite calls model methods directly. That is
exactly why a defect in how the WEB CLIENT reaches them can ship green:
an override that loses @api.model makes call_kw read the domain as
record ids, and every list and kanban view fails for every user while
the server-side suite still passes.

This test runs Odoo's own headless Chrome as a restricted user and walks
the screens a profile touches, so that class of regression fails here.
"""
from datetime import timedelta
import json

from odoo import fields, release
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestAccessStudioUI(HttpCase):

    def _profiled_user(self):
        groups = [self.env.ref("base.group_user").id]
        manager = self.env.ref("base.group_partner_manager", raise_if_not_found=False)
        if manager:
            groups.append(manager.id)
        field_name = "group_ids" if "group_ids" in self.env["res.users"]._fields else "groups_id"
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Access Studio Smoke",
            "login": "eh_access_smoke",
            "password": "eh_access_smoke",
            "email": "eh_access_smoke@example.test",
            field_name: [(6, 0, groups)],
        })
        user.partner_id.email = "eh_access_smoke@example.test"
        partner_model = self.env["ir.model"]._get_id("res.partner")
        email_field = self.env["ir.model.fields"].search([
            ("model", "=", "res.partner"), ("name", "=", "email"),
        ], limit=1)
        self.env["eh.access.profile"].create({
            "name": "Smoke Profile",
            "user_ids": [(6, 0, [user.id])],
            "hide_send_message": True,
            "hide_custom_filter": True,
            "hide_custom_group": True,
            "field_line_ids": [(0, 0, {
                "model_id": partner_model,
                "field_ids": [(6, 0, email_field.ids)],
                "invisible": True,
            })],
        })
        # A contact to open, and one the list can show.
        self.env["res.partner"].create({
            "name": "Smoke Contact", "email": "smoke.contact@example.test",
        })
        self.env.cr.flush()
        return user

    def test_the_screens_a_profile_touches_still_render(self):
        self._profiled_user()
        action = "base.action_partner_form"
        # The /odoo/... URL scheme arrived with Odoo 18; 16 and 17 use
        # the hash route.
        if release.version_info[0] >= 18:
            url = "/odoo/action-%s?view_type=list" % action
        else:
            url = "/web#action=%s&view_type=list" % action
        self.start_tour(
            url, "eh_access_studio_smoke",
            login="eh_access_smoke", timeout=90,
        )

    def test_signing_in_still_works_on_every_series(self):
        """_login is a classmethod with different arguments before Odoo
        19. An override with the wrong shape does not merely miss the
        gate: nobody can sign in at all."""
        self._profiled_user()
        self.authenticate("eh_access_smoke", "eh_access_smoke")
        self.assertTrue(self.session.uid)
        self.assertEqual(
            self.env["res.users"].browse(self.session.uid).login, "eh_access_smoke",
        )

    def _json_rpc(self, path, params):
        response = self.url_open(path, data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "id": 1, "params": params,
        }), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 200, response.text[:500])
        self.assertIn("application/json", response.headers.get("Content-Type", ""), response.text[:500])
        return response.json()

    def test_real_dataset_rpc_scrubs_hidden_fields_and_refuses_changes(self):
        """A routed browser request must reach the same data gate as ORM calls."""
        self._profiled_user()
        partner = self.env["res.partner"].search([("name", "=", "Smoke Contact")], limit=1)
        self.authenticate("eh_access_smoke", "eh_access_smoke")
        for path in ("/web/dataset/call_kw", "/web/dataset/call_kw/res.partner/read"):
            reply = self._json_rpc(path, {
                "model": "res.partner", "method": "read",
                "args": [partner.ids, ["name", "email"]], "kwargs": {},
            })
            self.assertNotIn("error", reply, reply)
            self.assertEqual(reply["result"][0]["name"], "Smoke Contact")
            self.assertFalse(reply["result"][0]["email"])
        for method, args in (
            ("write", [partner.ids, {"email": "forbidden.rpc@example.test"}]),
            ("export_data", [partner.ids, ["name", "email"]]),
        ):
            reply = self._json_rpc("/web/dataset/call_kw", {
                "model": "res.partner", "method": method, "args": args,
                "kwargs": {"context": {"eh_access_studio_bypass": True}},
            })
            self.assertNotIn("result", reply, reply)
            self.assertEqual(reply["error"]["data"]["name"], "odoo.exceptions.AccessError")
        self.env.invalidate_all()
        self.assertEqual(partner.email, "smoke.contact@example.test")

    def test_real_authentication_refuses_a_disabled_account(self):
        user = self._profiled_user()
        params = {"db": self.env.cr.dbname, "login": user.login, "password": "eh_access_smoke"}
        allowed = self._json_rpc("/web/session/authenticate", params)
        self.assertNotIn("error", allowed, allowed)
        self.assertEqual(allowed["result"]["uid"], user.id)
        user.access_profile_ids.write({"disable_login": True})
        self.env.cr.flush()
        ended = self._json_rpc("/web/session/authenticate", params)
        self.assertNotIn("result", ended, ended)
        self.assertEqual(ended["error"]["data"]["name"].rsplit(".", 1)[-1], "SessionExpiredException")
        # Test a genuinely fresh browser session separately. Older Odoo
        # HTTP test servers can retain the old cookie after an exceptional
        # response; replaying it would only retest the existing-session gate.
        for cookie in list(self.opener.cookies):
            if cookie.name == "session_id":
                self.opener.cookies.clear(cookie.domain, cookie.path, cookie.name)
        self.assertNotIn("session_id", self.opener.cookies.get_dict())
        # Correct credentials still cannot create a new session.
        blocked = self._json_rpc("/web/session/authenticate", params)
        self.assertNotIn("result", blocked, blocked)
        self.assertEqual(blocked["error"]["data"]["name"], "odoo.exceptions.AccessDenied")

    def test_the_chatter_route_is_gated_however_the_path_is_spelled(self):
        """End to end over HTTP: Odoo ignores a trailing slash when it
        routes, so every spelling of the route must meet the same gate."""
        import json
        user = self._profiled_user()
        profile = self.env["eh.access.profile"].search([("name", "=", "Smoke Profile")])
        profile.write({"hide_send_message": True, "hide_log_note": True})
        partner = self.env["res.partner"].search([("name", "=", "Smoke Contact")], limit=1)
        self.env.cr.flush()
        self.authenticate("eh_access_smoke", "eh_access_smoke")
        Message = self.env["mail.message"]
        for path in ("/mail/message/post", "/mail/message/post/", "//mail/message/post"):
            before = Message.search_count([
                ("model", "=", "res.partner"), ("res_id", "=", partner.id),
            ])
            self.url_open(path, data=json.dumps({
                "jsonrpc": "2.0", "method": "call", "params": {
                    "thread_model": "res.partner", "thread_id": partner.id,
                    "post_data": {
                        "body": "through %s" % path, "message_type": "comment",
                        "subtype_xmlid": "mail.mt_comment",
                    },
                },
            }), headers={"Content-Type": "application/json"})
            self.env.cr.flush()
            self.env.invalidate_all()
            self.assertEqual(
                Message.search_count([
                    ("model", "=", "res.partner"), ("res_id", "=", partner.id),
                ]),
                before,
                "a message was posted through %s although the profile"
                " hides both message kinds" % path,
            )
        self.assertTrue(user)


@tagged("post_install", "-at_install")
class TestAccessStudioGovernanceUI(HttpCase):
    """Exercise native views and buttons as their real, non-admin users."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_field = "group_ids" if "group_ids" in cls.env["res.users"]._fields else "groups_id"
        internal = cls.env.ref("base.group_user")
        manager = cls.env.ref("eh_access_studio.group_eh_access_manager")
        company = cls.env.company

        def make_user(login, groups):
            return cls.env["res.users"].with_context(no_reset_password=True).create({
                "name": login, "login": login, "password": login,
                "email": "%s@example.test" % login,
                "lang": "en_US", "company_id": company.id,
                "company_ids": [(6, 0, [company.id])],
                group_field: [(6, 0, groups.ids)],
            })

        cls.manager = make_user("eh_governance_manager", internal | manager)
        cls.employee = make_user("eh_governance_employee", internal)
        cls.other_employee = make_user("eh_governance_other", internal)
        cls.role = cls.env["eh.access.role"].create({
            "name": "Browser requestable role", "company_id": company.id,
            "requestable": True,
        })

    @staticmethod
    def _action_url(action):
        if release.version_info[0] >= 18:
            return "/odoo/action-%s" % action
        return "/web#action=%s" % action

    def test_manager_overview_renders_operational_counts(self):
        self.env.cr.flush()
        self.start_tour(
            self._action_url("eh_access_studio.action_eh_access_dashboard"),
            "eh_access_studio_overview", login=self.manager.login, timeout=90,
        )

    def test_manager_inspector_runs_and_renders_evidence(self):
        inspector = self.env["eh.access.inspector"].with_user(self.manager).create({
            "user_id": self.employee.id,
            "model_id": self.env["ir.model"]._get_id("res.partner"),
            "record_id": self.employee.partner_id.id,
            "company_id": self.env.company.id, "operation": "read",
        })
        # Use the actual inspector form with a saved question owned by this
        # manager; the browser still invokes action_inspect over normal RPC.
        action = self.env["ir.actions.act_window"].create({
            "name": "Browser Access Inspection", "res_model": inspector._name,
            "res_id": inspector.id, "view_mode": "form", "target": "current",
        })
        self.env.cr.flush()
        self.start_tour(
            self._action_url(action.id), "eh_access_studio_inspector",
            login=self.manager.login, timeout=90,
        )
        self.env.invalidate_all()
        self.assertEqual(inspector.decision, "allowed")
        self.assertIn("Effective Odoo groups", inspector.result_html)

    def test_manager_ai_draft_form_renders_without_provider_call(self):
        self.env.cr.flush()
        self.start_tour(
            self._action_url("eh_access_studio.action_eh_access_ai_wizard"),
            "eh_access_studio_ai_draft", login=self.manager.login, timeout=90,
        )

    def test_employee_requests_render_without_manager_overview(self):
        def create_request(user, reason):
            return self.env["eh.access.request"].with_user(user).create({
                "role_id": self.role.id, "reason": reason,
                "starts_at": fields.Datetime.now(),
                "ends_at": fields.Datetime.now() + timedelta(days=1),
            })

        request = create_request(self.employee, "Own browser request")
        create_request(self.other_employee, "Other employee's private request")
        self.env.cr.flush()
        self.start_tour(
            self._action_url("eh_access_studio.action_eh_access_request"),
            "eh_access_studio_employee_request", login=self.employee.login, timeout=90,
        )
        self.env.invalidate_all()
        self.assertEqual(request.state, "submitted")
        self.assertFalse(request.assignment_ids)
