# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Groups that a role handed out by a profile manager must never grant: with any
# of them the holder could change their own permissions.
FORBIDDEN_ASSIGNABLE_GROUPS = (
    "base.group_system",
    "base.group_erp_manager",
    "pms_user_roles.group_role_manager",
)


class ResUsersRole(models.Model):
    _inherit = "res.users.role"

    assignable = fields.Boolean(
        help="Profile managers may assign this role. Roles that grant "
        "administration rights can never be assignable.",
    )
    pms_all_properties = fields.Boolean(
        string="All properties",
        help="Users holding this role are given every active property and its "
        "company. The scope added this way is removed again when the user no "
        "longer holds any role with this option. The role must grant "
        "'Multi Companies', otherwise the role recompute would take it away.",
    )

    @api.model
    def _pms_user_roles_user_type_groups(self):
        category = self.env.ref("base.module_category_user_type")
        return self.env["res.groups"].sudo().search([("category_id", "=", category.id)])

    def _pms_user_roles_granted_groups(self):
        """Groups a holder of the roles in ``self`` gets, as base_user_role
        computes them in ``set_groups_from_roles``."""
        roles = self.sudo()
        return roles.group_id | roles.implied_ids | roles.trans_implied_ids

    def _check_pms_user_roles_groups(self):
        user_types = self._pms_user_roles_user_type_groups()
        forbidden = self.env["res.groups"]
        for xmlid in FORBIDDEN_ASSIGNABLE_GROUPS:
            forbidden |= self.env.ref(xmlid, raise_if_not_found=False) or forbidden
        multi_company = self.env.ref("base.group_multi_company")
        for role in self.sudo():
            granted = role._pms_user_roles_granted_groups()
            if len(granted & user_types) > 1:
                raise ValidationError(
                    _(
                        "The role %(role)s grants more than one user type "
                        "(%(types)s). A user can only be of one type.",
                        role=role.name,
                        types=", ".join((granted & user_types).mapped("name")),
                    )
                )
            if role.assignable and granted & forbidden:
                raise ValidationError(
                    _(
                        "The role %(role)s cannot be assignable by profile "
                        "managers because it grants %(groups)s.",
                        role=role.name,
                        groups=", ".join((granted & forbidden).mapped("full_name")),
                    )
                )
            if role.pms_all_properties and multi_company not in granted:
                raise ValidationError(
                    _(
                        "The role %(role)s gives access to all the properties, "
                        "so it must grant 'Multi Companies'.",
                        role=role.name,
                    )
                )

    @api.constrains("group_id", "assignable", "pms_all_properties")
    def _constrains_pms_user_roles_groups(self):
        self._check_pms_user_roles_groups()

    @api.model
    def _cron_pms_user_roles_audit(self):
        """Log the users that hold the group of a role without a role line.

        base_user_role only recomputes the groups of users that have role
        lines, so such users keep the permissions of the role until someone
        removes the group by hand. This only reports them.
        """
        self.env.cr.execute(
            """
            SELECT role.id, usr.login
              FROM res_users_role role
              JOIN res_groups_users_rel rel ON rel.gid = role.group_id
              JOIN res_users usr ON usr.id = rel.uid
         LEFT JOIN res_users_role_line line
                ON line.role_id = role.id AND line.user_id = usr.id
             WHERE line.id IS NULL
          ORDER BY role.id, usr.login
            """
        )
        rows = self.env.cr.fetchall()
        roles = self.sudo().browse({row[0] for row in rows})
        names = {role.id: role.name for role in roles}
        for role_id, login in rows:
            _logger.warning(
                "User %s has the group of role %s but no role line: "
                "role recomputes will never update it.",
                login,
                names.get(role_id),
            )
        return len(rows)
