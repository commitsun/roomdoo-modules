{
    "name": "PMS Smartlock TTLock",
    "version": "16.0.1.0.0",
    "category": "Hotel/PMS",
    "author": "Commitsun",
    "license": "AGPL-3",
    "website": "https://github.com/commitsun/roomdoo-smartlocks",
    "summary": "TTLock smartlock provider for the PMS",
    "depends": [
        "pms_smartlock_base",
    ],
    "external_dependencies": {
        "python": ["roomdoo_locks_ttlock"],
    },
    "data": [
        "views/lock_vendor_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
