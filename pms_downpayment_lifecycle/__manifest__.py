{
    "name": "PMS Down Payment Lifecycle",
    "summary": "Keep a down payment invoice correct when the stay it was "
    "collected for changes: a different customer, a refund, a cancellation or a "
    "change of amount -- always without rewriting a closed period.",
    "version": "16.0.1.2.0",
    "category": "Generic Modules/Property Management System",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": [
        "pms",
        "pms_autoinvoice",
        "queue_job",
        "account_invoice_constraint_chronology",
        "account_reconcile_lock_date",
    ],
    "data": [
        "data/queue_data.xml",
        "data/queue_job_function_data.xml",
        "data/ir_cron_data.xml",
        "views/account_move.xml",
        "views/account_payment.xml",
        "views/pms_folio.xml",
        "views/res_config_settings.xml",
    ],
    "installable": True,
    "application": False,
}
