{
    "name": "pms autoinvoice",
    "version": "16.0.1.0.0",
    "category": "",
    "author": "Commitsun",
    "license": "AGPL-3",
    "depends": [
        "base",
        "pms",
        "queue_job",
        "partner_identification_map_partner_field",
    ],
    "data": [
        "data/ir_cron.xml",
        "data/queue_data.xml",
        "data/queue_job_function_data.xml",
        "views/account_journal.xml",
        "views/pms_property.xml",
        "views/res_partner.xml",
        "views/res_company.xml",
    ],
}
