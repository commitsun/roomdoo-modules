# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "PMS Connector Channex",
    "summary": "Channel PMS connector Channex.io",
    "version": "16.0.1.0.0",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "category": "Connector",
    "author": "Roomdoo",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "depends": [
        "connector_pms",
    ],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "data/queue_data.xml",
        "views/channel_channex_backend_views.xml",
        "views/channel_channex_backend_type_views.xml",
        "views/pms_property_views.xml",
        "views/pms_room_type_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "connector_pms_channex/static/src/**/*.js",
            "connector_pms_channex/static/src/**/*.xml",
        ],
    },
}
