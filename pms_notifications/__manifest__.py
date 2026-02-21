{
    "name": "PMS Notifications Base",
    "summary": """
        Base notification framework for PMS
        (email templates, rules and logs), channel-agnostic.
    """,
    "version": "16.0.1.0.0",
    "category": "PMS Management",
    "author": "Commitsun, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/pms",
    "license": "AGPL-3",
    "depends": [
        "mail",
        "pms",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/pms_property_views.xml",
        "views/pms_room_type_views.xml",
        "views/pms_room_views.xml",
        "views/product_pricelist_views.xml",
        "views/pms_cancelation_rule_views.xml",
        "views/res_company_views.xml",
        "views/pms_notification_template_views.xml",
        "views/pms_property_notification_rule_views.xml",
        "views/pms_notification_log_views.xml",
        "views/pms_folio_views.xml",
        "views/pms_reservation_views.xml",
        "data/pms_notification_cron.xml",
        "data/mail_templates_pms.xml",
        "data/pms_property_notification_templates.xml",
        "data/pms_property_notification_rules.xml",
        "wizards/pms_notification_manual_send_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
