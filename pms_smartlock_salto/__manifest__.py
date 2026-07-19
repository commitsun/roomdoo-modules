{
    "name": "PMS Smartlock Salto",
    "version": "16.0.1.0.0",
    "category": "Hotel/PMS",
    "author": "Commitsun",
    "license": "AGPL-3",
    "website": "https://github.com/commitsun/roomdoo-smartlocks",
    "summary": "Salto KS smartlock provider for the PMS",
    "depends": [
        "pms_smartlock_base",
    ],
    "external_dependencies": {
        "python": ["roomdoo_locks_salto"],
    },
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/lock_vendor_views.xml",
        "views/lock_code_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
