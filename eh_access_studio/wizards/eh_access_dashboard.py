"""Actionable governance overview; counts describe work, not a risk score."""
from datetime import timedelta
from odoo import _, api, fields, models, release
from odoo.exceptions import AccessError


class EhAccessDashboard(models.TransientModel):
    _name = 'eh.access.dashboard'
    _description = 'Access Studio Overview'

    pending_count = fields.Integer(compute='_compute_counts', string='Awaiting decision')
    expiring_count = fields.Integer(compute='_compute_counts', string='Expire within 24 hours')
    overdue_count = fields.Integer(compute='_compute_counts', string='Expiry not reconciled')
    draft_count = fields.Integer(compute='_compute_counts', string='Inactive profiles')
    role_count = fields.Integer(compute='_compute_counts', string='Active roles')
    audit_ids = fields.Many2many('eh.access.audit', compute='_compute_counts', string='Recent governance events')

    def _require_manager(self):
        if not self.env.su and not self.env.user.has_group('eh_access_studio.group_eh_access_manager'):
            raise AccessError(_('Only Access Studio managers can open this overview.'))

    def _domains(self):
        now = fields.Datetime.now()
        return {
            'pending': [('state', '=', 'submitted'), ('user_id', '!=', self.env.uid)],
            'expiring': [('revoked', '=', False), ('ends_at', '>', now), ('ends_at', '<=', now + timedelta(days=1))],
            'overdue': [('applied_state', '=', 'active'), ('ends_at', '<=', now)],
            'draft': [('active', '=', False)], 'role': [('active', '=', True)],
        }

    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_counts(self):
        self._require_manager()
        domains = self._domains()
        values = {
            'pending_count': self.env['eh.access.request'].search_count(domains['pending']),
            'expiring_count': self.env['eh.access.assignment'].search_count(domains['expiring']),
            'overdue_count': self.env['eh.access.assignment'].search_count(domains['overdue']),
            'draft_count': self.env['eh.access.profile'].with_context(active_test=False).search_count(domains['draft']),
            'role_count': self.env['eh.access.role'].search_count(domains['role']),
            'audit_ids': self.env['eh.access.audit'].search([], limit=20),
        }
        for record in self:
            for key, value in values.items():
                record[key] = value

    def _open(self, model, name, key=None):
        self._require_manager()
        return {'type': 'ir.actions.act_window', 'res_model': model, 'name': name,
                'view_mode': 'tree,form' if release.version_info[0] < 18 else 'list,form',
                'domain': self._domains().get(key, []), 'context': {'active_test': False} if key == 'draft' else {}}

    def action_pending(self):
        return self._open('eh.access.request', _('Pending Access Requests'), 'pending')

    def action_expiring(self):
        return self._open('eh.access.assignment', _('Expiring Assignments'), 'expiring')

    def action_overdue(self):
        return self._open('eh.access.assignment', _('Expiry Reconciliation Required'), 'overdue')

    def action_drafts(self):
        return self._open('eh.access.profile', _('Inactive Policy Drafts'), 'draft')

    def action_roles(self):
        return self._open('eh.access.role', _('Active Roles'), 'role')
