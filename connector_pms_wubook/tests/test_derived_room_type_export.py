# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Push to Wubook of the prices of the room types deriving their price from
another room type (``pms.room.type.price_base_room_type_id``).

A room type deriving its price has no items on the days it derives it, so
the regular export of the pricelist never sends those prices: the connector
computes them with the pricing engine and pushes them with
``export_derived_prices``. No test reaches Wubook: the adapter is mocked and
the jobs are trapped.
"""

from datetime import date, timedelta
from unittest import SkipTest, mock

from odoo import fields
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.queue_job.job import identity_exact
from odoo.addons.queue_job.tests.common import trap_jobs

from .test_listener_batching import _make_second_backend
from .test_master_sync import _make_backend_environment

ADAPTER_WRITE = (
    "odoo.addons.connector_pms_wubook.models.product_pricelist."
    "adapter.ChannelWubookProductPricelistAdapter.write"
)
BINDING_LOGGER = "odoo.addons.connector_pms_wubook.models.product_pricelist.binding"
IMPORT_LOGGER = (
    "odoo.addons.connector_pms_wubook.models.product_pricelist.mapper_import"
)


def _make_derivation_environment(cls):
    """A regular (non flatten) pricelist bound to Wubook pricing a room type
    on three days, a room type deriving its price from it (+10) and a third
    one deriving its price from the second (+5 %), all of them bound.

    Expected derived prices: 110 / 115.5 on ``d1``, 130 / 136.5 on ``d2``
    and 140 / 147 on ``d3``. On ``d4`` the base room type has no rule (list
    price fallback): nothing is pushed.
    """
    _make_backend_environment(cls)
    cls.mother = cls.room_type_a  # list price 100
    cls.child = cls._create_room_type(
        "RT-MS Superior",
        "RTMSS",
        list_price=300.0,
        price_base_room_type_id=cls.mother.id,
        price_derivation_value=10.0,
    )
    cls.grandchild = cls._create_room_type(
        "RT-MS Family",
        "RTMSF",
        list_price=400.0,
        price_base_room_type_id=cls.child.id,
        price_derivation_type="percent",
        price_derivation_value=5.0,
    )
    RoomTypeBinding = cls.env["channel.wubook.pms.room.type"]
    cls.mother_binding = RoomTypeBinding.create(
        {"odoo_id": cls.mother.id, "backend_id": cls.backend.id, "external_id": 111}
    )
    cls.child_binding = RoomTypeBinding.create(
        {"odoo_id": cls.child.id, "backend_id": cls.backend.id, "external_id": 222}
    )
    cls.grandchild_binding = RoomTypeBinding.create(
        {
            "odoo_id": cls.grandchild.id,
            "backend_id": cls.backend.id,
            "external_id": 333,
        }
    )
    cls.pricelist = cls.env["product.pricelist"].create(
        {"name": "Standard rate", "company_id": cls.company.id}
    )
    cls.d1 = date.today() + timedelta(days=10)
    cls.d2 = cls.d1 + timedelta(days=1)
    cls.d3 = cls.d1 + timedelta(days=2)
    cls.d4 = cls.d1 + timedelta(days=3)
    cls.mother_items = (
        cls._set_price(cls.mother, cls.d1, 100.0)
        | cls._set_price(cls.mother, cls.d2, 120.0)
        | cls._set_price(cls.mother, cls.d3, 130.0)
    )
    cls.pricelist_binding = cls.env["channel.wubook.product.pricelist"].create(
        {
            "odoo_id": cls.pricelist.id,
            "backend_id": cls.backend.id,
            "external_id": 7777,
        }
    )
    # Nothing staged by the setup reaches the tests
    with trap_jobs():
        cls.env.cr.precommit.run()


class DerivedRoomTypeCase(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.env["pms.room.type"]._has_price_derivation():
            # The connector may be installed before pms derives room type
            # prices: nothing to push until then
            raise SkipTest("pms does not derive room type prices")
        _make_derivation_environment(cls)

    @classmethod
    def _create_room_type(cls, name, code, **vals):
        values = {
            "name": name,
            "default_code": code,
            "class_id": cls.room_type_class.id,
            "pms_property_ids": [(6, 0, cls.pms_property.ids)],
        }
        values.update(vals)
        return cls.env["pms.room.type"].create(values)

    @classmethod
    def _set_price(cls, room_type, day, price, pricelist=None):
        return cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": (pricelist or cls.pricelist).id,
                "applied_on": "0_product_variant",
                "compute_price": "fixed",
                "product_id": room_type.product_id.id,
                "fixed_price": price,
                "date_start_consumption": day,
                "date_end_consumption": day,
                "pms_property_ids": [(6, 0, cls.pms_property.ids)],
            }
        )

    def _flush_listeners(self):
        """Run the precommit buffers, dropping the jobs they enqueue."""
        with trap_jobs():
            self.env.cr.precommit.run()

    def _set_default_board_service(self, room_type, amount, capacity):
        """Give the room type a default board service in the property and a
        room of the given capacity, so Wubook prices add amount x capacity."""
        with trap_jobs():
            self.env["pms.room"].create(
                {
                    "pms_property_id": self.pms_property.id,
                    "name": f"{room_type.default_code} 1",
                    "room_type_id": room_type.id,
                    "capacity": capacity,
                }
            )
            breakfast = self.env["product.product"].create(
                {"name": "Breakfast", "type": "service", "list_price": amount}
            )
            board_service = self.env["pms.board.service"].create(
                {
                    "name": "Bed and breakfast",
                    "default_code": "BB",
                    "pms_property_ids": [(6, 0, self.pms_property.ids)],
                    "board_service_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": breakfast.id,
                                "amount": amount,
                                "adults": True,
                            },
                        )
                    ],
                }
            )
            self.env["pms.board.service.room.type"].create(
                {
                    "pms_board_service_id": board_service.id,
                    "pms_room_type_id": room_type.id,
                    "pms_property_id": self.pms_property.id,
                    "by_default": True,
                }
            )
            self.env.cr.precommit.run()

    def _payload(self, date_from, date_to, room_type_ids=None):
        items, skipped = self.pricelist_binding._compute_derived_payload_items(
            date_from, date_to, room_type_ids
        )
        return (
            sorted(
                (item["date"], item["rid"], round(item["price"], 2)) for item in items
            ),
            skipped,
        )


@tagged("post_install", "-at_install")
class TestDerivedPricesPayload(DerivedRoomTypeCase):
    """What ``export_derived_prices`` pushes, and what it never pushes."""

    def test_payload_has_the_derived_prices_only(self):
        payload, skipped = self._payload(self.d1, self.d4)
        self.assertEqual(
            payload,
            [
                (self.d1, 222, 110.0),
                (self.d1, 333, 115.5),
                (self.d2, 222, 130.0),
                (self.d2, 333, 136.5),
                (self.d3, 222, 140.0),
                (self.d3, 333, 147.0),
            ],
            "The base room type has items of its own (rid 111 not pushed) and no "
            "price derives from a list price (nothing on d4)",
        )
        self.assertEqual(skipped, [])

    def test_override_of_a_derived_room_type(self):
        """An item of the derived room type is an override: exported as an
        item, never as a derived price. Its own derived room types follow it.
        """
        override = self._set_price(self.child, self.d2, 150.0)
        payload, _skipped = self._payload(self.d1, self.d3)
        self.assertEqual(
            payload,
            [
                (self.d1, 222, 110.0),
                (self.d1, 333, 115.5),
                (self.d2, 333, 157.5),
                (self.d3, 222, 140.0),
                (self.d3, 333, 147.0),
            ],
        )
        override.unlink()
        payload, _skipped = self._payload(self.d2, self.d2)
        self.assertEqual(
            payload,
            [(self.d2, 222, 130.0), (self.d2, 333, 136.5)],
            "Without the override the room type derives its price again",
        )

    def test_items_the_engine_does_not_apply(self):
        """The export of the items sends an item of the room type even if the
        engine does not apply it (e.g. past its sale end date): no derived
        price on its day, or the plan would get two prices for the room."""
        override = self._set_price(self.child, self.d2, 150.0)
        override.date_end = fields.Datetime.now() - timedelta(days=1)
        product = self.child.product_id
        _price, rule_id = self.pricelist._compute_price_rule(
            product, 1, consumption_date=self.d2, pms_property_id=self.pms_property.id
        )[product.id]
        self.assertFalse(rule_id, "The engine does not apply the item")
        payload, _skipped = self._payload(self.d1, self.d3)
        self.assertEqual(
            payload,
            [
                (self.d1, 222, 110.0),
                (self.d1, 333, 115.5),
                (self.d2, 333, 136.5),
                (self.d3, 222, 140.0),
                (self.d3, 333, 147.0),
            ],
            "Nothing for the room type on d2, the price of the next one derives "
            "from the one of the engine (120 + 10)",
        )

    def test_prices_rounded_to_the_cent(self):
        """No floating point noise reaches Wubook: 154 + 5 % reads
        161.70000000000002 once rounded to the currency."""
        self.mother_items[0].fixed_price = 144.0
        items, _skipped = self.pricelist_binding._compute_derived_payload_items(
            self.d1, self.d1
        )
        prices = {item["rid"]: item["price"] for item in items}
        self.assertEqual(prices, {222: 154.0, 333: 161.7})
        # What the XML-RPC call serializes
        self.assertEqual(repr(prices[333]), "161.7")

    def test_price_derived_from_an_override(self):
        """The price comes from the first room type of the chain with a rule:
        an override is as explicit as an item of the base room type."""
        self._set_price(self.child, self.d4, 150.0)
        payload, _skipped = self._payload(self.d4, self.d4)
        self.assertEqual(
            payload,
            [(self.d4, 333, 157.5)],
            "No rule for the base room type on d4, but the price of the last "
            "room type derives from the override of the second one",
        )

    def test_scope_restricted_to_room_types(self):
        payload, _skipped = self._payload(self.d1, self.d1, [self.grandchild.id])
        self.assertEqual(payload, [(self.d1, 333, 115.5)])

    def test_prices_include_the_default_board_service(self):
        """Derived prices get the default board service like the items do."""
        self._set_default_board_service(self.child, amount=6.0, capacity=2)
        payload, _skipped = self._payload(self.d1, self.d1)
        self.assertEqual(
            payload,
            [(self.d1, 222, 122.0), (self.d1, 333, 115.5)],
            "110 + 6 x 2 for the room type with the board service, the price of "
            "the other one derives from the room price (110) only",
        )
        # The items export adds the same amount
        item = self._set_price(self.child, self.d4, 140.0)
        item_binding = (
            self.env["channel.wubook.product.pricelist.item"]
            .with_context(connector_no_export=True)
            .create({"odoo_id": item.id, "backend_id": self.backend.id})
        )
        with self.backend.work_on("channel.wubook.product.pricelist.item") as work:
            mapper = work.component(usage="export.mapper")
        values = mapper.item(item_binding)
        self.assertEqual(values["rid"], 222)
        self.assertAlmostEqual(values["price"], 152.0, places=2)

    def test_prices_not_above_0_01_are_left_out(self):
        """Wubook rejects them: a derivation down to zero is not pushed, even
        with a board service on top of it."""
        self._set_default_board_service(self.child, amount=6.0, capacity=2)
        self.child.with_context(
            connector_no_export=True
        ).price_derivation_value = -100.0
        payload, skipped = self._payload(self.d1, self.d2)
        self.assertEqual(
            payload,
            [(self.d2, 222, 32.0), (self.d2, 333, 21.0)],
            "d1: 100 - 100 = 0 for both; d2: 20 (+ 12 of board service) and 21",
        )
        self.assertEqual(
            sorted((room_type.id, day) for room_type, day in skipped),
            sorted([(self.child.id, self.d1), (self.grandchild.id, self.d1)]),
        )

    def test_no_export_bindings_are_left_out(self):
        """A binding marked ``no_export`` (e.g. a room anchored to another one
        in Wubook) gets no prices."""
        self.child_binding.with_context(connector_no_export=True).no_export = True
        payload, _skipped = self._payload(self.d1, self.d1)
        self.assertEqual(payload, [(self.d1, 333, 115.5)])

    def test_unbound_room_types_are_left_out(self):
        self.grandchild_binding.unlink()
        payload, _skipped = self._payload(self.d1, self.d1)
        self.assertEqual(payload, [(self.d1, 222, 110.0)])

    def test_room_type_classes_not_synchronized_are_left_out(self):
        nosync_class = self.env["pms.room.type.class"].create(
            {"name": "RTC not synchronized", "default_code": "RTCNS"}
        )
        self.backend.backend_type_id.child_id.write(
            {
                "room_type_class_ids": [
                    (0, 0, {"wubook_room_type": "-1", "room_type_shortname": "RTCNS"})
                ]
            }
        )
        self.child.with_context(connector_no_export=True).class_id = nosync_class
        payload, _skipped = self._payload(self.d1, self.d1)
        self.assertEqual(payload, [(self.d1, 333, 115.5)])

    def test_room_types_not_deriving_in_the_pricelist(self):
        """A room type set to derive in other pricelists has its list price
        here, so the room types deriving from it get no price either."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other rate", "company_id": self.company.id}
        )
        self.child.with_context(connector_no_export=True).write(
            {"price_derivation_pricelist_ids": [(6, 0, other_pricelist.ids)]}
        )
        payload, skipped = self._payload(self.d1, self.d3)
        self.assertEqual(payload, [])
        self.assertEqual(skipped, [])

    def test_export_room_types(self):
        self.assertEqual(
            self.pricelist_binding._get_derived_export_room_types(),
            {self.child: 222, self.grandchild: 333},
        )
        self.assertEqual(
            self.pricelist_binding._get_derived_export_room_types([self.child.id]),
            {self.child: 222},
        )


