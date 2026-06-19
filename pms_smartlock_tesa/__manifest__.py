{
    "name": "PMS Smartlock TESA",
    "version": "16.0.1.0.0",
    "category": "Hotel/PMS",
    "author": "Commitsun",
    "license": "AGPL-3",
    "website": "https://github.com/commitsun/roomdoo-smartlocks",
    "summary": "TESA Smartair smartlock provider for the PMS",
    "depends": [
        "pms_smartlock_base",
    ],
    "external_dependencies": {
        "python": ["roomdoo_locks_tesa"],
    },
    "data": [
        "views/lock_vendor_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
