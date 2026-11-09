# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "PMS Payment Card",
    "summary": "Keep and show the payment card of a folio behind a card password",
    "version": "16.0.1.0.0",
    "category": "Generic Modules/Property Management System",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": [
        "pms",
        "pms_api_rest",
    ],
    "data": [
        "security/pms_payment_card_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "wizards/pms_payment_card_password_wizard_views.xml",
        "views/pms_property_views.xml",
        "views/pms_payment_card_access_log_views.xml",
    ],
    "installable": True,
}
