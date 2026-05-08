# Copyright 2026 Roomdoo - Commit[Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Invoice & Payment XLSX Report",
    "version": "16.0.2.1.0",
    "author": "Commitsun",
    "website": "https://www.roomdoo.com",
    "category": "Accounting",
    "summary": "Export invoices and payments to a multi-sheet Excel file",
    "depends": [
        "account",
        "pms",
        "report_xlsx",
    ],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/ir.model.access.csv",
        "wizard/invoices_export_wizard.xml",
        "data/menus.xml",
        "data/report_data.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "AGPL-3",
}
