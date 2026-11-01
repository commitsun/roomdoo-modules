# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestHelpdeskRole(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.role = cls.env.ref(
            "pms_user_roles_helpdesk_enterprise.role_helpdesk_support_agent"
        )
        cls.user = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Agent",
                    "login": "agent",
                    "role_line_ids": [Command.create({"role_id": cls.role.id})],
                }
            )
        )
        cls.team = cls.env["helpdesk.team"].create(
            {"name": "Role test team", "alias_name": "role-test-team"}
        )
        cls.ticket = cls.env["helpdesk.ticket"].create(
            {"name": "Printer", "team_id": cls.team.id}
        )

    def test_agent_groups(self):
        self.assertTrue(self.user.has_group("helpdesk.group_helpdesk_user"))
        self.assertFalse(self.user.has_group("helpdesk.group_helpdesk_manager"))
        self.assertFalse(self.user.has_group("pms.group_pms_user"))

    def test_agent_works_tickets(self):
        self.env.invalidate_all()
        ticket = self.ticket.with_user(self.user)
        self.assertEqual(ticket.name, "Printer")
        ticket.write({"name": "Printer on fire"})
        self.env["helpdesk.ticket"].with_user(self.user).create(
            {"name": "New", "team_id": self.team.id}
        )

    def test_agent_has_no_pms_or_accounting_access(self):
        self.env.invalidate_all()
        for model in ("pms.folio", "pms.reservation", "account.move"):
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.user).search([], limit=1)
