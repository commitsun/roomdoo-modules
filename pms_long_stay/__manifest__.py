# Copyright 2020-21 Jose Luis Algara (Alda Hotels <https://www.aldahotels.es>)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

{
    "name": "PMS Long Stay Reservations",
    "version": "16.0.1.0.0",
    "summary": "Adds long stay reservation type and configuration per room type.",
    "category": "Hotel/PMS",
    "author": "Roomdoo, Odoo Community Association (OCA)",
    "website": "https://roomdoo.com",
    "license": "AGPL-3",
    "depends": [
        "pms",
        "product",
    ],
    "data": [
        "views/pms_room_type_views.xml",
        "views/pms_property_views.xml",
        "security/ir.model.access.csv",
    ],
    "installable": True,
    "application": False,
}
