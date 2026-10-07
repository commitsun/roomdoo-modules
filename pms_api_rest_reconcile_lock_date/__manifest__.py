# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "PMS API REST - Reconcile Lock Date",
    "summary": "Edit invoices and payments from the app without touching closed "
    "periods",
    "version": "16.0.1.0.0",
    "category": "Generic Modules/Property Management System",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": [
        "pms_api_rest",
        "account_reconcile_lock_date",
        "pms_autoreconcile_folio_payments",
    ],
    "auto_install": True,
    "installable": True,
}
