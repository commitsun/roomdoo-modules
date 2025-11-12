{
    "name": "PMS FastAPI",
    "version": "16.0.1.0.0",
    "development_status": "Beta",
    "author": "Commit [Sun], Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/pms",
    "category": "Generic Modules/Property Management System",
    "license": "AGPL-3",
    "depends": [
        "extendable_fastapi",
        "auth_jwt_login",
        "partner_firstname",
        "phone_validation",
        "account_payment_partner",
        "pms_api_rest",  # temporal
        "pms_l10n_es",  # temporal
        "partner_identification_map_partner_field",
    ],
    "data": [
        "security/pms_fastapi_groups.xml",
        "data/res_users.xml",
    ],
}
