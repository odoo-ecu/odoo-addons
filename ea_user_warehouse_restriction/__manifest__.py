# -*- coding: utf-8 -*-
##############################################################################
#
#    ERP Artists
#    Copyright (C) 2025-TODAY ERP Artists (<https://www.erpartists.com>).
#    Author: ERP Artists (<https://www.erpartists.com>)
#
##############################################################################
{
    'name': "User Warehouse Restriction",
    'version': '19.0.1.0.0',
    'category': 'Warehouse',
    'summary': 'Restrict warehouse and stock location access for users by assigning allowed warehouses and locations, ensuring secure and controlled inventory operations.',
    'description': """This module helps you to restrict warehouse and stock 
     location for the specific users. So that users can only access the allowed
     warehouse and locations.""",
"description": """
User Warehouse Restriction
==========================

User Warehouse Restriction helps you control which warehouses and stock
locations each user can access.

You can assign one or more warehouses and stock locations to a user. Users
will only be able to view and perform inventory operations in their assigned
warehouses and locations. This helps improve security, prevents unauthorized
access, and reduces mistakes during inventory management.""",
    'author': 'ERP Artists',
    'website': "https://www.erpartists.com",
    'license': 'LGPL-3',
    'depends': ['stock_sms'],
    'data': [
        'security/ea_user_warehouse_restriction_groups.xml',
        'security/ea_user_warehouse_restriction_security.xml',
        'views/res_config_settings_views.xml',
        'views/stock_warehouse_views.xml',
        'views/res_users_views.xml',
    ],
    'images': [
        'static/description/banner.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'price': 0.00,
    'currency': 'USD',
}
