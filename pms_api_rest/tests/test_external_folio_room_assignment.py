# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""External integrations (OTAs / channel managers) create folios sending
the nights of each reservation (``reservationLines``) and, usually, a board
service. The room must be assigned for the stay dates: a room that is free
today but taken on the stay dates must not be picked, or the reservation
lands on an occupied room and is flagged as overbooking.
"""
import datetime
from unittest import mock

from odoo import fields
from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestExternalFolioRoomAssignment(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        # Act as an external app: it is the path that computes the board
        # service while the reservation is being created
        cls.env.user.write(
            {
                "pms_api_client": True,
                "company_ids": [(4, cls.pms_property1.company_id.id)],
                "pms_property_ids": [(4, cls.pms_property1.id)],
            }
        )
        cls.room_type_class1.pms_property_ids = [(6, 0, [cls.pms_property1.id])]
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
                "name": "Double Assignment Test",
                "default_code": "DBL_ASG",
                "class_id": cls.room_type_class1.id,
                "list_price": 50,
            }
        )
        cls.room_taken = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Room 301",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
                "assignment_sequence": 1,
            }
        )
        cls.room_free = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Room 302",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
                "assignment_sequence": 2,
            }
        )
        cls.breakfast = cls.env["product.product"].create(
            {"name": "Breakfast", "list_price": 8.0, "per_person": True}
        )
        cls.board = cls.env["pms.board.service"].create(
            {
                "name": "Board BB",
                "default_code": "BB_ASG",
                "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
                "board_service_line_ids": [
                    (
                        0,
                        False,
                        {"product_id": cls.breakfast.id, "amount": 8.0, "adults": True},
                    ),
                ],
            }
        )
        cls.env["pms.board.service.room.type"].create(
            {
                "pms_board_service_id": cls.board.id,
                "pms_room_type_id": cls.room_type.id,
                "pms_property_id": cls.pms_property1.id,
                "board_service_line_ids": [
                    (
                        0,
                        False,
                        {"product_id": cls.breakfast.id, "amount": 8.0, "adults": True},
                    ),
                ],
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct", "channel_type": "direct"}
        )
        cls.checkin = datetime.date.today() + datetime.timedelta(days=30)
        cls.checkout = cls.checkin + datetime.timedelta(days=2)
        # The first room in assignment order is free today, but taken on
        # the stay dates
        cls.env["pms.reservation"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "room_type_id": cls.room_type.id,
                "preferred_room_id": cls.room_taken.id,
                "checkin": cls.checkin,
                "checkout": cls.checkout,
                "adults": 1,
                "partner_name": "Previous Guest",
                "pricelist_id": cls.pricelist1.id,
                "sale_channel_origin_id": cls.sale_channel.id,
            }
        )

    def _folio_service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="folios")

    def _folio_info(self):
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
            partnerName="External Guest",
            externalReference="EXT-ASG-1",
            reservations=[
                reservation_info(
                    roomTypeId=self.room_type.id,
                    checkin=self.checkin.strftime("%Y-%m-%d"),
                    checkout=self.checkout.strftime("%Y-%m-%d"),
                    adults=2,
                    children=0,
                    boardServiceId=self.board.id,
                    reservationLines=[
                        reservation_line_info(
                            date=night.strftime("%Y-%m-%d"),
                            price=70,
                            discount=0,
                        )
                        for night in nights
                    ],
                )
            ],
        )

    def _create_folio(self):
        """Create the folio through the service and return it along with the
        values each reservation was created with."""
        reservation_model = type(self.env["pms.reservation"])
        original_create = reservation_model.create
        sent_vals = []

        def spy_create(records, vals_list):
            sent_vals.extend(vals_list if isinstance(vals_list, list) else [vals_list])
            return original_create(records, vals_list)

        # The channel push after the creation is out of the scope of the test
        with mock.patch.object(reservation_model, "create", spy_create):
            with mock.patch.object(
                type(self.env["pms.property"]), "pms_api_push_batch"
            ):
                folio_id = self._folio_service().create_folio(self._folio_info())
        return self.env["pms.folio"].browse(folio_id), sent_vals

    def test_stay_dates_sent_with_nights(self):
        """The reservation gets its stay dates even when the nights come in
        the payload. Which fields are computed first while the reservation is
        created depends on the stored fields other installed modules hang on
        its lines: with a board service the room can be computed before the
        dates are derived from the nights, i.e. for today."""
        # ACT
        _folio, sent_vals = self._create_folio()
        # ASSERT
        reservation_vals = [
            vals for vals in sent_vals if vals.get("reservation_line_ids")
        ]
        self.assertEqual(len(reservation_vals), 1)
        self.assertEqual(
            fields.Date.to_date(reservation_vals[0].get("checkin")),
            self.checkin,
            "The checkin must be sent along with the nights",
        )
        self.assertEqual(
            fields.Date.to_date(reservation_vals[0].get("checkout")),
            self.checkout,
            "The checkout must be sent along with the nights",
        )

    def test_room_assigned_for_stay_dates_with_board_service(self):
        # ACT
        folio, _sent_vals = self._create_folio()
        reservation = folio.reservation_ids
        # ASSERT
        self.assertEqual(
            reservation.reservation_line_ids.room_id,
            self.room_free,
            "The room must be free on the stay dates, not only today",
        )
        self.assertFalse(
            reservation.overbooking,
            "A reservation with a free room on its dates is not an overbooking",
        )
        self.assertEqual(
            reservation.reservation_line_ids.mapped("price"),
            # 70 minus a breakfast of 8 per person for 2 adults
            [54.0, 54.0],
            "The nights must keep the payload price minus the board service",
        )
