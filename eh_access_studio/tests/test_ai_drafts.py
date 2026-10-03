import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.addons.eh_access_studio.wizards.eh_access_ai_wizard import FLAGS, PARAM, _set_param


@tagged('post_install', '-at_install')
class TestAIDrafts(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Model = cls.env['ir.model']._get('res.partner')
        cls.Wizard = cls.env['eh.access.ai.wizard']
        config = cls.env['ir.config_parameter'].sudo()
        for key, value in {'enabled': 'True', 'model': 'test-model', 'api_key': 'unit-test-placeholder'}.items():
            _set_param(config, PARAM + key, value)
        groups = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        key = 'group_ids' if 'group_ids' in Users._fields else 'groups_id'
        cls.employee = Users.create({'name': 'AI unauthorized', 'login': 'ai_unauthorized', key: [(6, 0, groups.ids)]})

    def _draft(self, **changes):
        data = dict(explanation='Hide phone on contacts. Existing Odoo access still applies.',
                    readonly=False, hide_export=False, record_domain='[]',
                    field_rules=[dict(field='phone', hidden=True, readonly=False, required=False, condition='')],
                    **{flag: False for flag in FLAGS})
        data.update(changes)
        return json.dumps(data)

    def _wizard(self, **changes):
        vals = {'name': 'AI draft test', 'model_id': self.Model.id, 'prompt': 'Hide phone', 'consent': True}
        vals.update(changes)
        return self.Wizard.create(vals)

    def test_generation_never_changes_profiles_and_apply_creates_inactive(self):
        wizard = self._wizard()
        profiles = self.env['eh.access.profile'].with_context(active_test=False)
        count = profiles.search_count([])
        with patch.object(type(wizard), '_call_provider', return_value=self._draft()) as provider:
            wizard.action_generate()
            provider.assert_called_once()
            payload = provider.call_args.args[2]
            self.assertFalse(payload['store'])
            self.assertNotIn('tools', payload)
            sent = json.loads(payload['input'])
            self.assertEqual(set(sent), {'model', 'fields', 'request'})
            self.assertNotIn('unit-test-placeholder', payload['input'])
        self.assertEqual(profiles.search_count([]), count)
        wizard.action_create_draft()
        self.assertFalse(wizard.profile_id.active)
        self.assertFalse(wizard.profile_id.user_ids)
        self.assertTrue(wizard.profile_id.field_line_ids.invisible)
        wizard.action_create_draft()
        self.assertEqual(profiles.search_count([]), count + 1)

    def test_no_consent_means_no_external_call(self):
        wizard = self._wizard(consent=False)
        with patch.object(type(wizard), '_call_provider') as provider, self.assertRaises(UserError):
            wizard.action_generate()
        provider.assert_not_called()

    def test_employee_cannot_trigger_provider(self):
        wizard = self._wizard().with_user(self.employee)
        with patch.object(type(wizard), '_call_provider') as provider, self.assertRaises(AccessError):
            wizard.action_generate()
        provider.assert_not_called()

    def test_unknown_field_or_extra_controls_rejected(self):
        wizard = self._wizard()
        data = json.loads(self._draft())
        data['active'] = True
        with self.assertRaises(ValidationError):
            wizard._validated_draft(json.dumps(data))
        data.pop('active')
        data['field_rules'][0]['field'] = 'unknown_secret'
        with self.assertRaises(ValidationError):
            wizard._validated_draft(json.dumps(data))

    def test_condition_cannot_execute_function(self):
        wizard = self._wizard()
        data = json.loads(self._draft())
        data['field_rules'][0]['condition'] = "__import__('os').getcwd()"
        with self.assertRaises(ValidationError):
            wizard._validated_draft(json.dumps(data))

    def test_changed_model_requires_regeneration(self):
        wizard = self._wizard()
        with patch.object(type(wizard), '_call_provider', return_value=self._draft()):
            wizard.action_generate()
        wizard.model_id = self.env['ir.model']._get('res.company')
        with self.assertRaises(UserError):
            wizard.action_create_draft()

    def test_settings_key_not_exposed_and_employee_cannot_save(self):
        settings = self.env['eh.access.ai.settings'].create({})
        self.assertFalse(settings.api_key)
        self.assertTrue(settings.configured)
        with self.assertRaises(AccessError):
            settings.with_user(self.employee).action_save()

    def test_invalid_domain_cannot_leave_partial_draft(self):
        wizard = self._wizard()
        with patch.object(type(wizard), '_call_provider', return_value=self._draft(record_domain="[('missing_field_xyz', '=', 1)]")):
            wizard.action_generate()
        count = self.env['eh.access.profile'].with_context(active_test=False).search_count([])
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertEqual(self.env['eh.access.profile'].with_context(active_test=False).search_count([]), count)

    def test_generated_evidence_cannot_be_forged(self):
        wizard = self._wizard()
        with self.assertRaises(AccessError):
            wizard.with_context(_eh_ai_result_token=True).write({'draft_json': self._draft()})
        with self.assertRaises(AccessError):
            self._wizard(generation_evidence={'provider': 'forged'})
        clean = self.Wizard.with_context(default_draft_json=self._draft()).create({
            'name': 'Default injection', 'model_id': self.Model.id, 'prompt': 'Hide phone'})
        self.assertFalse(clean.draft_json)

    def test_prompt_change_requires_regeneration_and_provenance_survives_wizard(self):
        wizard = self._wizard()
        with patch.object(type(wizard), '_call_provider', return_value=self._draft()):
            wizard.action_generate()
        wizard.prompt = 'Different request'
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        wizard.prompt = 'Hide phone'
        wizard.action_create_draft()
        revision = self.env['eh.access.revision'].search([('profile_id', '=', wizard.profile_id.id)])
        wizard.unlink()
        self.assertEqual(revision.provenance['provider'], 'OpenAI Responses')
        self.assertEqual(revision.provenance['model'], 'test-model')
        self.assertEqual(len(revision.provenance['prompt_sha256']), 64)

    def test_provider_failure_and_malformed_response_never_create_profile(self):
        wizard = self._wizard()
        count = self.env['eh.access.profile'].with_context(active_test=False).search_count([])
        endpoint = 'odoo.addons.eh_access_studio.wizards.eh_access_ai_wizard.requests.post'
        for status, body in ((503, b'provider error'), (200, b'[]'), (200, b'{"status":"completed","output":false}')):
            with patch(endpoint) as post:
                response = post.return_value.__enter__.return_value
                response.status_code = status
                response.iter_content.return_value = [body]
                with self.assertRaises(UserError):
                    wizard.action_generate()
                self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(self.env['eh.access.profile'].with_context(active_test=False).search_count([]), count)
        self.assertFalse(wizard.draft_json)
