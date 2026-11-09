# Copyright 2026 Commitsun
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Roomdoo host SSO",
    "summary": "Let the Roomdoo team in from host, with a user of their own "
    "that is archived when their access ends",
    "version": "16.0.1.0.0",
    "author": "Commitsun",
    "license": "AGPL-3",
    "website": "https://github.com/roomdoo/roomdoo-modules",
    "category": "Hidden",
    "depends": ["auth_jwt", "auth_signup", "roomdoo_fastapi", "roomdoo_host_link"],
    "data": [
        "security/ir.model.access.csv",
        "data/roomdoo_host_sso_profile_data.xml",
        "data/ir_cron.xml",
        "views/roomdoo_host_sso_profile_views.xml",
        "views/res_users_views.xml",
    ],
    "installable": True,
}
