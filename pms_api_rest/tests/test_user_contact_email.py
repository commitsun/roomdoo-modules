# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The email of a user's contact is where that user's password resets go.

Only the user itself, or an access rights manager, sets a new one: through the
contacts of the API, through a check-in, and never through the public
pre-check-in pages, which also stay within the reservation their token opens.
"""

import datetime

import werkzeug.exceptions

from odoo import Command
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestUserContactEmail(TestPms, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.customer = users.create(
            {
                "name": "Customer",
                "login": "customer@example.org",
                "email": "customer@example.org",
                "groups_id": [Command.set([cls.env.ref("base.group_portal").id])],
            }
        )
        staff = {
            "company_id": cls.company1.id,
            "company_ids": [Command.set([cls.company1.id])],
            "pms_property_ids": [Command.set([cls.pms_property1.id])],
            "pms_property_id": cls.pms_property1.id,
        }
        cls.receptionist = users.create(
            dict(
                staff,
                name="Receptionist",
                login="receptionist@example.org",
                email="receptionist@example.org",
                groups_id=[Command.set([cls.env.ref("base.group_user").id])],
            )
        )
        cls.access_manager = users.create(
            dict(
                staff,
                name="Access Manager",
                login="access.manager@example.org",
                email="access.manager@example.org",
                groups_id=[
                    Command.set(
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("base.group_erp_manager").id,
                        ]
                    )
                ],
            )
        )
        cls.guest = cls.env["res.partner"].create(
            {"name": "Guest", "email": "guest@example.org"}
        )
        room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double",
                "default_code": "DBL",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": name,
                    "short_name": short_name,
                    "room_type_id": room_type.id,
                    "capacity": 2,
                }
                for name, short_name in (("Room 101", "R101"), ("Room 102", "R102"))
            ]
        )
        channel = cls.env["pms.sale.channel"].create(
            {"name": "Door", "channel_type": "direct"}
        )
        reservations = cls.env["pms.reservation"]
        for _i in range(2):
            reservations |= reservations.create(
                {
                    "pms_property_id": cls.pms_property1.id,
                    "checkin": datetime.date.today(),
                    "checkout": datetime.date.today() + datetime.timedelta(days=1),
                    "adults": 1,
                    "room_type_id": room_type.id,
                    "partner_id": cls.guest.id,
                    "sale_channel_origin_id": channel.id,
                }
            )
        cls.reservation, cls.other_reservation = reservations

    def _service(self, usage, caller):
        collection = _PseudoCollection("pms.services", self.env(user=caller))
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage=usage)

    def _checkin_info(self, **values):
        return self.env.datamodels["pms.checkin.partner.info"](**values)

    # Contacts of the API

    def test_nobody_else_sets_a_new_email_on_a_users_contact(self):
        partners = self._service("partners", self.receptionist)
        for user in (self.customer, self.access_manager):
            with self.subTest(user=user.login):
                with self.assertRaises(werkzeug.exceptions.Forbidden):
                    partners._check_user_partner_email_change(
                        user.partner_id, {"email": "taken@example.org"}
                    )

    def test_the_rule_also_covers_archived_users(self):
        self.customer.active = False
        partners = self._service("partners", self.receptionist)
        with self.assertRaises(werkzeug.exceptions.Forbidden):
            partners._check_user_partner_email_change(
                self.customer.partner_id, {"email": "taken@example.org"}
            )

    def test_contacts_without_user_and_unchanged_emails_are_free(self):
        partners = self._service("partners", self.receptionist)
        partners._check_user_partner_email_change(
            self.guest, {"email": "moved@example.org"}
        )
        partners._check_user_partner_email_change(
            self.customer.partner_id, {"email": "customer@example.org"}
        )
        partners._check_user_partner_email_change(
            self.customer.partner_id, {"name": "Renamed"}
        )

    def test_a_user_and_an_access_rights_manager_set_it(self):
        self._service("partners", self.customer)._check_user_partner_email_change(
            self.customer.partner_id, {"email": "moved@example.org"}
        )
        self._service("partners", self.access_manager)._check_user_partner_email_change(
            self.customer.partner_id, {"email": "moved@example.org"}
        )

    # Check-in with a session

    def test_a_check_in_does_not_link_a_guest_to_a_staff_contact(self):
        reservations = self._service("reservations", self.receptionist)
        info = self._checkin_info(partnerId=self.access_manager.partner_id.id)
        with self.assertRaises(werkzeug.exceptions.Forbidden):
            reservations._check_checkin_partner_user_contact(info, self.guest.id)

    def test_a_check_in_does_not_set_a_new_email_on_a_users_contact(self):
        reservations = self._service("reservations", self.receptionist)
        info = self._checkin_info(email="taken@example.org")
        with self.assertRaises(werkzeug.exceptions.Forbidden):
            reservations._check_checkin_partner_user_contact(
                info, self.customer.partner_id.id
            )

    def test_a_check_in_links_a_customer_and_edits_a_guest(self):
        reservations = self._service("reservations", self.receptionist)
        reservations._check_checkin_partner_user_contact(
            self._checkin_info(partnerId=self.customer.partner_id.id), self.guest.id
        )
        reservations._check_checkin_partner_user_contact(
            self._checkin_info(email="moved@example.org"), self.guest.id
        )

    def test_an_access_rights_manager_links_any_contact(self):
        self._service(
            "reservations", self.access_manager
        )._check_checkin_partner_user_contact(
            self._checkin_info(
                partnerId=self.receptionist.partner_id.id, email="desk@example.org"
            ),
            self.guest.id,
        )

    # Public pre-check-in pages

    def _precheckin(self, reservation, guest, payload):
        token = reservation._portal_ensure_token()
        # The request reads the database, not the cache of this test
        self.env.flush_all()
        return self.opener.patch(
            f"{self.base_url()}/api/reservations/{reservation.id}"
            f"/precheckin-reservation/{token}/checkin-partners/{guest.id}",
            json=payload,
            timeout=30,
        )

    @mute_logger("odoo.addons.base_rest.http")
    def test_a_token_only_opens_the_guests_of_its_reservation(self):
        guest = self.other_reservation.checkin_partner_ids[:1]
        response = self._precheckin(self.reservation, guest, {"firstname": "Eve"})

        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotEqual(guest.firstname, "Eve")

    @mute_logger("odoo.addons.base_rest.http")
    def test_a_public_page_does_not_link_a_guest_to_a_staff_contact(self):
        guest = self.reservation.checkin_partner_ids[:1]
        response = self._precheckin(
            self.reservation, guest, {"partnerId": self.receptionist.partner_id.id}
        )

        self.assertEqual(response.status_code, 403, response.text)
        guest.invalidate_recordset()
        self.assertNotEqual(guest.partner_id, self.receptionist.partner_id)

    @mute_logger("odoo.addons.base_rest.http")
    def test_a_public_page_does_not_set_a_new_email_on_a_users_contact(self):
        guest = self.reservation.checkin_partner_ids[:1]
        guest.partner_id = self.customer.partner_id
        response = self._precheckin(
            self.reservation, guest, {"email": "taken@example.org"}
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.customer.partner_id.invalidate_recordset()
        self.assertEqual(self.customer.partner_id.email, "customer@example.org")
