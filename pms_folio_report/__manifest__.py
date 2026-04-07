{
    "name": "PMS Folio Report",
    "version": "16.0.1.0.0",
    "author": "Commitsun",
    "license": "AGPL-3",
    "category": "PMS",
    "summary": "Booking report: 3-tab Excel from folio list",
    "depends": ["pms", "report_xlsx"],
    "external_dependencies": {
        "python": ["xlsxwriter"],
        "bin": ["libreoffice"],
    },
    "assets": {
        "web.assets_backend": [
            "pms_folio_report/static/src/js/report/action_manager_report.esm.js",
        ],
    },
    "data": [
        "report/report_folio_xlsx.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
}
