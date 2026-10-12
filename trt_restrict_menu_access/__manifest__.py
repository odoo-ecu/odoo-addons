# -*- coding: utf-8 -*-
{
    'name': 'Manage Menu Access and Visibility',
    'version': '19.0.1.0',
    'summary': 'Manage menu access and visibility based on user groups',
    'description': '''Manage menu access and visibility based on user groups and users.
     This module allows administrators to control which users and groups can see specific menu items in the Odoo interface.''',
    'category': 'Tools',
    'author': 'Trineevoo Tech Solutions',
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'views/menu_access_rule_views.xml',
    ],
    "images":["static/description/img.png"],
    'installable': True,
    'auto_install': False,
    'application': True,
    'license': 'LGPL-3'
}
