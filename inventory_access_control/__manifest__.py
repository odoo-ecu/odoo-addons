{
    "name": "Inventory Access Control",
    "version": "19.0.1.0.0",
    "summary": "Restrict warehouse operations by allowed locations and control transfer validation",
    "category": "Inventory/Inventory",
    "author": "Merodev Software",
    "website": "https://www.merodev.com/",
    "license": "LGPL-3",
    "depends": ["stock"],

    "images": [
        "static/description/icon_banner.png",
    ],

    "data": [
        "security/groups.xml",
        "security/stock_security.xml",
        "views/res_users_views.xml",
        "views/product_views.xml",
    ],

    "installable": True,
    "application": False,
}