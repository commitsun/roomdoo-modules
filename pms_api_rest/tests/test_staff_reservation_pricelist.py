# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Invitations (staff reservations) created from the booking engine without
choosing a rate arrive with ``pricelistId`` 0 or null.

These tests cover that such reservations still get a pricelist (the one pms
computes instead of an explicit empty value), that services can be added to
them, that a folio update with an empty pricelist keeps the current one, and
that the prices endpoint answers for reservations without pricelist (the front
sends ``pricelistId=null`` when adding a service to them).
"""
import datetime

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestStaffReservationPricelist(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Admin (uid=1) avoids record rules; the endpoints sudo() anyway.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.write(
            {
                "company_ids": [(4, cls.pms_property1.company_id.id)],
                "pms_property_ids": [(4, cls.pms_property1.id)],
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Staff",
                "default_code": "DBL_STF",
                "class_id": cls.room_type_class1.id,
                "list_price": 25,
            }
        )
        # Two rooms: one test adds a second reservation for the same dates
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": name,
                    "short_name": short_name,
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for name, short_name in (("Room 301", "S301"), ("Room 302", "S302"))
            ]
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct", "channel_type": "direct"}
        )
        cls.closure_reason = cls.env["room.closure.reason"].create(
            {"name": "Maintenance staff test"}
        )
        cls.other_pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Other pricelist",
                "pms_property_ids": [(4, cls.pms_property1.id)],
                "availability_plan_id": cls.availability_plan1.id,
            }
        )
        cls.service_product = cls.env["product.product"].create(
            {"name": "Breakfast staff test", "list_price": 10}
        )
        # The property default pricelist prices the service at 7, not at its
        # list price (10), so the tests can tell which one was used
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pms_property1.default_pricelist_id.id,
                "applied_on": "0_product_variant",
                "product_id": cls.service_product.id,
                "compute_price": "fixed",
                "fixed_price": 7,
                "pms_property_ids": [(4, cls.pms_property1.id)],
            }
        )
        cls.checkin = datetime.date.today() + datetime.timedelta(days=40)
        cls.checkout = cls.checkin + datetime.timedelta(days=2)

    def _service(self, usage):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage=usage)

    def _reservation_info(self, reservation_type):
        return self.env.datamodels["pms.reservation.info"](
            roomTypeId=self.room_type.id,
            checkin=self.checkin.strftime("%Y-%m-%d"),
            checkout=self.checkout.strftime("%Y-%m-%d"),
            adults=1,
            children=0,
            # create_folio_reservations takes the type of each reservation
            reservationType=reservation_type,
        )

    def _folio_info(self, reservation_type, pricelist_id):
        values = {
            "pmsPropertyId": self.pms_property1.id,
            # What the booking engine sends: 0 when no rate is chosen
            "pricelistId": pricelist_id,
            "saleChannelId": self.sale_channel.id,
            "reservationType": reservation_type,
            "partnerName": "Staff Guest",
            "reservations": [self._reservation_info(reservation_type)],
        }
        if reservation_type == "out":
            values["closureReasonId"] = self.closure_reason.id
        return self.env.datamodels["pms.folio.info"](**values)

    def _create_folio(self, reservation_type, pricelist_id):
        folio_id = self._service("folios").create_folio(
            self._folio_info(reservation_type, pricelist_id)
        )
        return self.env["pms.folio"].browse(folio_id)

    def _price_param(self, pricelist_id, date_to=None):
        return self.env.datamodels["pms.price.search.param"](
            dateFrom=self.checkin.strftime("%Y-%m-%d"),
            dateTo=(date_to or self.checkin).strftime("%Y-%m-%d"),
            pmsPropertyId=self.pms_property1.id,
            productId=self.service_product.id,
            pricelistId=pricelist_id,
        )

    def test_staff_folio_without_pricelist_gets_property_pricelist(self):
        # ACT
        folio = self._create_folio("staff", 0)
        # ASSERT
        self.assertEqual(
            folio.reservation_ids.pricelist_id,
            self.pms_property1.default_pricelist_id,
            "An invitation created without pricelist must get the property one",
        )

    def test_staff_folio_with_pricelist_keeps_it(self):
        # Guard (passes without the fix too): an explicit pricelist is kept
        folio = self._create_folio("staff", self.other_pricelist.id)
        self.assertEqual(folio.reservation_ids.pricelist_id, self.other_pricelist)

    def test_out_of_service_folio_stays_without_pricelist(self):
        # ACT
        folio = self._create_folio("out", 0)
        # ASSERT
        self.assertFalse(
            folio.reservation_ids.pricelist_id,
            "Out of service reservations have no pricelist",
        )

    def test_add_reservation_without_pricelist_gets_folio_pricelist(self):
        # ARRANGE
        folio = self._create_folio("staff", self.other_pricelist.id)
        existing = folio.reservation_ids
        # ACT
        self._service("folios").create_folio_reservations(
            folio.id, self._folio_info("staff", 0)
        )
        # ASSERT: only the added reservation (the union of all pricelists of
        # the folio would hide an empty one)
        folio.invalidate_recordset(["reservation_ids"])
        added = folio.reservation_ids - existing
        self.assertEqual(len(added), 1)
        self.assertEqual(
            added.pricelist_id,
            self.other_pricelist,
            "A reservation added without pricelist must take the folio one",
        )

    def test_update_folio_with_empty_pricelist_keeps_it(self):
        # ARRANGE
        folio = self._create_folio("staff", self.other_pricelist.id)
        # ACT: a PATCH that resends the 0 of the booking engine
        self._service("folios").update_folio(
            folio.id, self.env.datamodels["pms.folio.info"](pricelistId=0)
        )
        # ASSERT
        self.assertEqual(folio.pricelist_id, self.other_pricelist)
        self.assertEqual(folio.reservation_ids.pricelist_id, self.other_pricelist)

    def test_add_service_to_invitation_created_without_pricelist(self):
        # ARRANGE: the case reported from the front (adding breakfast to an
        # invitation created without choosing a rate)
        reservation = self._create_folio("staff", 0).reservation_ids
        price = self._service("prices").get_prices(self._price_param(None))[0].price
        service_info = self.env.datamodels["pms.service.info"]
        line_info = self.env.datamodels["pms.service.line.info"]
        services = self._service("reservations")
        # ACT: as the front sends it (lines with the price it was given)...
        with_lines = services.create_reservation_service(
            reservation.id,
            service_info(
                productId=self.service_product.id,
                serviceLines=[
                    line_info(
                        date=self.checkin.strftime("%Y-%m-%d"),
                        priceUnit=price,
                        quantity=1,
                    )
                ],
            ),
        )
        # ... and without lines, so that pms prices them itself
        without_lines = services.create_reservation_service(
            reservation.id, service_info(productId=self.service_product.id)
        )
        # ASSERT
        self.assertEqual(price, 7.0)
        self.assertEqual(
            self.env["pms.service"].browse(with_lines).service_line_ids.price_unit,
            7.0,
        )
        self.assertEqual(
            set(
                self.env["pms.service"]
                .browse(without_lines)
                .service_line_ids.mapped("price_unit")
            ),
            {7.0},
        )

    def test_price_search_param_accepts_empty_pricelist(self):
        param_model = self.env.datamodels["pms.price.search.param"]
        common = {
            "dateFrom": self.checkin.strftime("%Y-%m-%d"),
            "dateTo": self.checkin.strftime("%Y-%m-%d"),
            "pmsPropertyId": self.pms_property1.id,
            "productId": self.service_product.id,
        }
        # Query strings carry the literal text "null", or nothing at all
        self.assertIsNone(
            param_model.load(dict(common, pricelistId="null")).pricelistId
        )
        self.assertIsNone(param_model.load(dict(common, pricelistId="")).pricelistId)
        self.assertIsNone(param_model.load(common).pricelistId)
        self.assertEqual(param_model.load(dict(common, pricelistId="5")).pricelistId, 5)

    def test_prices_without_pricelist_use_property_pricelist(self):
        # ACT
        prices = self._service("prices").get_prices(
            self._price_param(None, date_to=self.checkout)
        )
        # ASSERT: one price per date, from the property pricelist rule (7),
        # not the product list price (10)
        self.assertEqual([price.price for price in prices], [7.0, 7.0, 7.0])

    def test_prices_with_pricelist_use_it(self):
        # Guard (passes without the fix too): other_pricelist has no rule for
        # the service, so it prices it at its list price
        prices = self._service("prices").get_prices(
            self._price_param(self.other_pricelist.id)
        )
        self.assertEqual([price.price for price in prices], [10.0])
