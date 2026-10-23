# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import base64
import io
import json
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import pytz
from PIL import Image

from odoo.tests import tagged

from odoo.addons.pms_fastapi_booking_engine.routers.availability import (
    AvailabilitySearch,
    OfferOption,
    RoomTypeOption,
)

from .common import BookingEngineCase


@tagged("post_install", "-at_install")
class TestAvailability(BookingEngineCase):
    def test_stay_and_rate_plans(self):
        response = self._get_availability()

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(
            data["stay"],
            {
                "checkIn": self.check_in.isoformat(),
                "checkOut": self.check_out.isoformat(),
                "nights": 2,
                "adults": 2,
                "children": 0,
                "rooms": 1,
            },
        )
        flexible, non_refundable = data["ratePlans"]
        self.assertEqual(flexible["id"], str(self.pricelist_flexible.id))
        self.assertEqual(flexible["name"], "Flexible")
        self.assertEqual(flexible["cancellation"]["type"], "free")
        deadline = pytz.timezone("Europe/Madrid").localize(
            datetime.combine(self.check_in - timedelta(days=2), time(23, 59, 59))
        )
        self.assertEqual(
            datetime.fromisoformat(flexible["cancellation"]["freeUntil"]), deadline
        )
        self.assertEqual(non_refundable["id"], str(self.pricelist_non_refundable.id))
        self.assertEqual(
            non_refundable["cancellation"],
            {"type": "nonRefundable", "freeUntil": None},
        )

    def test_room_types_and_offers(self):
        response = self._get_availability()

        room_types = response.json()["roomTypes"]
        # The suite has no price in any rate
        self.assertEqual(
            [room_type["id"] for room_type in room_types],
            [str(self.double.id), str(self.single.id), str(self.triple.id)],
        )
        double = room_types[0]
        self.assertEqual(double["name"], "Double")
        self.assertEqual(double["capacity"], 2)
        self.assertEqual(double["availableUnits"], 2)
        self.assertEqual(double["photos"], [])
        self.assertIsNone(double["sizeM2"])
        flexible, non_refundable = (
            self.pricelist_flexible,
            self.pricelist_non_refundable,
        )
        board = self.double_breakfast
        # Room 100/night, breakfast 8 per guest and morning, tourist tax 1.5
        # per adult and night; the non refundable room is 10% cheaper.
        self.assertEqual(
            [(offer["id"], offer["price"]["total"]) for offer in double["offers"]],
            [
                (self._offer_id(non_refundable, self.double), 186.0),
                (self._offer_id(flexible, self.double), 206.0),
                (self._offer_id(non_refundable, self.double, board), 218.0),
                (self._offer_id(flexible, self.double, board), 238.0),
            ],
        )
        offer = double["offers"][1]
        self.assertEqual(offer["ratePlanId"], str(flexible.id))
        self.assertEqual(offer["boardCode"], "RO")
        self.assertEqual(offer["maxQuantity"], 2)
        self.assertEqual(offer["unitsLeft"], 2)
        self.assertEqual(offer["includedServices"], [])
        self.assertEqual(
            offer["price"],
            {
                "total": 206.0,
                "original": None,
                "perNight": 103.0,
                "currency": flexible.currency_id.name,
                "discount": None,
            },
        )
        self.assertEqual(double["offers"][3]["boardCode"], "AD")

    def test_discount_on_the_dearest_rate_with_the_same_board(self):
        response = self._get_availability()

        prices = {
            offer["id"]: offer["price"]
            for offer in self._room_type(response, self.double)["offers"]
        }
        flexible, non_refundable = (
            self.pricelist_flexible,
            self.pricelist_non_refundable,
        )
        board = self.double_breakfast
        # Each non refundable offer is a saving on the flexible one with the
        # same board, which carries no discount.
        self.assertEqual(
            prices[self._offer_id(non_refundable, self.double)],
            {
                "total": 186.0,
                "original": 206.0,
                "perNight": 93.0,
                "currency": flexible.currency_id.name,
                "discount": {"label": "Saving on Flexible", "percent": 9.7},
            },
        )
        with_board = prices[self._offer_id(non_refundable, self.double, board)]
        self.assertEqual(with_board["original"], 238.0)
        self.assertEqual(
            with_board["discount"], {"label": "Saving on Flexible", "percent": 8.4}
        )
        for offer_id in (
            self._offer_id(flexible, self.double),
            self._offer_id(flexible, self.double, board),
        ):
            self.assertIsNone(prices[offer_id]["original"])
            self.assertIsNone(prices[offer_id]["discount"])

    def test_original_price_rounded_for_json(self):
        helper = self.env["pms_booking_engine.availability_router.helper"].new()
        search = AvailabilitySearch(
            pms_property=self.pms_property,
            check_in=self.check_in,
            check_out=self.check_out,
            adults=2,
            children=0,
            rooms=1,
            today=date.today(),
        )
        currency = self.pricelist_flexible.currency_id

        # What currency.round() can return for 100.57
        price = helper._to_price(search, currency, 90.51, 10057 * 0.01)

        self.assertEqual(json.dumps(price.model_dump()["original"]), "100.57")

    def test_discount_compares_the_same_board_service(self):
        # Each rate sells the breakfast through a board of its own
        self.double_breakfast.pricelist_ids = self.pricelist_flexible
        non_refundable_breakfast = self.env["pms.board.service.room.type"].create(
            {
                "pms_board_service_id": self.board_breakfast.id,
                "pms_room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
                "pricelist_ids": [(6, 0, self.pricelist_non_refundable.ids)],
            }
        )

        response = self._get_availability()

        prices = {
            offer["id"]: offer["price"]
            for offer in self._room_type(response, self.double)["offers"]
        }
        price = prices[
            self._offer_id(
                self.pricelist_non_refundable, self.double, non_refundable_breakfast
            )
        ]
        self.assertEqual(price["original"], 238.0)
        self.assertEqual(
            price["discount"], {"label": "Saving on Flexible", "percent": 8.4}
        )

    def test_included_services_from_the_board(self):
        self.breakfast.default_code = "BRK"
        kids_menu = self.env["product.product"].create(
            {"name": "Kids menu", "taxes_id": [(6, 0, [])]}
        )
        # Children get a breakfast of their own price too: listed once
        self.double_breakfast.board_service_line_ids = [
            (0, 0, {"product_id": kids_menu.id, "children": True}),
            (
                0,
                0,
                {"product_id": self.breakfast.id, "amount": 4.0, "children": True},
            ),
        ]
        board_offer = self._offer_id(
            self.pricelist_flexible, self.double, self.double_breakfast
        )

        response = self._get_availability()

        services = {
            offer["id"]: offer["includedServices"]
            for offer in self._room_type(response, self.double)["offers"]
        }
        # No child sleeps in a double for two adults
        self.assertEqual(services[board_offer], [{"code": "BRK", "label": "Breakfast"}])
        self.assertEqual(
            services[self._offer_id(self.pricelist_flexible, self.double)], []
        )

        response = self._get_availability(adults=1, children=1)

        services = {
            offer["id"]: offer["includedServices"]
            for offer in self._room_type(response, self.double)["offers"]
        }
        # A product without internal reference goes by its id
        self.assertEqual(
            services[board_offer],
            [
                {"code": "BRK", "label": "Breakfast"},
                {"code": str(kids_menu.id), "label": "Kids menu"},
            ],
        )

    def test_units_left_only_when_few(self):
        response = self._get_availability()

        double = self._room_type(response, self.double)
        self.assertEqual({offer["unitsLeft"] for offer in double["offers"]}, {2})

        # Up to three units left carry the warning; from four on, none
        for number, units_left in ((3, 3), (4, None)):
            self.env["pms.room"].create(
                {
                    "name": f"Double {number}",
                    "pms_property_id": self.pms_property.id,
                    "room_type_id": self.double.id,
                    "capacity": 2,
                }
            )
            response = self._get_availability()

            offers = self._room_type(response, self.double)["offers"]
            self.assertEqual({offer["maxQuantity"] for offer in offers}, {number})
            self.assertEqual({offer["unitsLeft"] for offer in offers}, {units_left})

    def test_unit_priced_up_to_its_capacity(self):
        response = self._get_availability(adults=2, children=1)

        # The single takes one adult and the triple the whole party, whose
        # child has breakfast but no tourist tax.
        self.assertEqual(
            self._offer_totals(self._room_type(response, self.single)),
            {
                self._offer_id(self.pricelist_flexible, self.single): 123.0,
                self._offer_id(self.pricelist_non_refundable, self.single): 111.0,
            },
        )
        triple = self._offer_totals(self._room_type(response, self.triple))
        self.assertEqual(
            triple[
                self._offer_id(
                    self.pricelist_flexible, self.triple, self.triple_breakfast
                )
            ],
            314.0,
        )

    def test_recommended_one_room(self):
        response = self._get_availability()

        self.assertEqual(
            response.json()["recommended"],
            {
                "coversPeople": 2,
                "items": [
                    {
                        "roomTypeId": str(self.double.id),
                        "offerId": self._offer_id(
                            self.pricelist_non_refundable, self.double
                        ),
                        "quantity": 1,
                    }
                ],
                "price": {
                    "total": 186.0,
                    "original": None,
                    "perNight": 93.0,
                    "currency": self.pricelist_flexible.currency_id.name,
                    "discount": None,
                },
            },
        )

    def test_recommended_combines_room_types(self):
        response = self._get_availability(adults=3, rooms=2)

        recommended = response.json()["recommended"]
        # Double and single leave no spare bed: priced with two adults and one
        self.assertEqual(
            recommended["items"],
            [
                {
                    "roomTypeId": str(self.double.id),
                    "offerId": self._offer_id(
                        self.pricelist_non_refundable, self.double
                    ),
                    "quantity": 1,
                },
                {
                    "roomTypeId": str(self.single.id),
                    "offerId": self._offer_id(
                        self.pricelist_non_refundable, self.single
                    ),
                    "quantity": 1,
                },
            ],
        )
        self.assertEqual(recommended["coversPeople"], 3)
        self.assertEqual(recommended["price"]["total"], 297.0)

    def test_recommended_with_child_needs_a_bigger_room(self):
        response = self._get_availability(adults=2, children=1)

        recommended = response.json()["recommended"]
        self.assertEqual(
            recommended["items"],
            [
                {
                    "roomTypeId": str(self.triple.id),
                    "offerId": self._offer_id(
                        self.pricelist_non_refundable, self.triple
                    ),
                    "quantity": 1,
                }
            ],
        )
        self.assertEqual(recommended["price"]["total"], 240.0)

    def test_recommended_disabled(self):
        self.pms_property.booking_engine_show_recommended = False

        response = self._get_availability()

        self.assertIsNone(response.json()["recommended"])
        self.assertTrue(response.json()["roomTypes"])

    def test_units_free_the_whole_stay(self):
        double_1, double_2 = self.double.room_ids
        self._occupy(double_1, self.check_in, self.check_out)

        response = self._get_availability()

        self.assertEqual(self._room_type(response, self.double)["availableUnits"], 1)

    def test_units_free_only_some_nights_are_not_sold(self):
        double_1, double_2 = self.double.room_ids
        self._occupy(double_1, self.check_in, self.check_in + timedelta(days=1))
        self._occupy(double_2, self.check_in + timedelta(days=1), self.check_out)

        response = self._get_availability()

        self.assertIsNone(self._room_type(response, self.double))

    def test_closed_plan_hides_its_rate(self):
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan_non_refundable.id,
                "room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
                "date": self.check_in,
                "closed": True,
            }
        )

        response = self._get_availability()

        double = self._room_type(response, self.double)
        self.assertEqual(
            {offer["ratePlanId"] for offer in double["offers"]},
            {str(self.pricelist_flexible.id)},
        )

    def test_min_stay_hides_the_rate(self):
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan_flexible.id,
                "room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
                "date": self.check_in,
                "min_stay": 3,
            }
        )

        response = self._get_availability()

        double = self._room_type(response, self.double)
        self.assertEqual(
            {offer["ratePlanId"] for offer in double["offers"]},
            {str(self.pricelist_non_refundable.id)},
        )

    def test_max_avail_limits_the_offer(self):
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan_flexible.id,
                "room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
                "date": self.check_in + timedelta(days=1),
                "max_avail": 1,
            }
        )

        response = self._get_availability()

        double = self._room_type(response, self.double)
        self.assertEqual(double["availableUnits"], 2)
        self.assertEqual(
            {offer["ratePlanId"]: offer["maxQuantity"] for offer in double["offers"]},
            {
                str(self.pricelist_flexible.id): 1,
                str(self.pricelist_non_refundable.id): 2,
            },
        )

    def test_board_restricted_to_other_pricelists(self):
        self.double_breakfast.pricelist_ids = self.pricelist_non_refundable

        response = self._get_availability()

        double = self._room_type(response, self.double)
        self.assertEqual(
            {(offer["ratePlanId"], offer["boardCode"]) for offer in double["offers"]},
            {
                (str(self.pricelist_flexible.id), "RO"),
                (str(self.pricelist_non_refundable.id), "RO"),
                (str(self.pricelist_non_refundable.id), "AD"),
            },
        )

    def test_only_pricelists_published_for_the_property(self):
        other_property = self.env["pms.property"].create(
            {
                "name": "Other hotel",
                "company_id": self.env.company.id,
                "default_pricelist_id": self.pricelist_flexible.id,
            }
        )
        self.pricelist_non_refundable.pms_property_ids = other_property

        response = self._get_availability()

        self.assertEqual(
            [rate["id"] for rate in response.json()["ratePlans"]],
            [str(self.pricelist_flexible.id)],
        )

    def test_taxes_not_included_in_the_price_are_added(self):
        country = self.env.ref("base.es")
        tax = self.env["account.tax"].create(
            {
                "name": "VAT 10% excluded",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include": False,
                "company_id": self.env.company.id,
                "country_id": country.id,
                "tax_group_id": self.env["account.tax.group"]
                .create({"name": "VAT 10%", "country_id": country.id})
                .id,
            }
        )
        self.double.taxes_id = tax

        response = self._get_availability()

        totals = self._offer_totals(self._room_type(response, self.double))
        self.assertEqual(
            totals[self._offer_id(self.pricelist_flexible, self.double)], 226.0
        )

    def test_free_cancellation_already_over(self):
        check_in = date.today() + timedelta(days=1)

        response = self._get_availability(
            checkIn=check_in.isoformat(),
            checkOut=(check_in + timedelta(days=2)).isoformat(),
        )

        flexible = response.json()["ratePlans"][0]
        self.assertEqual(
            flexible["cancellation"], {"type": "nonRefundable", "freeUntil": None}
        )

    def test_photos_and_size_of_the_property(self):
        other_property = self.env["pms.property"].create(
            {
                "name": "Other hotel",
                "company_id": self.env.company.id,
                "default_pricelist_id": self.pricelist_flexible.id,
            }
        )
        # The double is shared with another hotel, which has its own photos
        self.double.pms_property_ids = self.pms_property | other_property
        Images = self.env["pms.room.type.image"]

        def create_image(sequence, pms_property):
            return Images.create(
                {
                    "room_type_id": self.double.id,
                    "pms_property_id": pms_property.id,
                    "sequence": sequence,
                    "image_1920": base64.b64encode(self._png()),
                }
            )

        common = create_image(20, self.env["pms.property"])
        own = create_image(10, self.pms_property)
        create_image(5, other_property)
        double_1, double_2 = self.double.room_ids
        double_1.size_m2 = 18.0
        double_2.size_m2 = 16.0

        double = self._room_type(self._get_availability(), self.double)

        base_url = self.pms_property.get_base_url()
        self.assertEqual(len(double["photos"]), 2)
        for url, image in zip(double["photos"], own | common, strict=True):
            self.assertTrue(
                url.startswith(
                    f"{base_url}/web/image/pms.room.type.image/{image.id}/image_1024"
                    "?unique="
                ),
                url,
            )
        self.assertEqual(double["sizeM2"], 16.0)
        # The single has neither photos nor size
        single = self._room_type(self._get_availability(), self.single)
        self.assertEqual(single["photos"], [])
        self.assertIsNone(single["sizeM2"])

    def test_photos_readable_without_session(self):
        image = self.env["pms.room.type.image"].create(
            {
                "room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
                "image_1920": base64.b64encode(self._png()),
            }
        )
        public_image = image.with_user(self.env.ref("base.public_user"))

        public_image.check_access_rights("read")
        public_image.check_access_rule("read")
        self.assertTrue(public_image.image_1024)

    @staticmethod
    def _png():
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "blue").save(buffer, "PNG")
        return buffer.getvalue()

    def test_without_cancellation_rule_free_until_check_in(self):
        self.pricelist_flexible.cancelation_rule_id = False

        response = self._get_availability()

        flexible = response.json()["ratePlans"][0]
        deadline = pytz.timezone("Europe/Madrid").localize(
            datetime.combine(self.check_in, time(23, 59, 59))
        )
        self.assertEqual(flexible["cancellation"]["type"], "free")
        self.assertEqual(
            datetime.fromisoformat(flexible["cancellation"]["freeUntil"]), deadline
        )

    def test_board_product_not_per_day_billed_once(self):
        # A welcome drink per person, served on arrival: pms.service bills one
        welcome = self.env["product.product"].create(
            {
                "name": "Welcome drink",
                "per_day": False,
                "per_person": True,
                "consumed_on": "checkin",
                "taxes_id": [(6, 0, [])],
            }
        )
        board = self.env["pms.board.service"].create(
            {
                "name": "Welcome",
                "default_code": "WD",
                "board_service_line_ids": [
                    (0, 0, {"product_id": welcome.id, "amount": 5.0, "adults": True})
                ],
            }
        )
        board_double = self.env["pms.board.service.room.type"].create(
            {
                "pms_board_service_id": board.id,
                "pms_room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
            }
        )

        response = self._get_availability()

        totals = self._offer_totals(self._room_type(response, self.double))
        offer_id = self._offer_id(self.pricelist_flexible, self.double, board_double)
        self.assertEqual(totals[offer_id], 211.0)

    def test_board_price_from_a_pricelist_item(self):
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist_flexible.id,
                "applied_on": "0_product_variant",
                "product_id": self.breakfast.id,
                "compute_price": "fixed",
                "fixed_price": 9.0,
            }
        )

        response = self._get_availability()

        totals = self._offer_totals(self._room_type(response, self.double))
        board = self.double_breakfast
        # The flexible rate sells breakfast at 9; the other one keeps the 8
        # of the board
        flexible = self._offer_id(self.pricelist_flexible, self.double, board)
        non_refundable = self._offer_id(
            self.pricelist_non_refundable, self.double, board
        )
        self.assertEqual(totals[flexible], 242.0)
        self.assertEqual(totals[non_refundable], 218.0)

    def test_taxes_computed_like_the_folio(self):
        self.pricelist_flexible.item_ids.filtered(
            lambda item: item.product_id == self.double.product_id
        ).fixed_price = 45.45
        country = self.env.ref("base.es")
        self.double.taxes_id = self.env["account.tax"].create(
            {
                "name": "VAT 10% excluded",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include": False,
                "company_id": self.env.company.id,
                "country_id": country.id,
                "tax_group_id": self.env["account.tax.group"]
                .create({"name": "VAT 10%", "country_id": country.id})
                .id,
            }
        )

        response = self._get_availability()

        # The folio groups both nights in one line: 90.90 + 9.09, not twice
        # 45.45 + 4.55; then the tourist tax
        totals = self._offer_totals(self._room_type(response, self.double))
        self.assertEqual(
            totals[self._offer_id(self.pricelist_flexible, self.double)], 105.99
        )

    def test_recommended_respects_the_availability_plan_limit(self):
        for room in self.single.room_ids | self.triple.room_ids:
            self._occupy(room, self.check_in, self.check_out)
        # The flexible rate can sell one double; the other one is closed
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan_flexible.id,
                "room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
                "date": self.check_in,
                "max_avail": 1,
            }
        )
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan_non_refundable.id,
                "room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
                "date": self.check_in,
                "closed": True,
            }
        )

        response = self._get_availability(adults=4, rooms=2)

        # Room only and breakfast are two offers of one plan that sells one
        # unit: two doubles cannot be recommended
        self.assertIsNone(response.json()["recommended"])

    def test_recommended_never_shares_a_room_with_its_parent(self):
        room_class = self.double.class_id
        dorm = self._create_room_type(room_class, "Dorm", "BE_DRM", 4, 1)
        bed = self._create_room_type(room_class, "Bed", "BE_BED", 1, 0)
        for number in range(1, 5):
            self.env["pms.room"].create(
                {
                    "name": f"Bed {number}",
                    "pms_property_id": self.pms_property.id,
                    "room_type_id": bed.id,
                    "capacity": 1,
                    "parent_id": dorm.room_ids.id,
                }
            )
        for room_type, price in ((dorm, 120.0), (bed, 30.0)):
            self.env["product.pricelist.item"].create(
                {
                    "pricelist_id": self.pricelist_flexible.id,
                    "applied_on": "0_product_variant",
                    "product_id": room_type.product_id.id,
                    "compute_price": "fixed",
                    "fixed_price": price,
                }
            )
        for room in (self.double | self.single | self.triple).room_ids:
            self._occupy(room, self.check_in, self.check_out)

        response = self._get_availability(adults=3, rooms=2)

        # The dorm and one of its beds are both free, but booking the dorm
        # takes its beds: they cannot go together
        self.assertEqual(self._room_type(response, dorm)["availableUnits"], 1)
        self.assertEqual(self._room_type(response, bed)["availableUnits"], 4)
        self.assertIsNone(response.json()["recommended"])
        # Two beds of the same dorm do go together
        two_beds = self._get_availability(adults=2, rooms=2).json()["recommended"]
        self.assertEqual(
            [(item["roomTypeId"], item["quantity"]) for item in two_beds["items"]],
            [(str(bed.id), 2)],
        )

    def test_recommended_among_the_cheapest_room_types(self):
        # A second, dearer room type for two
        deluxe = self._create_room_type(self.double.class_id, "Deluxe", "BE_DLX", 2, 1)
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist_flexible.id,
                "applied_on": "0_product_variant",
                "product_id": deluxe.product_id.id,
                "compute_price": "fixed",
                "fixed_price": 150.0,
            }
        )
        module = "odoo.addons.pms_fastapi_booking_engine.routers.availability"

        with patch(f"{module}.MAX_RECOMMENDATION_COMBINATIONS", 2), self.assertLogs(
            module, level="INFO"
        ) as logs:
            response = self._get_availability()

        # The deluxe goes because the double already fills the room, and the
        # triple, the dearest left, so that two room types remain
        self.assertIn("searched among 2 of 4 room types", logs.output[0])
        recommended = response.json()["recommended"]
        self.assertEqual(recommended["items"][0]["roomTypeId"], str(self.double.id))
        self.assertEqual(recommended["price"]["total"], 186.0)

    def test_recommended_quota_shared_by_the_plans(self):
        for room in self.single.room_ids | self.triple.room_ids:
            self._occupy(room, self.check_in, self.check_out)
        # One double left in the quota of the non refundable plan, the cheaper
        # one, which the reservations of every plan of the room type use up
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan_non_refundable.id,
                "room_type_id": self.double.id,
                "pms_property_id": self.pms_property.id,
                "date": self.check_in,
                "quota": 1,
            }
        )

        response = self._get_availability(adults=4, rooms=2)

        # Mixing one non refundable double with a flexible one would take two
        # units of a quota of one: both go flexible
        recommended = response.json()["recommended"]
        self.assertEqual(
            recommended["items"],
            [
                {
                    "roomTypeId": str(self.double.id),
                    "offerId": self._offer_id(self.pricelist_flexible, self.double),
                    "quantity": 2,
                }
            ],
        )
        self.assertEqual(recommended["price"]["total"], 412.0)

    def test_recommendation_options_keep_what_can_be_sold(self):
        helper = self.env["pms_booking_engine.availability_router.helper"]
        search = AvailabilitySearch(
            pms_property=self.pms_property,
            check_in=self.check_in,
            check_out=self.check_out,
            adults=4,
            children=0,
            rooms=2,
            today=date.today(),
        )

        def option(name, capacity, free_units, max_quantity, amount):
            offer = OfferOption(
                id=name,
                pricelist=self.pricelist_flexible,
                room_type=self.double,
                board=self.env["pms.board.service.room.type"],
                max_quantity=max_quantity,
                max_avail_limit=None,
                quota_limit=None,
            )
            adults, children = helper._get_unit_occupancy(search, capacity)
            search.unit_amounts[(name, adults, children)] = amount
            return RoomTypeOption(
                room_type=self.double,
                capacity=capacity,
                available_units=free_units,
                offers=[offer],
            )

        # The cheapest room type for two has rooms enough, but its rate sells
        # one: the next one stays too, the third goes
        cheap = option("cheap", 2, 5, 1, 100.0)
        second = option("second", 2, 3, 3, 150.0)
        third = option("third", 2, 3, 3, 200.0)
        single = option("single", 1, 3, 3, 60.0)
        options = [cheap, second, third, single]
        module = "odoo.addons.pms_fastapi_booking_engine.routers.availability"

        with patch(f"{module}.MAX_RECOMMENDATION_COMBINATIONS", 6):
            kept = helper._get_recommendation_options(search, options)
        self.assertEqual(kept, [cheap, second, single])

        # Still too many combinations: the dearest goes
        with patch(f"{module}.MAX_RECOMMENDATION_COMBINATIONS", 3):
            kept = helper._get_recommendation_options(search, options)
        self.assertEqual(kept, [cheap, single])

    def test_huge_cancellation_margin_is_non_refundable(self):
        self.flexible_rule.days_intime = 999999

        response = self._get_availability()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["ratePlans"][0]["cancellation"],
            {"type": "nonRefundable", "freeUntil": None},
        )

    def test_board_item_with_minimum_quantity(self):
        # The service prices its lines with quantity 0 when it creates them, so
        # an item asking for one unit does not apply
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist_flexible.id,
                "applied_on": "0_product_variant",
                "product_id": self.breakfast.id,
                "compute_price": "fixed",
                "fixed_price": 9.0,
                "min_quantity": 1,
            }
        )

        response = self._get_availability()

        totals = self._offer_totals(self._room_type(response, self.double))
        offer_id = self._offer_id(
            self.pricelist_flexible, self.double, self.double_breakfast
        )
        self.assertEqual(totals[offer_id], 238.0)

    def test_invalid_parameter_readable_from_another_origin(self):
        endpoint = self.env.ref(
            "pms_fastapi_booking_engine.fastapi_endpoint_booking_engine"
        )
        app = endpoint._get_app()
        # Like the dispatcher, which leaves the errors to Odoo
        endpoint._clear_fastapi_exception_handlers(app)
        self.default_fastapi_router = None
        query = {
            "checkIn": "2026-02-30",
            "checkOut": self.check_out.isoformat(),
            "adults": 2,
            "children": 0,
            "rooms": 1,
        }

        with self._create_test_client(app=app) as client:
            response = client.get(
                f"/properties/{self.pms_property.id}/availability",
                params=query,
                headers={"Origin": "https://www.hotel.example"},
            )
            missing = client.get(
                f"/properties/{self.pms_property.id}/availability",
                params={"checkIn": self.check_in.isoformat()},
                headers={"Origin": "https://www.hotel.example"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        self.assertEqual(response.json()["type"], "/errors/invalid-parameter")
        self.assertEqual(response.json()["field"], "checkIn")
        self.assertEqual(missing.status_code, 400, missing.text)
        self.assertEqual(missing.headers["access-control-allow-origin"], "*")

    def test_openapi_schema(self):
        endpoint = self.env.ref(
            "pms_fastapi_booking_engine.fastapi_endpoint_booking_engine"
        )
        self.default_fastapi_router = None

        with self._create_test_client(app=endpoint._get_app()) as client:
            response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200, response.text)
        schema = response.json()
        self.assertIn("/properties/{propertyId}/availability", schema["paths"])
        self.assertIn("AvailabilityResponse", schema["components"]["schemas"])
        self.assertIn("Problem", schema["components"]["schemas"])
        operation = schema["paths"]["/properties/{propertyId}/availability"]["get"]
        self.assertNotIn("422", operation["responses"])
        # Errors are documented with the media type they are sent with, and
        # with room for the members some of them add
        self.assertEqual(
            list(operation["responses"]["404"]["content"]),
            ["application/problem+json"],
        )
        problem = schema["components"]["schemas"]["Problem"]
        self.assertNotEqual(problem.get("additionalProperties"), False)

    def test_nothing_available_is_not_an_error(self):
        for room in self.env["pms.room"].search(
            [("pms_property_id", "=", self.pms_property.id)]
        ):
            self._occupy(room, self.check_in, self.check_out)

        response = self._get_availability()

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["ratePlans"], [])
        self.assertEqual(data["roomTypes"], [])
        self.assertIsNone(data["recommended"])


