# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "PMS User Roles",
    "summary": "Role catalog for PMS users, delegated role assignment and "
    "safety nets on top of base_user_role",
    "version": "16.0.1.0.0",
    "category": "Generic Modules/Property Management System",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": ["pms", "base_user_role"],
    "data": [
        "security/res_groups.xml",
        "security/ir.model.access.csv",
        "data/res_users_role.xml",
        "data/ir_cron.xml",
        "views/res_users_role_views.xml",
        "views/res_users_role_line_views.xml",
    ],
    "installable": True,
    "application": False,
}
