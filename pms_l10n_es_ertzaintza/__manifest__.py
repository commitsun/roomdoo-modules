# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

{
    "name": "PMS Ertzaintza A19 traveller reports",
    "summary": "Send traveller reports and reservations to the Ertzaintza "
    "(Basque Country) instead of SES",
    "version": "16.0.1.0.0",
    "author": "Commit [Sun], Odoo Community Association (OCA)",
    "license": "AGPL-3",
    "category": "Generic Modules/Property Management System",
    "website": "https://github.com/OCA/pms",
    "depends": ["pms_l10n_es", "l10n_es_aeat"],
    "external_dependencies": {
        "python": ["xmlsig", "lxml", "cryptography", "requests"],
    },
    "data": [
        "security/ir.model.access.csv",
        "security/pms_ertzaintza_security.xml",
        "data/cron_jobs.xml",
        "views/pms_ertzaintza_communication_views.xml",
        "views/pms_property_views.xml",
        "views/pms_reservation_views.xml",
    ],
    "installable": True,
    "auto_install": False,
}
