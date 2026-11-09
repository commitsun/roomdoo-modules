# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "Wubook payment cards",
    "summary": "Show the payment cards Wubook keeps, without storing them",
    "version": "16.0.1.0.0",
    "category": "Generic Modules/Property Management System",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": [
        "connector_pms_wubook",
        "pms_payment_card",
    ],
    "data": [
        "security/ir.model.access.csv",
        "wizards/channel_wubook_backend_card_password_wizard_views.xml",
        "views/channel_wubook_backend_views.xml",
    ],
    "auto_install": True,
    "installable": True,
}
