{
    "name": "Roomdoo pms FastAPI customizations",
    "version": "16.0.1.0.0",
    "development_status": "Beta",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "category": "Generic Modules/Property Management System",
    "license": "AGPL-3",
    "depends": [
        "pms_fastapi",
        "pms_partner_type_residence",
        "kellys_daily_report",
        "cash_daily_report",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings.xml",
        "views/auth_jwt_validator.xml",
        "views/res_partner_id_category.xml",
    ],
}
