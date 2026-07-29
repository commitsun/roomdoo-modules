# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "PMS Connector Dummy",
    "summary": "Test-only second channel manager. Do not install in production.",
    "version": "16.0.1.0.0",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "category": "Connector",
    "author": "Roomdoo",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "depends": [
        "connector_pms",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/channel_dummy_backend_views.xml",
        "views/pms_room_type_views.xml",
        "views/product_pricelist_views.xml",
    ],
}
