# Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Governance security and grant-ownership regression tests."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from ..models.eh_access_role import _group_field


@tagged("post_install", "-at_install", "eh_access_governance")
class TestAccessGovernance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gfield = _group_field(cls.env)
        cls.internal = cls.env.ref("base.group_user")
        cls.manager_group = cls.env.ref("eh_access_studio.group_eh_access_manager")
        cls.native_group = cls.env["res.groups"].create({"name": "Governance fixture business permission"})
        cls.company = cls.env.company
        cls.admin = cls.env.ref("base.user_admin")
        User = cls.env["res.users"].with_context(no_reset_password=True)

        def user(name, groups):
            return User.create({"name": name, "login": name + "@governance.test",
                                "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])],
                                cls.gfield: [(6, 0, groups.ids)]})

        cls.employee = user("requester", cls.internal)
        cls.other = user("other", cls.internal)
        cls.manager = user("reviewer", cls.internal | cls.manager_group | cls.native_group)
        cls.profile = cls.env["eh.access.profile"].create({
            "name": "Governance fixture profile", "apply_to_all_companies": False,
            "company_ids": [(6, 0, [cls.company.id])], "hide_export": True,
        })
        cls.role = cls.env["eh.access.role"].with_user(cls.admin).create({
            "name": "Governance fixture role", "company_id": cls.company.id, "requestable": True,
            "group_ids": [(6, 0, [cls.native_group.id])], "profile_ids": [(6, 0, [cls.profile.id])],
        })

    def _assignment(self, user=None, role=None, **values):
        return self.env["eh.access.assignment"].with_user(self.manager).create(dict({
            "role_id": (role or self.role).id, "user_id": (user or self.employee).id,
            "starts_at": fields.Datetime.now() - timedelta(minutes=1),
            "ends_at": fields.Datetime.now() + timedelta(hours=1), "reason": "Cover a business task",
        }, **values))

    def _request(self, user=None):
        return self.env["eh.access.request"].with_user(user or self.employee).create({
            "role_id": self.role.id, "reason": "I need access for month end",
            "starts_at": fields.Datetime.now() - timedelta(minutes=1),
            "ends_at": fields.Datetime.now() + timedelta(hours=1),
        })

    def test_overlapping_grants_revoke_only_last_owner(self):
        first = self._assignment()
        second = self._assignment()
        self.assertIn(self.native_group, self.employee[self.gfield])
        self.assertIn(self.employee, self.profile.user_ids)
        first.action_revoke()
        self.assertIn(self.native_group, self.employee[self.gfield])
        self.assertIn(self.employee, self.profile.user_ids)
        second.action_revoke()
        self.assertNotIn(self.native_group, self.employee[self.gfield])
        self.assertNotIn(self.employee, self.profile.user_ids)

    def test_uninstall_releases_owned_access_and_preserves_independent_permissions(self):
        from .. import uninstall_hook

        owned_baseline = set(self.employee[self.gfield].ids)
        owned = self._assignment() | self._assignment()
        self.other.write({self.gfield: [(4, self.native_group.id)]})
        self.profile.write({"user_ids": [(4, self.other.id)]})
        manual_baseline = set(self.other[self.gfield].ids)
        baseline = self._assignment(user=self.other)
        wrapper = self.env["res.groups"].create({"name": "Manual access survives uninstall",
                                                 "implied_ids": [(4, self.native_group.id)]})
        implied_user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Independently implied", "login": "implied-uninstall@governance.test",
            "company_id": self.company.id, "company_ids": [(6, 0, [self.company.id])],
            self.gfield: [(6, 0, (self.internal | wrapper).ids)],
        })
        implied_baseline = set(implied_user[self.gfield].ids)
        implied = self._assignment(user=implied_user)
        with self.assertRaises(AccessError):
            self.env["eh.access.assignment"].with_user(self.manager)._uninstall_cleanup()

        uninstall_hook(self.env)
        uninstall_hook(self.env)  # repeated cleanup must be harmless
        self.assertTrue(all((owned | baseline | implied).mapped("revoked")))
        self.assertNotIn(self.native_group, self.employee[self.gfield])
        self.assertNotIn(self.employee, self.profile.user_ids)
        self.assertIn(self.native_group, self.other[self.gfield])
        self.assertIn(self.other, self.profile.user_ids)
        self.assertIn(wrapper, implied_user[self.gfield])
        effective = "all_group_ids" if "all_group_ids" in implied_user._fields else self.gfield
        self.assertIn(self.native_group, implied_user[effective])
        self.assertNotIn(implied_user, self.profile.user_ids)
        self.assertEqual(set(self.employee[self.gfield].ids), owned_baseline)
        self.assertEqual(set(self.other[self.gfield].ids), manual_baseline)
        self.assertEqual(set(implied_user[self.gfield].ids), implied_baseline)

    def test_preexisting_manual_memberships_survive(self):
        self.employee.write({self.gfield: [(4, self.native_group.id)]})
        self.profile.write({"user_ids": [(4, self.employee.id)]})
        assignment = self._assignment()
        assignment.action_revoke()
        self.assertIn(self.native_group, self.employee[self.gfield])
        self.assertIn(self.employee, self.profile.user_ids)

    def test_explicit_manual_add_during_grant_survives(self):
        assignment = self._assignment()
        self.employee.write({self.gfield: [(4, self.native_group.id)]})
        self.profile.write({"user_ids": [(4, self.employee.id)]})
        assignment.action_revoke()
        self.assertIn(self.native_group, self.employee[self.gfield])
        self.assertIn(self.employee, self.profile.user_ids)

    def test_form_set_does_not_convert_temporary_access_to_manual(self):
        assignment = self._assignment()
        self.employee.write({self.gfield: [(6, 0, self.employee[self.gfield].ids)]})
        self.profile.write({"user_ids": [(6, 0, self.profile.user_ids.ids)]})
        assignment.action_revoke()
        self.assertNotIn(self.native_group, self.employee[self.gfield])
        self.assertNotIn(self.employee, self.profile.user_ids)

    def test_later_manual_implying_group_survives_role_expiry(self):
        assignment = self._assignment()
        manual_wrapper = self.env["res.groups"].create({"name": "Independent manual wrapper",
                                                       "implied_ids": [(4, self.native_group.id)]})
        self.employee.write({self.gfield: [(4, manual_wrapper.id)]})
        assignment.action_revoke()
        effective = "all_group_ids" if "all_group_ids" in self.employee._fields else self.gfield
        self.assertIn(self.native_group, self.employee[effective])
        if effective != self.gfield:
            self.assertNotIn(self.native_group, self.employee[self.gfield],
                             "An independently implied permission must not become a permanent direct grant.")
        self.employee.write({self.gfield: [(3, manual_wrapper.id)]})
        self.assertNotIn(self.native_group, self.employee[effective])

    def test_manual_removal_cannot_erase_an_active_assignment(self):
        assignment = self._assignment()
        self.employee.write({self.gfield: [(3, self.native_group.id)]})
        self.profile.write({"user_ids": [(3, self.employee.id)]})
        self.assertIn(self.native_group, self.employee[self.gfield])
        self.assertIn(self.employee, self.profile.user_ids)
        assignment.action_revoke()
        self.assertNotIn(self.native_group, self.employee[self.gfield])

    def test_future_grants_and_expiry_reconcile(self):
        start = fields.Datetime.now() + timedelta(hours=1)
        end = start + timedelta(hours=1)
        assignment = self._assignment(starts_at=start, ends_at=end)
        self.assertEqual(assignment.applied_state, "scheduled")
        self.assertNotIn(self.native_group, self.employee[self.gfield])
        with patch.object(fields.Datetime, "now", return_value=start):
            self.env["eh.access.assignment"]._cron_reconcile_governance()
        self.assertEqual(assignment.applied_state, "active")
        self.assertIn(self.native_group, self.employee[self.gfield])
        with patch.object(fields.Datetime, "now", return_value=end):
            self.env["eh.access.assignment"]._cron_reconcile_governance()
        self.assertEqual(assignment.applied_state, "expired")
        self.assertNotIn(self.native_group, self.employee[self.gfield])
        self.assertNotIn(self.employee, self.profile.user_ids)

    def test_request_employee_create_submit_manager_approve(self):
        request = self._request()
        request.action_submit()
        request.with_user(self.manager).action_approve()
        self.assertEqual(request.state, "approved")
        self.assertEqual(len(request.sudo().assignment_ids), 1)
        self.assertEqual(request.sudo().assignment_ids.request_id, request.sudo())
        self.assertEqual(request.sudo().assignment_ids.granted_by_id, self.manager)
        self.assertIn(self.native_group, self.employee[self.gfield])
        request.with_user(self.manager).action_revoke()
        self.assertEqual(request.state, "revoked")
        self.assertNotIn(self.native_group, self.employee[self.gfield])

    def test_request_state_identity_and_context_spoof_rejected(self):
        request = self._request()
        for vals in ({"state": "approved"}, {"user_id": self.other.id}, {"approver_id": self.manager.id}):
            with self.assertRaises(AccessError), self.cr.savepoint():
                request.with_context(_eh_governance_token=True).write(vals)
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env["eh.access.request"].with_user(self.employee).create({
                "role_id": self.role.id, "reason": "spoof", "state": "approved",
                "starts_at": fields.Datetime.now(), "ends_at": fields.Datetime.now() + timedelta(days=1),
            })
        request.action_submit()
        with self.assertRaises(AccessError), self.cr.savepoint():
            request.action_approve()
        with self.assertRaises(AccessError), self.cr.savepoint():
            request.with_user(self.other).write({"reason": "tampered"})

    def test_requests_are_private_and_no_self_approval(self):
        request = self._request()
        other_request = self._request(self.other)
        self.assertNotIn(other_request.id, self.env["eh.access.request"].with_user(self.employee).search([]).ids)
        manager_request = self._request(self.manager)
        manager_request.action_submit()
        with self.assertRaises(AccessError), self.cr.savepoint():
            manager_request.action_approve()
        request.action_submit()
        request.with_user(self.manager).action_approve()
        with self.assertRaises(UserError), self.cr.savepoint():
            request.with_user(self.manager).action_approve()

    def test_request_revocation_preserves_independent_assignment(self):
        manual = self._assignment()
        request = self._request()
        request.action_submit()
        request.with_user(self.manager).action_approve()
        request.with_user(self.manager).action_revoke()
        self.assertFalse(manual.revoked)
        self.assertIn(self.native_group, self.employee[self.gfield])
        manual.action_revoke()
        self.assertNotIn(self.native_group, self.employee[self.gfield])

    def test_reject_requires_reason_and_preserves_history(self):
        request = self._request()
        request.action_submit()
        with self.assertRaises(UserError), self.cr.savepoint():
            request.with_user(self.manager).action_reject()
        request.with_user(self.manager).write({"decision_reason": "Use a less privileged role."})
        request.with_user(self.manager).action_reject()
        self.assertEqual(request.state, "rejected")
        with self.assertRaises(AccessError), self.cr.savepoint():
            request.with_user(self.manager).unlink()

    def test_transitive_privileged_groups_require_system_admin(self):
        wrapper = self.env["res.groups"].create({"name": "Wrapped admin", "implied_ids": [(4, self.env.ref("base.group_erp_manager").id)]})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env["eh.access.role"].with_user(self.manager).create({
                "name": "Forbidden elevation", "company_id": self.company.id, "group_ids": [(4, wrapper.id)],
            })
        privileged = self.env["eh.access.role"].with_user(self.admin).create({
            "name": "Admin-only role", "company_id": self.company.id, "group_ids": [(4, wrapper.id)],
        })
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._assignment(role=privileged)
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env["eh.access.role"].with_user(self.manager).create({
                "name": "Nested elevation", "company_id": self.company.id, "included_role_ids": [(4, privileged.id)],
            })

    def test_role_cycles_and_foreign_profile_rejected(self):
        child = self.env["eh.access.role"].create({"name": "Cycle fixture", "company_id": self.company.id})
        self.role.write({"included_role_ids": [(4, child.id)]})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            child.write({"included_role_ids": [(4, self.role.id)]})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.profile.write({"apply_to_all_companies": True})

    def test_company_boundary_for_assignment_and_request(self):
        foreign = self.env["res.company"].create({"name": "Governance other company"})
        foreign_role = self.env["eh.access.role"].sudo().create({"name": "Foreign role", "company_id": foreign.id, "requestable": True})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._assignment(role=foreign_role)
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env["eh.access.request"].with_user(self.employee).create({
                "role_id": foreign_role.id, "reason": "Cross company", "starts_at": fields.Datetime.now(),
                "ends_at": fields.Datetime.now() + timedelta(days=1),
            })

    def test_inverse_group_mutation_locks_recipient_before_membership_write(self):
        """Inverse native group edits must not race role reconciliation."""
        self._assignment()
        member_field = next(name for name in ("users", "user_ids") if name in self.native_group._fields)
        cursor_type = type(self.env.cr)
        original_execute = cursor_type.execute
        row_locked = []
        relation_mutations = []

        def observed_execute(cursor, query, *args, **kwargs):
            if hasattr(query, "_sql_tuple"):
                code = query._sql_tuple[0]  # native Odoo 20
            else:
                code = query.code if hasattr(query, "code") else str(query)
            if "SELECT id FROM res_users" in code and "FOR UPDATE" in code:
                row_locked.append(True)
            if "res_groups_users_rel" in code and code.lstrip().upper().startswith(("INSERT", "DELETE", "UPDATE")):
                relation_mutations.append(bool(row_locked))
            return original_execute(cursor, query, *args, **kwargs)

        with patch.object(cursor_type, "execute", new=observed_execute):
            self.native_group.write({member_field: [(3, self.employee.id)]})
        self.assertTrue(relation_mutations, "The test must observe a real native membership mutation.")
        self.assertTrue(all(relation_mutations), "Recipient locks must precede every inverse membership mutation.")
        self.assertIn(self.native_group, self.employee[self.gfield], "The still-active role must retain its owned permission.")

    def test_audit_and_assignment_provenance_are_not_editable(self):
        assignment = self._assignment()
        audit = self.env["eh.access.audit"].search([("resource_model", "=", assignment._name), ("resource_id", "=", assignment.id)], limit=1)
        self.assertTrue(audit)
        self.assertEqual(audit.actor_id, self.manager)
        for operation in (lambda: audit.with_user(self.manager).write({"details": "forged"}),
                          lambda: audit.with_user(self.manager).unlink(),
                          lambda: assignment.with_context(_eh_governance_token=True).write({"revoked": True}),
                          lambda: assignment.unlink()):
            with self.assertRaises(AccessError), self.cr.savepoint():
                operation()

    def test_archiving_included_role_removes_its_contribution(self):
        parent = self.env["eh.access.role"].create({"name": "Parent fixture", "company_id": self.company.id,
                                                   "included_role_ids": [(4, self.role.id)]})
        assignment = self._assignment(role=parent)
        self.assertIn(self.native_group, self.employee[self.gfield])
        self.role.write({"active": False})
        self.assertNotIn(self.native_group, self.employee[self.gfield])
        self.assertNotIn(self.employee, self.profile.user_ids)
        self.assertEqual(assignment.applied_state, "active")
