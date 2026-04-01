{
    "name": "PMS Televes Connector",
    "version": "16.0.1.0.0",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "category": "Property Management System",
    "summary": "Integration between PMS Roomdoo and Televes/Arantia ATV3 IPTV system",
    "depends": ["pms"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "data/ir_cron.xml",
        "views/pms_property_views.xml",
        "views/pms_room_views.xml",
    ],
    "installable": True,
    "application": False,
}
