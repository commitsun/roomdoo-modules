{
    "name": "PMS Satisfaction Survey",
    "summary": """
        Send Odoo satisfaction surveys to PMS guests after checkout, with
        per-property opt-in, timing configuration and folio-level traceability.
    """,
    "version": "16.0.1.0.0",
    "category": "PMS Management",
    "author": "Commitsun",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": [
        "mail",
        "pms",
        "survey",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/survey_satisfaction_data.xml",
        "views/pms_property_views.xml",
        "views/pms_folio_views.xml",
        "views/survey_user_input_views.xml",
    ],
    "installable": True,
    "application": False,
}
