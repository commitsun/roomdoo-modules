# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "PMS FastAPI Booking Engine",
    "version": "16.0.1.0.0",
    "summary": "Public API of the web booking engine",
    "category": "Generic Modules/Property Management System",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": ["pms_fastapi"],
    "data": [
        "security/res_groups.xml",
        "security/ir.model.access.csv",
        "data/res_users.xml",
        "data/fastapi_endpoint.xml",
        "views/pms_property_views.xml",
    ],
}
