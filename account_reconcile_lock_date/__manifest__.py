{
    "name": "Account Reconcile Lock Date",
    "summary": "Enforce the closing date when undoing reconciliations or "
    "unposting entries.",
    "version": "16.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": ["account"],
    "data": [
        "security/account_reconcile_lock_date_groups.xml",
        "views/res_config_settings.xml",
    ],
    "installable": True,
    "application": False,
}
