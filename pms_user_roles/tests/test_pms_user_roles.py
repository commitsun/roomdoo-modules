# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestPmsUserRoles(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Role = cls.env["res.users.role"]
        cls.Line = cls.env["res.users.role.line"]
        cls.group_user = cls.env.ref("base.group_user")
        cls.group_pms_user = cls.env.ref("pms.group_pms_user")
        cls.group_pms_manager = cls.env.ref("pms.group_pms_manager")
        cls.group_multi_company = cls.env.ref("base.group_multi_company")
        cls.role_base = cls.env.ref("pms_user_roles.role_internal_base")
        cls.role_reception = cls.env.ref("pms_user_roles.role_pms_reception")
        cls.role_reception.assignable = True
        cls.role_manager = cls.env.ref("pms_user_roles.role_pms_manager")
        cls.company2 = cls.env["res.company"].create({"name": "Company 2"})
        cls.pms_property2 = cls.env["pms.property"].create(
            {
                "name": "Property 2",
                "company_id": cls.company2.id,
                "default_pricelist_id": cls.pricelist1.id,
            }
        )
        cls.profile_manager = cls._create_user(
            "profile.manager",
            groups=cls.env.ref("pms_user_roles.group_role_manager")
            | cls.group_pms_user,
        )

    @classmethod
    def _create_user(cls, login, groups=None, roles=None):
        vals = {
            "name": login,
            "login": login,
            "company_id": cls.company1.id,
            "company_ids": [Command.set(cls.company1.ids)],
            "pms_property_ids": [Command.set(cls.pms_property1.ids)],
            "pms_property_id": cls.pms_property1.id,
            "groups_id": [Command.set((groups or cls.group_user).ids)],
        }
        if roles:
            vals["role_line_ids"] = [Command.create({"role_id": r.id}) for r in roles]
        return cls.env["res.users"].with_context(no_reset_password=True).create(vals)

    def _expected_groups(self, roles):
        return (
            roles.group_id
            | roles.implied_ids
            | roles.trans_implied_ids
            | self.group_user.trans_implied_ids
        )

    # Catalog and recompute

    def test_catalog_role_sets_groups(self):
        user = self._create_user("reception", roles=self.role_reception)
        self.assertIn(self.group_pms_user, user.groups_id)
        self.assertNotIn(self.group_pms_manager, user.groups_id)
        self.assertFalse(user.groups_id - self._expected_groups(self.role_reception))

    def test_line_created_alone_is_applied(self):
        """base_user_role recomputes on user writes only; a line created on
        its own must take effect too."""
        user = self._create_user("lonely", roles=self.role_base)
        self.Line.create({"user_id": user.id, "role_id": self.role_manager.id})
        self.assertIn(self.group_pms_manager, user.groups_id)

    # Safety net

    def test_safety_net_keeps_internal_user(self):
        # A role that grants no user type, e.g. an add-on stacked on top of
        # another role.
        addon_role = self.Role.create(
            {
                "name": "Add-on",
                "implied_ids": [
                    Command.link(self.env.ref("base.group_multi_currency").id)
                ],
            }
        )
        user = self._create_user("addon", roles=self.role_base)
        user.role_line_ids.role_id = addon_role
        self.assertIn(self.group_user, user.groups_id)
        self.assertFalse(user.share)
        with self.assertNoLogs("odoo.addons.pms_user_roles.models.res_users"):
            user.set_groups_from_roles(force=True)

    def test_safety_net_on_last_line_removed(self):
        user = self._create_user("last", roles=self.role_reception)
        with self.assertLogs(
            "odoo.addons.pms_user_roles.models.res_users", level="WARNING"
        ):
            user.role_line_ids.unlink()
        self.assertIn(self.group_user, user.groups_id)
        self.assertNotIn(self.group_pms_user, user.groups_id)
        self.assertFalse(user.share)

    # Constraints

    def test_role_with_two_user_types(self):
        with self.assertRaises(ValidationError):
            self.Role.create(
                {
                    "name": "Broken",
                    "implied_ids": [
                        Command.link(self.group_user.id),
                        Command.link(self.env.ref("base.group_portal").id),
                    ],
                }
            )

    def test_assignable_role_cannot_grant_admin(self):
        with self.assertRaises(ValidationError):
            self.Role.create(
                {
                    "name": "Escalation",
                    "assignable": True,
                    "implied_ids": [
                        Command.link(self.env.ref("base.group_erp_manager").id)
                    ],
                }
            )
        role = self.Role.create({"name": "Later", "assignable": True})
        with self.assertRaises(ValidationError):
            role.implied_ids = [Command.link(self.env.ref("base.group_system").id)]

    def test_all_properties_requires_multi_company(self):
        with self.assertRaises(ValidationError):
            self.Role.create(
                {
                    "name": "All without multi company",
                    "pms_all_properties": True,
                    "implied_ids": [Command.link(self.group_user.id)],
                }
            )

    # Profile managers

    def test_profile_manager_assigns_role(self):
        target = self._create_user("target", roles=self.role_base)
        self.env.invalidate_all()
        self.Line.with_user(self.profile_manager).create(
            {"user_id": target.id, "role_id": self.role_reception.id}
        )
        self.assertIn(self.group_pms_user, target.groups_id)

    def test_profile_manager_cannot_assign_non_assignable(self):
        target = self._create_user("target2", roles=self.role_base)
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.Line.with_user(self.profile_manager).create(
                {"user_id": target.id, "role_id": self.role_manager.id}
            )

    def test_profile_manager_cannot_touch_admins(self):
        admin = self._create_user(
            "admin2", groups=self.env.ref("base.group_erp_manager")
        )
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.Line.with_user(self.profile_manager).create(
                {"user_id": admin.id, "role_id": self.role_reception.id}
            )
        with self.assertRaises(AccessError):
            self.Line.with_user(self.profile_manager).create(
                {"user_id": self.profile_manager.id, "role_id": self.role_reception.id}
            )

    def test_profile_manager_cannot_downgrade_hand_managed_user(self):
        target = self._create_user("handmade", groups=self.group_pms_manager)
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.Line.with_user(self.profile_manager).create(
                {"user_id": target.id, "role_id": self.role_reception.id}
            )
        self.assertIn(self.group_pms_manager, target.groups_id)

    def test_profile_manager_removes_role(self):
        target = self._create_user(
            "target3", roles=self.role_base | self.role_reception
        )
        line = target.role_line_ids.filtered(
            lambda rec: rec.role_id == self.role_reception
        )
        self.env.invalidate_all()
        line.with_user(self.profile_manager).unlink()
        self.assertNotIn(self.group_pms_user, target.groups_id)
        self.assertIn(self.group_user, target.groups_id)

    def test_profile_manager_cannot_retarget_line_to_admin(self):
        target = self._create_user(
            "target4", roles=self.role_base | self.role_reception
        )
        admin = self._create_user(
            "admin3", groups=self.env.ref("base.group_erp_manager")
        )
        line = target.role_line_ids.filtered(
            lambda rec: rec.role_id == self.role_reception
        )
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            line.with_user(self.profile_manager).write({"user_id": admin.id})

    # All properties scope

    def test_all_properties_scope(self):
        role = self.Role.create(
            {
                "name": "Support",
                "pms_all_properties": True,
                "implied_ids": [
                    Command.link(self.group_user.id),
                    Command.link(self.group_multi_company.id),
                ],
            }
        )
        user = self._create_user("support", roles=self.role_base)
        user.write({"role_line_ids": [Command.create({"role_id": role.id})]})
        all_properties = self.env["pms.property"].search([])
        self.assertEqual(user.pms_property_ids, all_properties)
        self.assertIn(self.company2, user.company_ids)
        self.assertIn(self.group_multi_company, user.groups_id)

        new_property = self.env["pms.property"].create(
            {
                "name": "Property 3",
                "company_id": self.company2.id,
                "default_pricelist_id": self.pricelist1.id,
            }
        )
        self.assertIn(new_property, user.pms_property_ids)

        user.write(
            {
                "role_line_ids": [
                    Command.unlink(
                        user.role_line_ids.filtered(lambda r: r.role_id == role).id
                    )
                ]
            }
        )
        # Only what the role added goes away.
        self.assertEqual(user.pms_property_ids, self.pms_property1)
        self.assertEqual(user.pms_property_id, self.pms_property1)
        self.assertNotIn(self.company2, user.company_ids)

    # Audit cron

    def test_audit_cron_reports_group_without_line(self):
        user = self._create_user("orphan", roles=self.role_base)
        # Give the role group by hand, the way it happens when lines are
        # deleted with SQL or groups are copied between users.
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s)",
            (self.role_manager.group_id.id, user.id),
        )
        with self.assertLogs(
            "odoo.addons.pms_user_roles.models.res_users_role", level="WARNING"
        ) as logs:
            self.Role._cron_pms_user_roles_audit()
        self.assertTrue(any("orphan" in line for line in logs.output))
