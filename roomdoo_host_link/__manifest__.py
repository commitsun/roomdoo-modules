# Copyright 2026 Commitsun
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Roomdoo host link",
    "summary": "Keep the Roomdoo host registry of this instance current, in real time",
    "version": "16.0.1.0.0",
    "author": "Commitsun",
    "license": "AGPL-3",
    "website": "https://github.com/roomdoo/roomdoo-modules",
    "category": "Hidden",
    "depends": ["pms", "queue_job", "roomdoo_fastapi"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/roomdoo_host_link_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
