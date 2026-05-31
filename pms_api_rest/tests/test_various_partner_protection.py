# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestVariousPartnerProtection(TransactionCase):
    """The ``pms.various_pms_partner`` contact is the system stand-in
    used by the PMS for simplified invoices. It must never be mutated
    by the REST API even when callers use ``sudo()`` to bypass record
    rules. These tests cover both direct mutation and the
    "create-child-contact" route that also leaks guest data onto it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.various = cls.env.ref("pms.various_pms_partner")

    def test_write_identity_field_is_blocked(self):
        with self.assertRaises(UserError):
            self.various.write({"name": "Mr. Doe"})

    def test_write_email_field_is_blocked(self):
        with self.assertRaises(UserError):
            self.various.write({"email": "doe@example.com"})

    def test_write_address_field_is_blocked(self):
        with self.assertRaises(UserError):
            self.various.write({"street": "Calle Falsa 123"})

    def test_write_with_sudo_is_blocked(self):
        # The bug we are guarding against: sudo() bypasses record rules
        # but the override still fires.
        with self.assertRaises(UserError):
            self.various.sudo().write({"vat": "ES12345678Z"})

    def test_write_unprotected_field_passes(self):
        # ``ref`` is not in the protected set; nothing prevents touching it.
        self.various.write({"ref": "VARIOUS-REF"})
        self.assertEqual(self.various.ref, "VARIOUS-REF")

    def test_create_child_contact_under_various_is_blocked(self):
        with self.assertRaises(UserError):
            self.env["res.partner"].create(
                {
                    "name": "Leaked Guest",
                    "email": "leak@example.com",
                    "parent_id": self.various.id,
                }
            )

    def test_create_with_commercial_partner_pointing_to_various_is_blocked(self):
        with self.assertRaises(UserError):
            self.env["res.partner"].create(
                {
                    "name": "Leaked Guest",
                    "commercial_partner_id": self.various.id,
                }
            )

    def test_create_unrelated_partner_passes(self):
        partner = self.env["res.partner"].create({"name": "Real Guest"})
        self.assertTrue(partner.id)
        self.assertNotEqual(partner.parent_id, self.various)
