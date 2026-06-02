{
    "name": "PMS Long Stay Autoinvoice Bridge",
    "version": "16.0.1.0.0",
    "summary": "Schedule monthly autoinvoicing for long-stay reservations "
    "and their extra services from the previous month.",
    "category": "Hotel/PMS",
    "author": "Roomdoo, Odoo Community Association (OCA)",
    "website": "https://roomdoo.com",
    "license": "AGPL-3",
    "depends": [
        "pms_long_stay",
        "pms_autoinvoice",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": True,
}
