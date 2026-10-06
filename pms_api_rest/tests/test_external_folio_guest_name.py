# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""External integrations (OTAs / channel managers) can send the folio
holder (``partnerId``) at folio level and the guest of each reservation
(``partnerName`` / ``partnerEmail`` / ``partnerPhone``) inside every
reservation of the payload — e.g. retail agency bookings where the folio
is held by the agency's company account and each reservation carries the
name of the person that will actually stay.

These tests cover that the per-reservation guest data is applied on
folio creation (``create_folio``) and kept/updated on folio modification
(``update_folio_values`` → ``wrapper_reservations``), while the folio
stays held by the company partner.
"""
import datetime

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestExternalFolioGuestName(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Use admin (uid=1) so record rules do not get in the way; the API
        # endpoint itself sudo()es its lookups.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        # pms_api_check_access requires the property to be among the
        # user's allowed properties
        cls.env.user.write(
            {
                "company_ids": [(4, cls.pms_property1.company_id.id)],
                "pms_property_ids": [(4, cls.pms_property1.id)],
            }
        )

        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Test",
                "default_code": "DBL_GST",
                "class_id": cls.room_type_class1.id,
                "list_price": 25,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Room 201",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct", "channel_type": "direct"}
        )
        cls.company_partner = cls.env["res.partner"].create(
            {
                "name": "Company Holder",
                "is_company": True,
            }
        )
        cls.checkin = datetime.date.today() + datetime.timedelta(days=30)
        cls.checkout = cls.checkin + datetime.timedelta(days=2)

    def _folio_service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="folios")

    def _folio_info(self, guest_name, state_code=None, external_reference="EXT-GST-1"):
        reservation_line_info = self.env.datamodels["pms.reservation.line.info"]
        reservation_info = self.env.datamodels["pms.reservation.info"]
        folio_info = self.env.datamodels["pms.folio.info"]
        nights = [
            self.checkin + datetime.timedelta(days=i)
            for i in range((self.checkout - self.checkin).days)
        ]
        return folio_info(
            pmsPropertyId=self.pms_property1.id,
            pricelistId=self.pricelist1.id,
            saleChannelId=self.sale_channel.id,
            partnerId=self.company_partner.id,
            externalReference=external_reference,
            reservations=[
                reservation_info(
                    roomTypeId=self.room_type.id,
                    checkin=self.checkin.strftime("%Y-%m-%d"),
                    checkout=self.checkout.strftime("%Y-%m-%d"),
                    adults=2,
                    children=0,
                    partnerName=guest_name,
                    partnerEmail="guest@example.com",
                    partnerPhone="+34600000000",
                    stateCode=state_code,
                    reservationLines=[
                        reservation_line_info(
                            date=night.strftime("%Y-%m-%d"),
                            price=50,
                            discount=0,
                        )
                        for night in nights
                    ],
                )
            ],
        )

    def test_create_folio_keeps_reservation_guest_name(self):
        # ARRANGE
        service = self._folio_service()
        folio_info = self._folio_info(guest_name="Guest One")
        # ACT
        folio_id = service.create_folio(folio_info)
        folio = self.env["pms.folio"].browse(folio_id)
        folio.reservation_ids.flush_recordset()
        folio.reservation_ids.invalidate_recordset(["partner_name", "partner_id"])
        # ASSERT
        self.assertEqual(
            folio.partner_id,
            self.company_partner,
            "The folio must be held by the company partner",
        )
        self.assertEqual(
            folio.reservation_ids.partner_name,
            "Guest One",
            "The reservation must keep the guest name sent per reservation",
        )
        self.assertEqual(
            folio.reservation_ids.partner_id,
            self.company_partner,
            "The reservation billing partner must stay the folio holder",
        )
        self.assertEqual(
            folio.reservation_ids.email,
            "guest@example.com",
            "The reservation must keep the guest email sent per reservation",
        )
        self.assertEqual(
            folio.reservation_ids.mobile,
            "+34600000000",
            "The reservation must keep the guest phone sent per reservation",
        )

    def test_update_folio_updates_reservation_guest_name(self):
        # ARRANGE
        service = self._folio_service()
        folio_id = service.create_folio(self._folio_info(guest_name="Guest One"))
        folio = self.env["pms.folio"].browse(folio_id)
        reservation = folio.reservation_ids
        # ACT: same reservation shape (dates/room type/pax) with a new guest
        update_info = self._folio_info(
            guest_name="Guest Two", state_code=reservation.state
        )
        service.update_folio_values(folio, update_info)
        # ASSERT
        self.assertEqual(
            folio.reservation_ids.filtered(lambda r: r.state != "cancel"),
            reservation,
            "The update must modify the existing reservation, not recreate it",
        )
        self.assertEqual(
            reservation.partner_name,
            "Guest Two",
            "The reservation guest name must be updated from the payload",
        )
        self.assertEqual(
            folio.partner_id,
            self.company_partner,
            "The folio must stay held by the company partner",
        )
