# Copyright 2009-2020 Noviat.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "AEAT Identification type and partner documents integration",
    "author": "Commitsun, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-spain",
    "category": "Generic Modules/Property Management System",
    "version": "16.0.1.1.0",
    "license": "AGPL-3",
    "depends": [
        "partner_identification",
        "l10n_es_aeat",
        "pms_l10n_es",
        "partner_identification_map_partner_field",
        "parnter_identification_unique",
    ],
    "data": ["data/res_partner_id_category.xml"],
    "installable": True,
}
