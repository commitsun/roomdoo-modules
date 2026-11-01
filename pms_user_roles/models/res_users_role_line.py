# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import SUPERUSER_ID, _, api, models
from odoo.exceptions import AccessError

from .res_users_role import FORBIDDEN_ASSIGNABLE_GROUPS

_logger = logging.getLogger(__name__)


class ResUsersRoleLine(models.Model):
    _inherit = "res.users.role.line"

    def _pms_user_roles_is_restricted(self):
        """Profile managers act under the checks below; superuser and access
        rights administrators keep the behaviour of base_user_role."""
        return not (self.env.su or self.env.user.has_group("base.group_erp_manager"))

    def _pms_user_roles_check_role(self, role):
        role = role.sudo()
        if not role.assignable:
            raise AccessError(
                _("You are not allowed to assign the role %s.", role.name)
            )
        forbidden = self.env["res.groups"]
        for xmlid in FORBIDDEN_ASSIGNABLE_GROUPS:
            forbidden |= self.env.ref(xmlid, raise_if_not_found=False) or forbidden
        if role._pms_user_roles_granted_groups() & forbidden:
            raise AccessError(
                _("You are not allowed to assign the role %s.", role.name)
            )

    def _pms_user_roles_check_user(self, user):
        # The user must be visible to the profile manager under their own
        # record rules (e.g. the property rule of pms).
        user.with_user(self.env.user).check_access_rule("read")
        user = user.sudo()
        protected_groups = (
            "base.group_system",
            "base.group_erp_manager",
            "pms_user_roles.group_role_manager",
        )
        if (
            user.id in (SUPERUSER_ID, self.env.uid)
            or not user.active
            or user.share
            or any(user.has_group(xmlid) for xmlid in protected_groups)
        ):
            raise AccessError(
                _("You are not allowed to manage the roles of %s.", user.name)
            )

    def _pms_user_roles_check_first_assignment(self, user, roles):
        """Refuse to put under roles a user whose groups are set by hand when
        that would take groups away: migrating such users is a decision for
        an access rights administrator."""
        user = user.sudo()
        if user._get_enabled_roles():
            return
        granted = roles._pms_user_roles_granted_groups()
        group_user = self.env.ref("base.group_user")
        granted |= group_user | group_user.trans_implied_ids
        if user.groups_id - granted:
            raise AccessError(
                _(
                    "The permissions of %s are not managed by roles yet and "
                    "assigning this role would remove some of them. Ask an "
                    "access rights administrator to migrate the user.",
                    user.name,
                )
            )

    def _pms_user_roles_log(self, action):
        for line in self.sudo():
            _logger.info(
                "Profile manager %s %s role %s for user %s",
                self.env.user.login,
                action,
                line.role_id.name,
                line.user_id.login,
            )

    @api.model_create_multi
    def create(self, vals_list):
        restricted = self._pms_user_roles_is_restricted()
        if restricted:
            Role = self.env["res.users.role"]
            Users = self.env["res.users"]
            new_roles = {}
            for vals in vals_list:
                role = Role.browse(vals.get("role_id"))
                user = Users.browse(vals.get("user_id"))
                self._pms_user_roles_check_role(role)
                self._pms_user_roles_check_user(user)
                new_roles.setdefault(user, Role)
                new_roles[user] |= role
            for user, roles in new_roles.items():
                self._pms_user_roles_check_first_assignment(user, roles)
        lines = super().create(vals_list)
        if restricted:
            lines._pms_user_roles_log("assigned")
        # base_user_role only recomputes on res.users writes: a line created
        # on its own (profile manager menu, XML data, RPC) would not take
        # effect until the next cron run.
        lines.user_id.sudo().set_groups_from_roles(force=True)
        return lines

    def write(self, vals):
        restricted = self._pms_user_roles_is_restricted()
        if restricted:
            for line in self.sudo():
                self._pms_user_roles_check_role(line.role_id)
                self._pms_user_roles_check_user(line.user_id)
            if vals.get("role_id"):
                self._pms_user_roles_check_role(
                    self.env["res.users.role"].browse(vals["role_id"])
                )
            if vals.get("user_id"):
                self._pms_user_roles_check_user(
                    self.env["res.users"].browse(vals["user_id"])
                )
        users_before = self.sudo().user_id
        res = super().write(vals)
        if restricted:
            self._pms_user_roles_log("changed")
        (users_before | self.sudo().user_id).set_groups_from_roles(force=True)
        return res

    def unlink(self):
        if not self._pms_user_roles_is_restricted():
            return super().unlink()
        self.check_access_rights("unlink")
        self.check_access_rule("unlink")
        for line in self.sudo():
            self._pms_user_roles_check_role(line.role_id)
            self._pms_user_roles_check_user(line.user_id)
        self._pms_user_roles_log("removed")
        # base_user_role recomputes the groups of the users right after the
        # deletion, which requires writing res.users: the checks above are
        # what a profile manager is allowed to do, the rest runs as superuser.
        return super(ResUsersRoleLine, self.sudo()).unlink()
