# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from datetime import date, timedelta

from odoo.tests import tagged

from odoo.addons.extendable_fastapi.tests.common import FastAPITransactionCase
from odoo.addons.pms_fastapi_booking_engine.models.fastapi_endpoint import (
    booking_engine_router,
)


@tagged("post_install", "-at_install")
class BookingEngineCase(FastAPITransactionCase):
    """A property sold online with two rates: flexible, and non refundable at
    10% less. Nothing carries VAT, so the totals add up by hand."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.default_fastapi_router = booking_engine_router
        cls.default_fastapi_running_user = cls.env.ref(
            "pms_fastapi_booking_engine.pms_booking_engine_user"
        )
        # The endpoint answers in the language of its user, whatever the
        # default language of the database is.
        cls.default_fastapi_running_user.lang = "en_US"
        cls.env["product.product"].search([("is_tourist_tax", "=", True)]).write(
            {"active": False}
        )
        cls._setup_rates()
        cls.pms_property = cls.env["pms.property"].create(
            {
                "name": "Booking Engine Hotel",
                "company_id": cls.env.company.id,
                "default_pricelist_id": cls.pricelist_flexible.id,
                "tz": "Europe/Madrid",
            }
        )
        cls.engine_channel = cls.env["pms.sale.channel"].create(
            {
                "name": "Web booking engine",
                "channel_type": "direct",
                "is_on_line": True,
                "product_pricelist_ids": [
                    (6, 0, (cls.pricelist_flexible | cls.pricelist_non_refundable).ids)
                ],
            }
        )
        cls.pms_property.booking_engine_sale_channel_id = cls.engine_channel
        cls._setup_rooms()
        cls._setup_prices()
        cls._setup_board()
        cls.tourist_tax = cls.env["product.product"].create(
            {
                "name": "Tourist tax",
                "list_price": 1.5,
                "is_tourist_tax": True,
                "per_person": True,
                "tourist_tax_apply_from_night": 1,
                "tourist_tax_apply_to_night": 5,
                "tourist_tax_min_age": 18,
                "taxes_id": [(6, 0, [])],
            }
        )
        cls.door_channel = cls.env["pms.sale.channel"].create(
            {"name": "Door", "channel_type": "direct"}
        )
        cls.guest = cls.env["res.partner"].create({"name": "Guest"})
        cls.check_in = date.today() + timedelta(days=10)
        cls.check_out = cls.check_in + timedelta(days=2)

    @classmethod
    def _setup_rates(cls):
        cls.flexible_rule = cls.env["pms.cancelation.rule"].create(
            {
                "name": "Free until two days before",
                "days_intime": 2,
                "penalty_late": 100,
                "apply_on_late": "first",
            }
        )
        cls.non_refundable_rule = cls.env["pms.cancelation.rule"].create(
            {
                "name": "Non refundable",
                "is_non_refundable": True,
                "penalty_late": 100,
                "apply_on_late": "all",
            }
        )
        cls.plan_flexible = cls.env["pms.availability.plan"].create(
            {"name": "Web flexible"}
        )
        cls.plan_non_refundable = cls.env["pms.availability.plan"].create(
            {"name": "Web non refundable"}
        )
        cls.pricelist_flexible = cls.env["product.pricelist"].create(
            {
                "name": "Flexible",
                "sequence": 1,
                "is_pms_available": True,
                "availability_plan_id": cls.plan_flexible.id,
                "cancelation_rule_id": cls.flexible_rule.id,
            }
        )
        cls.pricelist_non_refundable = cls.env["product.pricelist"].create(
            {
                "name": "Non refundable",
                "sequence": 2,
                "is_pms_available": True,
                "availability_plan_id": cls.plan_non_refundable.id,
                "cancelation_rule_id": cls.non_refundable_rule.id,
            }
        )

    @classmethod
    def _setup_rooms(cls):
        room_class = cls.env["pms.room.type.class"].create(
            {"name": "Booking engine rooms", "default_code": "BE"}
        )
        cls.double = cls._create_room_type(room_class, "Double", "BE_DBL", 2, 2)
        cls.single = cls._create_room_type(room_class, "Single", "BE_SGL", 1, 1)
        cls.triple = cls._create_room_type(room_class, "Triple", "BE_TPL", 3, 1)
        # Without prices in any rate
        cls.suite = cls._create_room_type(room_class, "Suite", "BE_STE", 2, 1)

    @classmethod
    def _create_room_type(cls, room_class, name, code, capacity, rooms):
        room_type = cls.env["pms.room.type"].create(
            {
                "name": name,
                "default_code": code,
                "class_id": room_class.id,
                "pms_property_ids": [(6, 0, cls.pms_property.ids)],
                # The placeholder an unpriced night would fall back to
                "list_price": 400.0,
                "taxes_id": [(6, 0, [])],
            }
        )
        for number in range(1, rooms + 1):
            cls.env["pms.room"].create(
                {
                    "name": f"{name} {number}",
                    "pms_property_id": cls.pms_property.id,
                    "room_type_id": room_type.id,
                    "capacity": capacity,
                }
            )
        return room_type

    @classmethod
    def _setup_prices(cls):
        Item = cls.env["product.pricelist.item"]
        for room_type, price in (
            (cls.double, 100.0),
            (cls.single, 60.0),
            (cls.triple, 130.0),
        ):
            Item.create(
                {
                    "pricelist_id": cls.pricelist_flexible.id,
                    "applied_on": "0_product_variant",
                    "product_id": room_type.product_id.id,
                    "compute_price": "fixed",
                    "fixed_price": price,
                }
            )
        for room_type in cls.double | cls.single | cls.triple | cls.suite:
            Item.create(
                {
                    "pricelist_id": cls.pricelist_non_refundable.id,
                    "applied_on": "0_product_variant",
                    "product_id": room_type.product_id.id,
                    "compute_price": "formula",
                    "base": "pricelist",
                    "base_pricelist_id": cls.pricelist_flexible.id,
                    "price_discount": 10.0,
                }
            )

    @classmethod
    def _setup_board(cls):
        cls.breakfast = cls.env["product.product"].create(
            {
                "name": "Breakfast",
                "per_day": True,
                "per_person": True,
                "consumed_on": "after",
                "taxes_id": [(6, 0, [])],
            }
        )
        cls.board_breakfast = cls.env["pms.board.service"].create(
            {
                "name": "Bed and breakfast",
                "default_code": "AD",
                "board_service_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.breakfast.id,
                            "amount": 8.0,
                            "adults": True,
                            "children": True,
                        },
                    )
                ],
            }
        )
        cls.double_breakfast, cls.triple_breakfast = (
            cls.env["pms.board.service.room.type"].create(
                {
                    "pms_board_service_id": cls.board_breakfast.id,
                    "pms_room_type_id": room_type.id,
                    "pms_property_id": cls.pms_property.id,
                }
            )
            for room_type in (cls.double, cls.triple)
        )

    def _get_availability(self, pms_property=None, **params):
        query = {
            "checkIn": self.check_in.isoformat(),
            "checkOut": self.check_out.isoformat(),
            "adults": 2,
            "children": 0,
            "rooms": 1,
        }
        query.update(params)
        pms_property = pms_property or self.pms_property
        with self._create_test_client() as client:
            return client.get(
                f"/properties/{pms_property.id}/availability", params=query
            )

    def _occupy(self, room, check_in, check_out):
        return self.env["pms.reservation"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": room.room_type_id.id,
                "preferred_room_id": room.id,
                "checkin": check_in,
                "checkout": check_out,
                "partner_id": self.guest.id,
                "adults": 1,
                "pricelist_id": self.pricelist_flexible.id,
                "sale_channel_origin_id": self.door_channel.id,
            }
        )

    @staticmethod
    def _room_type(response, room_type):
        return next(
            (
                item
                for item in response.json()["roomTypes"]
                if item["id"] == str(room_type.id)
            ),
            None,
        )

    @staticmethod
    def _offer_totals(room_type_data):
        return {
            offer["id"]: offer["price"]["total"] for offer in room_type_data["offers"]
        }

    @staticmethod
    def _offer_id(pricelist, room_type, board=None):
        return f"{pricelist.id}-{room_type.id}-{board.id if board else 0}"
