# Copyright 2026 Roomdoo - Commit[Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Payments Report Exporter",
    "version": "16.0.1.0.0",
    "author": "Commitsun",
    "website": "https://www.roomdoo.com",
    "category": "Accounting",
    "summary": "Export a list of payments to PDF or Excel",
    "depends": [
        "account",
        "pms",
        "report_xlsx",
    ],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "report/report_payments_templates.xml",
        "report/report_data.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "AGPL-3",
}