@tagged("post_install", "-at_install")
class TestDerivedPricesExport(DerivedRoomTypeCase):
    """The ``export_derived_prices`` job and its window."""

    def test_export_pushes_the_derived_prices_to_the_plan(self):
        with mock.patch(ADAPTER_WRITE) as mocked_write:
            result = self.pricelist_binding.export_derived_prices(
                date_from=self.d1, date_to=self.d4
            )
        mocked_write.assert_called_once()
        external_id, payload = mocked_write.call_args[0]
        self.assertEqual(external_id, 7777)
        self.assertEqual(set(payload), {"type", "items"})
        self.assertEqual(payload["type"], "standard")
        self.assertEqual(len(payload["items"]), 6)
        self.assertEqual(
            payload["items"],
            sorted(payload["items"], key=lambda item: item["date"]),
        )
        self.assertEqual(
            result,
            f"Derived room type prices exported: {self.d1}..{self.d4} (6 prices)",
        )

    def test_export_without_prices_does_not_call_wubook(self):
        with mock.patch(ADAPTER_WRITE) as mocked_write:
            result = self.pricelist_binding.export_derived_prices(
                date_from=self.d4, date_to=self.d4
            )
        mocked_write.assert_not_called()
        self.assertEqual(result, "No derived room type prices to export")

    def test_export_requires_a_standard_plan_in_wubook(self):
        self.pricelist_binding.with_context(
            connector_no_export=True
        ).external_id = False
        with mock.patch(ADAPTER_WRITE) as mocked_write:
            result = self.pricelist_binding.export_derived_prices(
                date_from=self.d1, date_to=self.d3
            )
        mocked_write.assert_not_called()
        self.assertEqual(result, "No derived room type prices to export")

    @mute_logger(BINDING_LOGGER)
    def test_export_reports_the_prices_left_out(self):
        self.child.with_context(
            connector_no_export=True
        ).price_derivation_value = -100.0
        with mock.patch(ADAPTER_WRITE) as mocked_write:
            result = self.pricelist_binding.export_derived_prices(
                date_from=self.d1, date_to=self.d1
            )
        mocked_write.assert_not_called()
        self.assertEqual(
            result,
            "No derived room type prices to export\n"
            "2 prices not above 0.01, which Wubook rejects, were left out",
        )

    @mute_logger(BINDING_LOGGER)
    @mock.patch(ADAPTER_WRITE)
    def test_export_messages_in_spanish(self, mocked_write):
        self.env["res.lang"]._activate_lang("es_ES")
        binding = self.pricelist_binding.with_context(lang="es_ES")
        result = binding.export_derived_prices(date_from=self.d1, date_to=self.d1)
        self.assertEqual(
            result,
            "Precios de tipologías derivadas exportados: "
            f"{self.d1}..{self.d1} (2 precios)",
        )
        result = binding.export_derived_prices(date_from=self.d4, date_to=self.d4)
        self.assertEqual(result, "No hay precios de tipologías derivadas que exportar")
        self.child.with_context(
            connector_no_export=True
        ).price_derivation_value = -100.0
        result = binding.export_derived_prices(date_from=self.d1, date_to=self.d1)
        mocked_write.assert_called_once()
        self.assertEqual(
            result,
            "No hay precios de tipologías derivadas que exportar\n"
            "Se han omitido 2 precios no superiores a 0.01, que Wubook rechaza",
        )

    def test_window_help_mentions_the_derived_prices(self):
        """The flatten window is also the one of the derived prices."""
        self.env["res.lang"]._activate_lang("es_ES")
        self.env["ir.module.module"]._load_module_terms(
            ["connector_pms_wubook"], ["es_ES"], overwrite=True
        )
        Backend = self.env["channel.wubook.backend"]
        help_en = Backend.with_context(lang="en_US").fields_get(
            ["flatten_window_days"]
        )["flatten_window_days"]["help"]
        self.assertIn("room types derived from another room type", help_en)
        help_es = Backend.with_context(lang="es_ES").fields_get(
            ["flatten_window_days"]
        )["flatten_window_days"]["help"]
        self.assertIn("tipologías derivadas de otra tipología", help_es)

    def test_export_window_within_the_dates_wubook_accepts(self):
        today = date.today()
        Binding = self.env["channel.wubook.product.pricelist"]
        self.assertEqual(
            Binding._get_wubook_window(
                today - timedelta(days=10), today + timedelta(days=800)
            ),
            (today - timedelta(days=2), today + timedelta(days=730)),
        )
        self.assertEqual(
            Binding._get_wubook_window(
                today - timedelta(days=10), today - timedelta(days=5)
            ),
            (None, None),
        )
        with mock.patch.object(
            type(self.pricelist_binding),
            "_compute_derived_payload_items",
            return_value=([], []),
        ) as mocked_payload:
            self.pricelist_binding.export_derived_prices(
                date_from=today - timedelta(days=10),
                date_to=today + timedelta(days=800),
            )
        mocked_payload.assert_called_once_with(
            today - timedelta(days=2), today + timedelta(days=730), None
        )

    def test_default_window(self):
        today = date.today()
        self.backend.flatten_window_days = 540
        self.assertEqual(
            self.pricelist_binding._get_derived_default_window(),
            (today, self.d3),
            "Capped by the last day an item of the pricelist applies to",
        )
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "compute_price": "fixed",
                "product_id": self.mother.product_id.id,
                "fixed_price": 90.0,
                "date_start_consumption": self.d4,
            }
        )
        self.assertEqual(
            self.pricelist_binding._get_derived_default_window(),
            (today, today + timedelta(days=539)),
            "An item with no end applies up to the flatten window",
        )
        self.backend.flatten_window_days = 900
        self.assertEqual(
            self.pricelist_binding._get_derived_default_window(),
            (today, today + timedelta(days=730)),
        )
        self.backend.flatten_window_days = 0
        self.assertEqual(
            self.pricelist_binding._get_derived_default_window(), (None, None)
        )

    def test_export_without_dates_uses_the_default_window(self):
        with mock.patch(ADAPTER_WRITE) as mocked_write:
            self.pricelist_binding.export_derived_prices()
        payload = mocked_write.call_args[0][1]
        self.assertEqual(
            {item["date"] for item in payload["items"]}, {self.d1, self.d2, self.d3}
        )

    def test_flatten_pricelist_delegates_to_the_flatten_export(self):
        """Flatten plans price every room type, the derived ones included."""
        flatten_pricelist = self.env["product.pricelist"].create(
            {
                "name": "Flatten rate",
                "company_id": self.company.id,
                "wubook_flatten_to_daily": True,
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "3_global",
                            "compute_price": "formula",
                            "base": "pricelist",
                            "base_pricelist_id": self.pricelist.id,
                            "price_discount": -10.0,
                        },
                    )
                ],
            }
        )
        flatten_binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": flatten_pricelist.id,
                "backend_id": self.backend.id,
                "external_id": 8888,
            }
        )
        with mock.patch.object(
            type(flatten_binding), "export_flattened"
        ) as mocked_flatten:
            flatten_binding.export_derived_prices(
                date_from=self.d1, date_to=self.d2, room_type_ids=[self.child.id]
            )
        mocked_flatten.assert_called_once_with(self.d1, self.d2, [self.child.id])


