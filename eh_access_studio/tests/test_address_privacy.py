from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.addons.eh_access_studio.models.base_model import _eh_rpc_scope


@tagged('post_install', '-at_install')
class TestAddressPrivacy(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = cls.env.ref('base.group_user') | cls.env.ref('base.group_partner_manager')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        key = 'group_ids' if 'group_ids' in Users._fields else 'groups_id'
        cls.user = Users.create({'name': 'Address Reader', 'login': 'address_reader', key: [(6, 0, groups.ids)]})
        cls.partner = cls.env['res.partner'].create({'name': 'Private Address', 'street': 'Secret Street 987', 'city': 'Visible City'})
        field = cls.env['ir.model.fields']._get('res.partner', 'street')
        cls.env['eh.access.profile'].create({
            'name': 'Protect street', 'user_ids': [(6, 0, cls.user.ids)],
            'field_line_ids': [(0, 0, {'model_id': cls.env['ir.model']._get_id('res.partner'),
                                     'field_ids': [(6, 0, field.ids)], 'invisible': True})],
        })

    def test_formatted_address_does_not_reveal_hidden_street(self):
        record = self.partner.with_user(self.user)
        with _eh_rpc_scope(self.user.id, 'res.partner', 'read'):
            values = record.read(['street', 'city', 'contact_address', 'display_name'])[0]
        self.assertFalse(values['street'])
        self.assertFalse(values['contact_address'])
        self.assertEqual(values['city'], 'Visible City')
        self.assertNotIn('Secret Street', str(values))

    def test_hidden_formatter_does_not_damage_saved_address(self):
        record = self.partner.with_user(self.user)
        with _eh_rpc_scope(self.user.id, 'res.partner', 'write'):
            record.write({'city': 'New City'})
        self.assertEqual(self.partner.street, 'Secret Street 987')
        self.assertEqual(self.partner.city, 'New City')

    def test_enriched_display_and_export_do_not_reveal_street(self):
        record = self.partner.with_user(self.user).with_context(show_address=True)
        with _eh_rpc_scope(self.user.id, 'res.partner', 'read'):
            values = record.read(['display_name', 'contact_address'])
        self.assertNotIn('Secret Street', str(values))
        # Export permission is native and independent from the field rule.
        group = self.env.ref('base.group_allow_export', raise_if_not_found=False)
        if group:
            key = 'group_ids' if 'group_ids' in self.user._fields else 'groups_id'
            self.user.write({key: [(4, group.id)]})
        with self.assertRaises(AccessError), _eh_rpc_scope(self.user.id, 'res.partner', 'export_data'):
            record.export_data(['contact_address', 'city'])
        with _eh_rpc_scope(self.user.id, 'res.partner', 'export_data'):
            exported = record.export_data(['city'])
        self.assertEqual(exported['datas'], [['Visible City']])

    def test_required_toggle_cannot_make_hidden_field_unfillable(self):
        profile = self.env['eh.access.profile'].search([('name', '=', 'Protect street')])
        with self.assertRaises(ValidationError):
            profile.field_line_ids.write({'required': True})
        self.assertFalse(profile.field_line_ids.required)
