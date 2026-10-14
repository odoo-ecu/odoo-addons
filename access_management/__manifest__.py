{
    "name": "Access Management",
    "version": "18.0.2.0.0",
    "summary": "Easily create access management rules to manage the visibility of the model views, menuitems, fields and records.",
    "category": "Tools",
    "author": "Leofren Technologies",
    "maintainer": "Fenil Moradiya",
    "website": "https://leofren.com",
    "description": """
    To prevent your overhaul in managing views and visibility of menuitems, actions, fields and debug mode.
    """,
    "depends": ["base", "mail"],
    "data": [
        "security/access_rule_groups.xml",
        "security/ir.model.access.csv",
        "security/access_rule_security.xml",
        "views/access_rule_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "access_management/static/src/chatter/*",
            "access_management/static/src/import_records/*",
            "access_management/static/src/view_service.js",
            "access_management/static/src/views/list/*",
            "access_management/static/src/views/view.js",
        ]
    },
    "installable": True,
    "application": True,
    "auto_install": False,
    "images": ["static/description/icon.png"],
    "license": "LGPL-3",
}