@tagged("post_install", "-at_install")
class TestDerivedPricesListeners(DerivedRoomTypeCase):
    """What enqueues ``export_derived_prices`` (and ``export_flattened`` for
    the derived room types of flatten plans)."""

    def _make_flatten_pricelist(self):
        flatten_pricelist = self.env["product.pricelist"].create(
            {
                "name": "Flatten rate",
                "company_id": self.company.id,
                "wubook_flatten_to_daily": True,
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "3_global",
                            "compute_price": "formula",
                            "base": "pricelist",
                            "base_pricelist_id": self.pricelist.id,
                            "price_discount": -10.0,
                        },
                    )
                ],
            }
        )
        flatten_binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": flatten_pricelist.id,
                "backend_id": self.backend.id,
                "external_id": 8888,
            }
        )
        self._flush_listeners()
        return flatten_binding

    def test_base_item_change_enqueues_the_derived_prices(self):
        with trap_jobs() as trap:
            self.mother_items[0].fixed_price = 105.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={
                "date_from": self.d1,
                "date_to": self.d1,
                "room_type_ids": sorted([self.child.id, self.grandchild.id]),
            },
            properties={"identity_key": identity_exact},
        )

    def test_base_items_changes_collapse_to_one_job(self):
        d6 = self.d1 + timedelta(days=5)
        d9 = self.d1 + timedelta(days=8)
        with trap_jobs() as trap:
            self._set_price(self.mother, d9, 140.0)
            self._set_price(self.mother, d6, 150.0)
            self.mother_items[1].fixed_price = 125.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={
                "date_from": self.d2,
                "date_to": d9,
                "room_type_ids": sorted([self.child.id, self.grandchild.id]),
            },
        )

    def test_override_removal_pushes_the_derived_price_again(self):
        override = self._set_price(self.child, self.d2, 150.0)
        self._flush_listeners()
        with trap_jobs() as trap:
            override.unlink()
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={
                "date_from": self.d2,
                "date_to": self.d2,
                "room_type_ids": sorted([self.child.id, self.grandchild.id]),
            },
        )

    def test_moving_an_override_to_other_days(self):
        """The room type derives its price again on the days the override
        leaves: they are pushed as well."""
        override = self._set_price(self.child, self.d2, 150.0)
        self._flush_listeners()
        with trap_jobs() as trap:
            override.write(
                {"date_start_consumption": self.d4, "date_end_consumption": self.d4}
            )
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={
                "date_from": self.d2,
                "date_to": self.d4,
                "room_type_ids": sorted([self.child.id, self.grandchild.id]),
            },
        )

    def test_moving_an_override_to_another_room_type(self):
        """The room type the override leaves derives its price again."""
        override = self._set_price(self.child, self.d2, 150.0)
        self._flush_listeners()
        with trap_jobs() as trap:
            override.product_id = self.grandchild.product_id
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={
                "date_from": self.d2,
                "date_to": self.d2,
                "room_type_ids": sorted([self.child.id, self.grandchild.id]),
            },
        )

    def test_moving_an_item_to_another_pricelist(self):
        """Both pricelists get the derived prices of the day: the one the item
        leaves and the one it lands on."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Non refundable", "company_id": self.company.id}
        )
        self._set_price(self.mother, self.d2, 110.0, pricelist=other_pricelist)
        other_binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": other_pricelist.id,
                "backend_id": self.backend.id,
                "external_id": 7778,
            }
        )
        self._flush_listeners()
        with trap_jobs() as trap:
            self.mother_items[0].pricelist_id = other_pricelist
            self.env.cr.precommit.run()
        trap.assert_jobs_count(4)
        for binding in self.pricelist_binding | other_binding:
            trap.assert_enqueued_job(
                binding.export_derived_prices,
                kwargs={
                    "date_from": self.d1,
                    "date_to": self.d1,
                    "room_type_ids": sorted([self.child.id, self.grandchild.id]),
                },
            )

    def test_moving_an_item_without_export(self):
        """``connector_no_export`` (e.g. the import of prices from Wubook)
        silences the move as well."""
        with trap_jobs() as trap:
            self.mother_items[0].with_context(connector_no_export=True).write(
                {"date_start_consumption": self.d4, "date_end_consumption": self.d4}
            )
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)

    def test_backends_without_derived_room_types(self):
        """A backend with no room type deriving its price bound has no derived
        price of the pricelist to push: its binding only gets the export of
        the items."""
        _make_second_backend(self)
        other_binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": self.pricelist.id,
                "backend_id": self.backend_b.id,
                "external_id": 7778,
            }
        )
        self._flush_listeners()
        with trap_jobs() as trap:
            # For all the properties, so for both backends
            self.mother_items[0].pms_property_ids = False
            self.env.cr.precommit.run()
        trap.assert_jobs_count(3)
        trap.assert_enqueued_job(
            other_binding.export_record,
            args=(self.backend_b, self.pricelist),
        )
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={
                "date_from": self.d1,
                "date_to": self.d1,
                "room_type_ids": sorted([self.child.id, self.grandchild.id]),
            },
        )

    def test_derived_scope_of_an_item_change(self):
        """An item of a room type deriving its price re-pushes the room type
        itself (an override created, changed or removed); an item of a room
        type no other one derives from only needs the regular export."""
        with trap_jobs() as trap:
            self._set_price(self.grandchild, self.d1, 200.0)
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={
                "date_from": self.d1,
                "date_to": self.d1,
                "room_type_ids": [self.grandchild.id],
            },
        )
        other = self._create_room_type("RT-MS Other", "RTMSO", list_price=90.0)
        with trap_jobs() as trap:
            self._set_price(other, self.d1, 95.0)
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )

    def test_rule_for_all_room_types_with_no_dates(self):
        """A rule for no room type in particular may move any derived price,
        and with no consumption dates, on any day: default window, all."""
        with trap_jobs() as trap:
            self.mother_items[0].fixed_price = 105.0
            self.env["product.pricelist.item"].create(
                {
                    "pricelist_id": self.pricelist.id,
                    "applied_on": "3_global",
                    "compute_price": "formula",
                    "base": "list_price",
                    "price_discount": 10.0,
                }
            )
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={},
        )

    def test_undated_item_staged_before_a_dated_one(self):
        """Regression: an empty date reads ``False``, which the staging of the
        listener compared with the dates of the other items of the
        transaction (TypeError on any change mixing both kinds of items).
        The item with no dates may move prices on any day: default window,
        for the flatten descendants as well."""
        flatten_binding = self._make_flatten_pricelist()
        with trap_jobs() as trap:
            self.env["product.pricelist.item"].create(
                {
                    "pricelist_id": self.pricelist.id,
                    "applied_on": "3_global",
                    "compute_price": "formula",
                    "base": "list_price",
                    "price_discount": 10.0,
                }
            )
            self.mother_items[0].fixed_price = 105.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(3)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={},
        )
        trap.assert_enqueued_job(flatten_binding.export_flattened, kwargs={})

    def test_pricelist_without_derived_room_types(self):
        with trap_jobs():
            (self.child | self.grandchild).with_context(connector_no_export=True).write(
                {"price_base_room_type_id": False}
            )
        with trap_jobs() as trap:
            self.mother_items[0].fixed_price = 105.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )

    def test_flatten_descendants_include_the_derived_room_types(self):
        flatten_binding = self._make_flatten_pricelist()
        with trap_jobs() as trap:
            self.mother_items[0].fixed_price = 105.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(3)
        trap.assert_enqueued_job(
            flatten_binding.export_flattened,
            kwargs={
                "date_from": self.d1,
                "date_to": self.d1,
                "room_type_ids": sorted(
                    [self.mother.id, self.child.id, self.grandchild.id]
                ),
            },
        )

    def test_derivation_change_enqueues_the_room_type_and_its_derived_ones(self):
        with trap_jobs() as trap:
            self.child.price_derivation_value = 15.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={"room_type_ids": sorted([self.child.id, self.grandchild.id])},
            properties={"identity_key": identity_exact},
        )

    def test_derivation_removal_enqueues_the_ones_still_deriving(self):
        with trap_jobs() as trap:
            self.child.price_base_room_type_id = False
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={"room_type_ids": [self.grandchild.id]},
        )

    def test_derivation_change_on_flatten_plans(self):
        flatten_binding = self._make_flatten_pricelist()
        with trap_jobs() as trap:
            self.child.write(
                {"price_derivation_type": "percent", "price_derivation_value": 10.0}
            )
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            flatten_binding.export_flattened,
            kwargs={"room_type_ids": sorted([self.child.id, self.grandchild.id])},
        )
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={"room_type_ids": sorted([self.child.id, self.grandchild.id])},
        )

    def test_derivation_change_of_unbound_room_types(self):
        mother = self._create_room_type("RT-U", "RTU", list_price=80.0)
        child = self._create_room_type(
            "RT-U Superior",
            "RTUS",
            list_price=90.0,
            price_base_room_type_id=mother.id,
            price_derivation_value=5.0,
        )
        self._flush_listeners()
        with trap_jobs() as trap:
            child.price_derivation_value = 7.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)

    def test_derivation_change_without_export(self):
        with trap_jobs() as trap:
            self.child.with_context(
                connector_no_export=True
            ).price_derivation_value = 15.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)

    def test_pms_without_price_derivation(self):
        """The connector may be updated before ``pms``: until ``pms`` derives
        room type prices, nothing is derived nor pushed as such."""
        with mock.patch.object(
            type(self.env["pms.room.type"]), "_has_price_derivation", return_value=False
        ), trap_jobs() as trap:
            self.mother_items[0].fixed_price = 105.0
            self.env.cr.precommit.run()
            self.backend.export_pricelists()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )

    def test_export_pricelists_pushes_the_derived_prices(self):
        with trap_jobs() as trap:
            self.backend.export_pricelists()
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={},
            properties={"identity_key": identity_exact},
        )

    def test_export_pricelists_without_derived_room_types(self):
        with trap_jobs():
            (self.child | self.grandchild).with_context(connector_no_export=True).write(
                {"price_base_room_type_id": False}
            )
        with trap_jobs() as trap:
            self.backend.export_pricelists()
        trap.assert_jobs_count(1)

    def test_connecting_a_derived_room_type_pushes_its_prices(self):
        suite = self._create_room_type(
            "RT-MS Suite",
            "RTMSU",
            list_price=500.0,
            price_base_room_type_id=self.mother.id,
            price_derivation_value=50.0,
        )
        self._flush_listeners()
        wizard = self.env["channel.wubook.connect.wizard"].create(
            {
                "res_model": "pms.room.type",
                "res_id": suite.id,
                "backend_id": self.backend.id,
                "mode": "manual",
                "manual_external_id": 444,
            }
        )
        with trap_jobs() as trap:
            wizard.action_connect()
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_derived_prices,
            kwargs={"room_type_ids": [suite.id]},
        )

    def test_creating_a_plan_in_wubook_pushes_its_derived_prices(self):
        pricelist = self.env["product.pricelist"].create(
            {"name": "Non refundable", "company_id": self.company.id}
        )
        self._set_price(self.mother, self.d1, 90.0, pricelist=pricelist)
        self._flush_listeners()
        wizard = self.env["channel.wubook.connect.wizard"].create(
            {
                "res_model": "product.pricelist",
                "res_id": pricelist.id,
                "backend_id": self.backend.id,
                "mode": "new",
            }
        )
        PricelistBinding = self.env["channel.wubook.product.pricelist"]

        def fake_export_record(backend, relation):
            PricelistBinding.search([("odoo_id", "=", relation.id)]).with_context(
                connector_no_export=True
            ).external_id = 9999

        with mock.patch.object(
            type(PricelistBinding), "export_record", side_effect=fake_export_record
        ), trap_jobs() as trap:
            wizard.action_connect()
        binding = PricelistBinding.search([("odoo_id", "=", pricelist.id)])
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(binding.export_derived_prices, kwargs={})


@tagged("post_install", "-at_install")
class TestDerivedPricesImport(DerivedRoomTypeCase):
    """Importing plan prices from Wubook never creates items (overrides) for
    the room types deriving their price in the pricelist."""

    def _import_values(self, binding=None):
        """The products of the items the import creates or updates."""
        return [vals["product_id"] for _op, _id, vals in self._import_commands(binding)]

    def _import_commands(self, binding=None):
        external_data = {
            "id": 7777,
            "name": "Standard rate",
            "daily": 1,
            "type": "standard",
            "items": [
                {"rid": 111, "date": self.d4, "price": 90.0},
                {"rid": 222, "date": self.d4, "price": 100.0},
                {"rid": 333, "date": self.d4, "price": 105.0},
            ],
        }
        with self.backend.work_on("channel.wubook.product.pricelist") as work:
            mapper = work.component(usage="import.mapper")
        map_record = mapper.map_record(external_data)
        if binding:
            values = map_record.values(binding=binding)
        else:
            values = map_record.values(for_create=True)
        return values["channel_wubook_item_ids"]

    def test_import_leaves_out_the_derived_room_types(self):
        with self.assertLogs(IMPORT_LOGGER, level="INFO") as logs:
            products = self._import_values(self.pricelist_binding)
        self.assertEqual(products, [self.mother.product_id.id])
        self.assertIn("Skipped 2 new prices", logs.output[0])

    def test_import_updates_the_overrides(self):
        """Only new items are left out: the items the room types deriving
        their price already have, overrides, are updated as any other one."""
        override = self._set_price(self.child, self.d4, 150.0)
        with self.assertLogs(IMPORT_LOGGER, level="INFO") as logs:
            commands = self._import_commands(self.pricelist_binding)
        self.assertEqual(
            sorted(
                (vals["product_id"], vals["fixed_price"], vals.get("odoo_id"))
                for _op, _id, vals in commands
            ),
            sorted(
                [
                    (self.mother.product_id.id, 90.0, None),
                    (self.child.product_id.id, 100.0, override.id),
                ]
            ),
            "New item of the base room type, update of the override, nothing "
            "for the last room type",
        )
        self.assertIn("Skipped 1 new prices", logs.output[0])

    def test_import_into_a_new_pricelist(self):
        """A new pricelist: the room types deriving their price in all the
        pricelists derive it there, not the ones restricted to others."""
        with mute_logger(IMPORT_LOGGER):
            products = self._import_values()
        self.assertEqual(products, [self.mother.product_id.id])
        self.grandchild.with_context(connector_no_export=True).write(
            {"price_derivation_pricelist_ids": [(6, 0, self.pricelist.ids)]}
        )
        with mute_logger(IMPORT_LOGGER):
            products = self._import_values()
        self.assertEqual(
            sorted(products),
            sorted([self.mother.product_id.id, self.grandchild.product_id.id]),
        )

    def test_import_of_room_types_not_deriving_in_the_pricelist(self):
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other rate", "company_id": self.company.id}
        )
        (self.child | self.grandchild).with_context(connector_no_export=True).write(
            {"price_derivation_pricelist_ids": [(6, 0, other_pricelist.ids)]}
        )
        products = self._import_values(self.pricelist_binding)
        self.assertEqual(
            sorted(products),
            sorted(
                [
                    self.mother.product_id.id,
                    self.child.product_id.id,
                    self.grandchild.product_id.id,
                ]
            ),
        )
