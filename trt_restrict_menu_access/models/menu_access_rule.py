# -*- coding: utf-8 -*-
from odoo import api, fields, models

class MenuAccessRule(models.Model):
    """
    Custom model to restrict menus dynamically based on groups.
    Each record links a group to one or more menus that should be hidden
    if the user belongs to that group.
    """
    _name = 'menu.access.rule'
    _description = 'Menu Access Rule'

    name = fields.Char(required=True, string="Rule Name")
    group_id = fields.Many2one(
        'res.groups',
        string="Restricted Group",
        help="If a user belongs to this group, the selected menus will be hidden."
    )
    allow_group_id = fields.Many2one(
        'res.groups',
        string="Allowed Group",
        help="If a user also belongs to this group, the restriction will not apply."
    )
    menu_ids = fields.Many2many(
        'ir.ui.menu',
        string="Menus to Hide",
        help="Select the menus that should be hidden for this group."
    )
    active = fields.Boolean(
        string="Active",
        default=True,
        help="If unchecked, this rule will be ignored."
    )
    based_option = fields.Selection([
        ('groups', 'Groups'), ('users', 'Users')
    ], string="Based on", default="groups")
    user_ids = fields.Many2many("res.users", string="Users")
