{
    "name": "PMS Reservation Invoice Lock",
    "summary": "Block validation of reservation invoices until a configurable "
    "per-property domain is met (e.g. not before checkout).",
    "version": "16.0.1.0.0",
    "category": "Generic Modules/Property Management System",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": ["account", "pms"],
    "data": [
        "security/pms_security.xml",
        "views/res_config_settings.xml",
    ],
    "installable": True,
    "application": False,
}
