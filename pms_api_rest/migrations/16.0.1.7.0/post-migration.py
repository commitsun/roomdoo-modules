"""Portal users that do not work at a property lose the role they were given.

Until now every user was created as a receptionist, so having a role told
nothing. From this version a portal user needs one to use the PMS APIs, so the
role is kept only by the portal users with properties assigned, which is what
a hotel user has, and by the API clients. Internal users keep theirs.
"""
from openupgradelib import openupgrade


def clear_roles_of_customers(cr):
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_users u
        SET pms_api_user_role = NULL
        WHERE u.share
            AND u.pms_api_user_role IS NOT NULL
            AND NOT COALESCE(u.pms_api_client, FALSE)
            AND NOT EXISTS (
                SELECT 1 FROM pms_property_users_rel r WHERE r.user_id = u.id
            )
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    clear_roles_of_customers(env.cr)
