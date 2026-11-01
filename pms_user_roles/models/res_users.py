# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import fields, models

from odoo.addons.base_user_role.models.user import ResUsers as BaseUserRoleResUsers

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    # What the "All properties" option of a role added to the user, so that it
    # can be taken back, and only that, when the user loses the role.
    pms_role_scope_property_ids = fields.Many2many(
        comodel_name="pms.property",
        relation="res_users_pms_role_scope_property_rel",
        column1="user_id",
        column2="pms_property_id",
        string="Properties added by roles",
        groups="base.group_erp_manager",
        copy=False,
    )
    pms_role_scope_company_ids = fields.Many2many(
        comodel_name="res.company",
        relation="res_users_pms_role_scope_company_rel",
        column1="user_id",
        column2="company_id",
        string="Companies added by roles",
        groups="base.group_erp_manager",
        copy=False,
    )

    def _pms_user_roles_groups_from_roles(self):
        self.ensure_one()
        return self._get_enabled_roles().role_id._pms_user_roles_granted_groups()

    def set_groups_from_roles(self, force=False):
        """Keep internal users internal when their roles grant no user type.

        base_user_role replaces the groups of a user with the groups of their
        enabled roles. When those roles grant no user type (a user whose only
        role is an add-on role, or whose last role line was removed), the user
        silently becomes a share user without any group. Such users keep
        'Internal User' (and its implied groups) and a warning is logged.
        """
        user_types = self.env["res.users.role"]._pms_user_roles_user_type_groups()
        protected = self.browse()
        for user in self:
            if not user.active or user.share:
                continue
            if not user.role_line_ids and not force:
                continue
            if not user._pms_user_roles_groups_from_roles() & user_types:
                protected |= user
        res = super(ResUsers, self - protected).set_groups_from_roles(force=force)
        group_user = self.env.ref("base.group_user")
        for user in protected:
            target = (
                user._pms_user_roles_groups_from_roles()
                | group_user
                | group_user.trans_implied_ids
            )
            to_remove = user.groups_id - target
            to_add = target - user.groups_id
            if not (to_remove or to_add):
                continue
            _logger.warning(
                "The roles of user %s grant no user type: keeping them as an "
                "internal user (roles: %s).",
                user.login,
                ", ".join(user._get_enabled_roles().role_id.mapped("name")) or "-",
            )
            # Same bypass base_user_role uses: write the groups without
            # triggering a new role recompute.
            super(BaseUserRoleResUsers, user).write(
                {
                    "groups_id": [(3, group.id) for group in to_remove]
                    + [(4, group.id) for group in to_add]
                }
            )
        self._pms_user_roles_sync_property_scope()
        return res

    def _pms_user_roles_sync_property_scope(self):
        """Apply or take back the scope of roles flagged "All properties".

        Users with an enabled flagged role get every active property and the
        companies of those properties, in a single write so that the property
        and company constraints of pms hold. What is added is recorded, and
        removed again when the user no longer holds a flagged role. Rules are
        cached per user with the property ids baked in, and pms_property_ids
        is not an invalidation field of res.users, so the cache is cleared.
        """
        changed = False
        properties = None
        for user in self.sudo():
            flagged = user.active and user._get_enabled_roles().role_id.filtered(
                "pms_all_properties"
            )
            if flagged:
                if properties is None:
                    properties = self.env["pms.property"].sudo().search([])
                vals = user._pms_user_roles_scope_add_vals(properties)
            else:
                vals = user._pms_user_roles_scope_remove_vals()
            if vals:
                user.write(vals)
                changed = True
        if changed:
            self.env["ir.rule"].clear_caches()

    def _pms_user_roles_scope_add_vals(self, properties):
        self.ensure_one()
        new_properties = properties - self.pms_property_ids
        new_companies = properties.company_id - self.company_ids
        vals = {}
        if new_companies:
            vals["company_ids"] = [(4, company.id) for company in new_companies]
            vals["pms_role_scope_company_ids"] = [
                (4, company.id) for company in new_companies
            ]
        if new_properties:
            vals["pms_property_ids"] = [(4, prop.id) for prop in new_properties]
            vals["pms_role_scope_property_ids"] = [
                (4, prop.id) for prop in new_properties
            ]
        if not self.pms_property_id and properties:
            vals["pms_property_id"] = properties[0].id
        return vals

    def _pms_user_roles_scope_remove_vals(self):
        self.ensure_one()
        added_properties = self.pms_role_scope_property_ids
        added_companies = self.pms_role_scope_company_ids
        if not (added_properties or added_companies):
            return {}
        remaining_properties = self.pms_property_ids - added_properties
        # A company stays while the user keeps a property of it or uses it
        # as their current company.
        companies_to_remove = (
            (added_companies & self.company_ids)
            - remaining_properties.company_id
            - self.company_id
        )
        vals = {
            "pms_property_ids": [(3, prop.id) for prop in added_properties],
            "company_ids": [(3, company.id) for company in companies_to_remove],
            "pms_role_scope_property_ids": [(5, 0, 0)],
            "pms_role_scope_company_ids": [(5, 0, 0)],
        }
        if self.pms_property_id in added_properties:
            vals["pms_property_id"] = remaining_properties[:1].id or False
        return vals
