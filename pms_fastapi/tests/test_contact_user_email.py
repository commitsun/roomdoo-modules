# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The email of a user's contact is where that user's password resets go:
through /contacts only the user itself, or an access rights manager, sets a
new one."""

from fastapi import status

from odoo import Command

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestContactUserEmail(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.access_manager_group = cls.env.ref("base.group_erp_manager")
        cls.test_user.write(
            {"groups_id": [Command.unlink(cls.access_manager_group.id)]}
        )
        cls.colleague = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Colleague",
                    "login": "colleague@example.org",
                    "email": "colleague@example.org",
                    "company_id": cls.test_company.id,
                    "company_ids": [Command.set([cls.test_company.id])],
                }
            )
        )
        cls.guest = cls.env["res.partner"].create(
            {"name": "Guest", "email": "guest@example.org"}
        )

    def _patch_email(self, partner, email):
        with self._create_test_client() as test_client:
            self._login(test_client)
            return test_client.patch(f"/contacts/{partner.id}", json={"email": email})

    def _email_of(self, partner):
        partner.invalidate_recordset(["email"])
        return partner.email

    def test_cannot_set_a_new_email_on_a_colleagues_contact(self):
        response = self._patch_email(self.colleague.partner_id, "taken@example.org")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self._email_of(self.colleague.partner_id), "colleague@example.org"
        )

    def test_archived_users_are_covered_too(self):
        self.colleague.active = False
        response = self._patch_email(self.colleague.partner_id, "taken@example.org")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_sets_a_new_email_on_its_own_contact(self):
        response = self._patch_email(self.test_user.partner_id, "moved@example.org")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(self._email_of(self.test_user.partner_id), "moved@example.org")

    def test_sets_a_new_email_on_a_guest(self):
        response = self._patch_email(self.guest, "moved@example.org")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(self._email_of(self.guest), "moved@example.org")

    def test_an_access_rights_manager_sets_it(self):
        self.test_user.write(
            {"groups_id": [Command.link(self.access_manager_group.id)]}
        )
        response = self._patch_email(self.colleague.partner_id, "desk@example.org")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(self._email_of(self.colleague.partner_id), "desk@example.org")
