# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Room types deriving their price from another one, through the API.

pms prices a room type from another one (its base room type) on the days a
pricelist has no rule of its own for it, e.g. Superior = Double + 10. The
price grid of the planning (``GET /calendar/calendar-prices-rules``) reads the
prices with SQL from the daily items of the pricelist, falling back to the list
price of the room type. For a room type deriving its price it must show the
price the pricing engine sells instead, flagged as derived, and keep showing
the prices set by hand on it (items, which override the derivation).
"""

import datetime
import json
import unittest
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestDerivedRoomTypePrices(TestPms):
    """The Double room type is priced in the pricelist on two dates and the
    Superior one costs 10 more, with no items of its own."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "price_base_room_type_id" not in cls.env["pms.room.type"]._fields:
            raise unittest.SkipTest("The pms module does not derive room type prices.")
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.pms_property_ids = [(4, cls.pms_property1.id)]
        cls.date = fields.Date.today() + datetime.timedelta(days=30)
        cls.next_date = cls.date + datetime.timedelta(days=1)
        cls.double = cls._create_room_type("Double", "DRV_DBL", list_price=400.0)
        cls.superior = cls._create_room_type(
            "Superior",
            "DRV_SUP",
            list_price=300.0,
            price_base_room_type_id=cls.double.id,
            price_derivation_value=10.0,
        )
        cls.double_item = cls._set_price(cls.double, cls.date, 100.0)
        cls._set_price(cls.double, cls.next_date, 120.0)

    @classmethod
    def _create_room_type(cls, name, code, rooms=1, **vals):
        """A room type of the property; the grid only shows those with rooms."""
        values = {
            "name": name,
            "default_code": code,
            "class_id": cls.room_type_class1.id,
            "pms_property_ids": [(6, 0, cls.pms_property1.ids)],
        }
        values.update(vals)
        room_type = cls.env["pms.room.type"].create(values)
        for number in range(1, rooms + 1):
            cls.env["pms.room"].create(
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": f"{name} {number}",
                    "room_type_id": room_type.id,
                    "capacity": 2,
                }
            )
        return room_type

    @classmethod
    def _set_price(cls, room_type, date, price):
        """A price set on a date, as the grid does: a daily item."""
        return cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist1.id,
                "applied_on": "0_product_variant",
                "product_id": room_type.product_id.id,
                "compute_price": "fixed",
                "fixed_price": price,
                "date_start_consumption": date,
                "date_end_consumption": date,
                "pms_property_ids": [(6, 0, cls.pms_property1.ids)],
            }
        )

    def _service(self, usage, env=None):
        collection = _PseudoCollection("pms.services", env or self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage=usage)

    def _json(self, records):
        """What the endpoint answers for its output datamodel(s)."""
        if isinstance(records, list):
            return json.loads(json.dumps([record.dump() for record in records]))
        return json.loads(json.dumps(records.dump()))

    def _grid(self, pricelist_id, date_from=None, date_to=None):
        """The price grid of the property, by room type id and date."""
        search_param = self.env.datamodels["pms.calendar.search.param"](
            dateFrom=str(date_from or self.date),
            dateTo=str(date_to or self.next_date),
            pmsPropertyId=self.pms_property1.id,
            availabilityPlanId=self.availability_plan1.id,
            pricelistId=pricelist_id,
        )
        # The grid reads with SQL what earlier requests stored
        self.env.flush_all()
        rows = self._service("calendar").get_prices_rules_calendar(search_param)
        return {
            (row["roomTypeId"], datetime.date.fromisoformat(date["date"][:10])): date
            for row in self._json(rows)
            for date in row["dates"]
        }

    def _assert_derived(self, cell, price, base_room_type):
        self.assertAlmostEqual(cell["price"], price, places=2)
        self.assertIs(cell["priceDerived"], True)
        self.assertEqual(cell["priceBaseRoomTypeId"], base_room_type.id)
        self.assertIsNone(cell["pricelistItemId"])

    def test_derived_price_of_a_room_type_without_items(self):
        """The grid shows the price the engine derives, flagged as derived."""
        grid = self._grid(self.pricelist1.id)
        self._assert_derived(grid[self.superior.id, self.date], 110.0, self.double)
        self._assert_derived(grid[self.superior.id, self.next_date], 130.0, self.double)
        cell = grid[self.double.id, self.date]
        self.assertEqual(cell["price"], 100.0)
        self.assertEqual(cell["pricelistItemId"], self.double_item.id)
        self.assertIs(cell["priceDerived"], False)
        self.assertIsNone(
            cell["priceBaseRoomTypeId"],
            "The base room type does not derive its price",
        )

    def test_item_of_the_derived_room_type_overrides_its_date(self):
        """A price set on the derived room type is kept on its date only."""
        item = self._set_price(self.superior, self.date, 150.0)
        grid = self._grid(self.pricelist1.id)
        cell = grid[self.superior.id, self.date]
        self.assertEqual(cell["price"], 150.0)
        self.assertEqual(cell["pricelistItemId"], item.id)
        self.assertIs(cell["priceDerived"], False)
        self.assertEqual(
            cell["priceBaseRoomTypeId"],
            self.double.id,
            "The room type still derives its price there: the item is an override",
        )
        self._assert_derived(grid[self.superior.id, self.next_date], 130.0, self.double)

    def test_price_set_through_the_api_overrides_the_derivation(self):
        """Setting the price of a derived room type creates an item, as before."""
        item_info = self.env.datamodels["pms.pricelist.item.info"]
        self._service("pricelists").create_pricelist_item_fix_patch(
            self.pricelist1.id,
            self.env.datamodels["pms.pricelist.items.info"](
                pricelistItems=[
                    item_info(
                        roomTypeId=self.superior.id,
                        date=str(self.date),
                        price=160.0,
                        pricelistId=self.pricelist1.id,
                        pmsPropertyId=self.pms_property1.id,
                    )
                ]
            ),
        )
        grid = self._grid(self.pricelist1.id)
        cell = grid[self.superior.id, self.date]
        self.assertEqual(cell["price"], 160.0)
        self.assertTrue(cell["pricelistItemId"])
        self.assertIs(cell["priceDerived"], False)
        self._assert_derived(grid[self.superior.id, self.next_date], 130.0, self.double)

    def test_base_room_type_without_rule(self):
        """With no rule for its base, the price derives from the list price of
        the base room type, as the engine does, not from its own one."""
        date = self.date + datetime.timedelta(days=5)
        grid = self._grid(self.pricelist1.id, date, date)
        self.assertEqual(grid[self.double.id, date]["price"], 400.0)
        self._assert_derived(grid[self.superior.id, date], 410.0, self.double)

    def test_chain_of_derivations(self):
        """A room type can derive its price from a derived one."""
        family = self._create_room_type(
            "Family",
            "DRV_FAM",
            list_price=500.0,
            price_base_room_type_id=self.superior.id,
            price_derivation_type="percent",
            price_derivation_value=5.0,
        )
        grid = self._grid(self.pricelist1.id)
        # (100 + 10) + 5% and (120 + 10) + 5%
        self._assert_derived(grid[family.id, self.date], 115.5, self.superior)
        self._assert_derived(grid[family.id, self.next_date], 136.5, self.superior)
        self._assert_derived(grid[self.superior.id, self.date], 110.0, self.double)

    def test_base_room_type_without_rooms(self):
        """The base room type may have no rooms, and so no row, in the grid."""
        twin = self._create_room_type("Twin", "DRV_TWN", rooms=0, list_price=400.0)
        self._set_price(twin, self.date, 90.0)
        twin_superior = self._create_room_type(
            "Twin Superior",
            "DRV_TWS",
            price_base_room_type_id=twin.id,
            price_derivation_value=10.0,
        )
        grid = self._grid(self.pricelist1.id)
        self.assertNotIn((twin.id, self.date), grid)
        self._assert_derived(grid[twin_superior.id, self.date], 100.0, twin)

    def test_generic_rule_wins_over_the_derivation(self):
        """A rule of the pricelist covering the derived room type (here a
        global one) wins: its price is shown, and it is not derived."""
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist1.id,
                "applied_on": "3_global",
                "product_id": False,
                "compute_price": "fixed",
                "fixed_price": 77.0,
                "date_start_consumption": self.date,
                "date_end_consumption": self.date,
            }
        )
        grid = self._grid(self.pricelist1.id)
        self.assertEqual(grid[self.double.id, self.date]["price"], 100.0)
        cell = grid[self.superior.id, self.date]
        self.assertEqual(cell["price"], 77.0, "The price the engine sells")
        self.assertIs(cell["priceDerived"], False)
        self.assertEqual(cell["priceBaseRoomTypeId"], self.double.id)
        self.assertIsNone(cell["pricelistItemId"])
        self._assert_derived(grid[self.superior.id, self.next_date], 130.0, self.double)

    def test_derivation_restricted_to_other_pricelists(self):
        """Out of the pricelists it derives in, the room type shows as before."""
        pricelist2 = self.env["product.pricelist"].create(
            {
                "name": "Pricelist 2",
                "availability_plan_id": self.availability_plan1.id,
                "is_pms_available": True,
            }
        )
        self.superior.price_derivation_pricelist_ids = [(6, 0, pricelist2.ids)]
        cell = self._grid(self.pricelist1.id)[self.superior.id, self.date]
        self.assertEqual(cell["price"], 300.0, "Its list price, as before")
        self.assertIs(cell["priceDerived"], False)
        self.assertIsNone(cell["priceBaseRoomTypeId"])
        # No items in that pricelist: the list price of the base room type + 10
        cell = self._grid(pricelist2.id)[self.superior.id, self.date]
        self._assert_derived(cell, 410.0, self.double)
        # The room type tells it for every pricelist, e.g. for the batch
        # price changes of several pricelists at once
        info = self._json(
            self._service("room-types").get_restricted_room_type(self.superior.id)
        )
        self.assertEqual(info["priceBaseRoomTypeId"], self.double.id)
        self.assertEqual(info["priceDerivationPricelistIds"], pricelist2.ids)

    def test_grid_without_pricelist(self):
        """Without a pricelist nothing is derived, as before."""
        cell = self._grid(None)[self.superior.id, self.date]
        self.assertEqual(cell["price"], 300.0)
        self.assertIs(cell["priceDerived"], False)
        self.assertIsNone(cell["priceBaseRoomTypeId"])

    def test_room_type_info(self):
        """The room types tell the room type their price derives from, the
        variation and the pricelists it is derived in (empty for all)."""
        search_param = self.env.datamodels["pms.room.type.search.param"](
            pmsPropertyIds=[self.pms_property1.id]
        )
        room_types = {
            info["id"]: info
            for info in self._json(
                self._service("room-types").get_room_types(search_param)
            )
        }
        superior = room_types[self.superior.id]
        self.assertEqual(superior["priceBaseRoomTypeId"], self.double.id)
        self.assertEqual(superior["priceDerivationType"], "amount")
        self.assertEqual(superior["priceDerivationValue"], 10.0)
        self.assertEqual(superior["priceDerivationPricelistIds"], [])
        double = room_types[self.double.id]
        for key in (
            "priceBaseRoomTypeId",
            "priceDerivationType",
            "priceDerivationValue",
            "priceDerivationPricelistIds",
        ):
            self.assertIn(key, double)
            self.assertIsNone(double[key], "Its price is not derived")
        pricelist2 = self.env["product.pricelist"].create(
            {"name": "Pricelist 2", "availability_plan_id": self.availability_plan1.id}
        )
        pricelists = self.pricelist1 | pricelist2
        self.superior.write(
            {
                "price_derivation_type": "percent",
                "price_derivation_value": 12.5,
                "price_derivation_pricelist_ids": [(6, 0, pricelists.ids)],
            }
        )
        superior = self._json(
            self._service("room-types").get_restricted_room_type(self.superior.id)
        )
        self.assertEqual(superior["priceBaseRoomTypeId"], self.double.id)
        self.assertEqual(superior["priceDerivationType"], "percent")
        self.assertEqual(superior["priceDerivationValue"], 12.5)
        self.assertEqual(
            superior["priceDerivationPricelistIds"], sorted(pricelists.ids)
        )

    def test_pricelists_of_the_derivation_as_stored(self):
        """The pricelists the price is derived in are listed as stored.

        Neither archiving them nor the access of the user asking may empty the
        list, which would read as deriving the price in all the pricelists.
        """
        pms_property2 = self.env["pms.property"].create(
            {
                "name": "Property 2",
                "company_id": self.company1.id,
                "default_pricelist_id": self.pricelist1.id,
            }
        )
        rate_property1, old_rate = self.env["product.pricelist"].create(
            [
                {
                    "name": "Rate of Property 1",
                    "pms_property_ids": [(6, 0, self.pms_property1.ids)],
                    "availability_plan_id": self.availability_plan1.id,
                },
                {
                    "name": "Old Rate",
                    "availability_plan_id": self.availability_plan1.id,
                },
            ]
        )
        pricelists = rate_property1 | old_rate
        # Room types of all the properties
        double = self._create_room_type(
            "Shared Double", "DRV_SHDBL", rooms=0, pms_property_ids=[(6, 0, [])]
        )
        superior = self._create_room_type(
            "Shared Superior",
            "DRV_SHSUP",
            rooms=0,
            pms_property_ids=[(6, 0, [])],
            price_base_room_type_id=double.id,
            price_derivation_value=10.0,
            price_derivation_pricelist_ids=[(6, 0, pricelists.ids)],
        )
        old_rate.active = False
        user = self.env["res.users"].create(
            {
                "name": "Front Desk of Property 2",
                "login": "drv_front_desk",
                "company_id": self.company1.id,
                "company_ids": [(6, 0, self.company1.ids)],
                "pms_property_ids": [(6, 0, pms_property2.ids)],
                "pms_property_id": pms_property2.id,
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("pms.group_pms_user").id,
                        ],
                    )
                ],
            }
        )
        env = self.env(
            user=user,
            context=dict(self.env.context, allowed_company_ids=self.company1.ids),
        )
        # Every request starts with an empty cache: values read before by the
        # superuser would hide what the user can read
        env.invalidate_all()
        self.assertFalse(
            superior.with_env(env).price_derivation_pricelist_ids,
            "Read by the user, the list would be empty",
        )
        # A new request, without what the user read in the cache
        env.invalidate_all()
        search_param = env.datamodels["pms.room.type.search.param"](
            pmsPropertyIds=pms_property2.ids
        )
        room_types = {
            info["id"]: info
            for info in self._json(
                self._service("room-types", env).get_room_types(search_param)
            )
        }
        self.assertEqual(
            room_types[superior.id]["priceDerivationPricelistIds"],
            sorted(pricelists.ids),
        )
        env.invalidate_all()
        info = self._json(
            self._service("room-types", env).get_restricted_room_type(superior.id)
        )
        self.assertEqual(info["priceDerivationPricelistIds"], sorted(pricelists.ids))

    def test_pms_without_price_derivation(self):
        """The endpoints keep answering as before with a pms module that does
        not derive room type prices yet (it may be deployed after this one)."""
        room_type_fields = type(self.env["pms.room.type"])._fields
        with patch.dict(room_type_fields):
            del room_type_fields["price_base_room_type_id"]
            cell = self._grid(self.pricelist1.id)[self.superior.id, self.date]
            superior = self._json(
                self._service("room-types").get_restricted_room_type(self.superior.id)
            )
        self.assertEqual(cell["price"], 300.0)
        self.assertIs(cell["priceDerived"], False)
        self.assertIsNone(cell["priceBaseRoomTypeId"])
        self.assertEqual(superior["id"], self.superior.id)
        self.assertNotIn("priceBaseRoomTypeId", superior)
        self.assertNotIn("priceDerivationPricelistIds", superior)