@tagged("post_install", "-at_install")
class TestAvailabilityErrors(BookingEngineCase):
    def _assert_problem(self, response, status, error_type, **members):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        problem = response.json()
        self.assertEqual(problem["type"], f"/errors/{error_type}")
        self.assertEqual(problem["status"], status)
        for name, value in members.items():
            self.assertEqual(problem[name], value)

    def test_unknown_property(self):
        unknown = self.pms_property.browse(self.pms_property.id + 1000)

        response = self._get_availability(pms_property=unknown)

        self._assert_problem(response, 404, "property-not-found")

    def test_property_not_sold_online(self):
        self.pms_property.booking_engine_sale_channel_id = False

        response = self._get_availability()

        self._assert_problem(response, 404, "property-not-found")

    def test_booking_engine_channel_archived_or_offline(self):
        for values in ({"active": False}, {"is_on_line": False}):
            with self.subTest(values=values):
                self.engine_channel.write(values)

                response = self._get_availability()

                self._assert_problem(response, 404, "property-not-found")
                self.engine_channel.write({"active": True, "is_on_line": True})

    def test_method_not_allowed(self):
        endpoint = self.env.ref(
            "pms_fastapi_booking_engine.fastapi_endpoint_booking_engine"
        )
        app = endpoint._get_app()
        endpoint._clear_fastapi_exception_handlers(app)
        self.default_fastapi_router = None

        with self._create_test_client(app=app) as client:
            response = client.post(f"/properties/{self.pms_property.id}/availability")

        self._assert_problem(response, 405, "method-not-allowed")
        self.assertEqual(response.headers["allow"], "GET")

    def test_check_out_before_check_in(self):
        response = self._get_availability(checkOut=self.check_in.isoformat())

        self._assert_problem(response, 400, "invalid-date-range")

    def test_check_in_in_the_past(self):
        check_in = date.today() - timedelta(days=2)

        response = self._get_availability(
            checkIn=check_in.isoformat(),
            checkOut=(check_in + timedelta(days=4)).isoformat(),
        )

        self._assert_problem(response, 400, "check-in-in-the-past")

    def test_stay_too_long(self):
        response = self._get_availability(
            checkOut=(self.check_in + timedelta(days=31)).isoformat()
        )

        self._assert_problem(response, 400, "stay-too-long", maxNights=30)

    def test_check_in_too_far(self):
        check_in = date.today() + timedelta(days=800)

        response = self._get_availability(
            checkIn=check_in.isoformat(),
            checkOut=(check_in + timedelta(days=2)).isoformat(),
        )

        self._assert_problem(response, 400, "date-out-of-window", maxDaysAhead=730)

    def test_more_rooms_than_adults(self):
        response = self._get_availability(adults=1, rooms=2)

        self._assert_problem(
            response, 400, "occupancy-out-of-range", field="rooms", max=1
        )

    def test_too_many_children(self):
        response = self._get_availability(children=7)

        self._assert_problem(
            response, 400, "occupancy-out-of-range", field="children", max=6
        )

    def test_without_adults(self):
        response = self._get_availability(adults=0)

        self._assert_problem(
            response, 400, "occupancy-out-of-range", field="adults", min=1
        )
