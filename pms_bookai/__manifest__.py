{
    "name": "PMS BookAI Integration",
    "summary": """
        WhatsApp notifications via BookAI API for PMS
    """,
    "version": "16.0.1.0.0",
    "category": "PMS Management",
    "author": "Commitsun, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/pms",
    "license": "AGPL-3",
    "depends": [
        "pms_notifications",
        "pms",
        "pms_fastapi",
    ],
    "data": [
        "views/pms_notification_template_views.xml",
        "views/pms_notification_log_views.xml",
        "views/pms_property_views.xml",
        "data/config_data.xml",
        "data/pms_notification_template_bookai_whatsapp.xml",
        "data/pms_property_notification_rules_bookai_whatsapp.xml",
        "security/ir.model.access.csv",
        "wizards/pms_notification_manual_send_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
