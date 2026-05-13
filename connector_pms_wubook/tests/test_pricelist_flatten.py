# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from datetime import date, timedelta
from unittest import mock

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.queue_job.tests.common import trap_jobs


class TestPricelistFlatten(TransactionCase):
    """Tests for the flatten-to-daily helpers on product.pricelist.

    These tests exercise the pure-Odoo side of the feature (model fields,
    chain helpers, synthetic item computation) without requiring a live
    Wubook backend nor the queue_job runner.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "TestFlatten Co"})
        cls.pricelist_default = cls.env["product.pricelist"].create(
            {"name": "Flatten Default", "company_id": cls.company.id}
        )
        cls.pms_property = cls.env["pms.property"].create(
            {
                "name": "TestFlatten Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist_default.id,
            }
        )
        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "RTC Flatten", "default_code": "RTCF"}
        )
        cls.product_a = cls.env["product.product"].create(
            {"name": "Room A product", "type": "service", "list_price": 100.0}
        )
        cls.product_b = cls.env["product.product"].create(
            {"name": "Room B product", "type": "service", "list_price": 80.0}
        )
        cls.room_type_a = cls.env["pms.room.type"].create(
            {
                "name": "RT-A",
                "default_code": "RTA",
                "class_id": cls.room_type_class.id,
                "product_id": cls.product_a.id,
                "pms_property_ids": [(6, 0, [cls.pms_property.id])],
            }
        )
        cls.room_type_b = cls.env["pms.room.type"].create(
            {
                "name": "RT-B",
                "default_code": "RTB",
                "class_id": cls.room_type_class.id,
                "product_id": cls.product_b.id,
                "pms_property_ids": [(6, 0, [cls.pms_property.id])],
            }
        )
        cls.d0 = date.today() + timedelta(days=10)
        cls.d1 = cls.d0 + timedelta(days=4)  # 5-day window

        cls.pricelist_a = cls.env["product.pricelist"].create(
            {"name": "PL A (daily base)", "company_id": cls.company.id}
        )
        cls._add_fixed_items(
            cls.pricelist_a,
            [
                (cls.product_a, cls.d0, cls.d1, 100.0),
                (cls.product_b, cls.d0, cls.d1, 80.0),
            ],
        )
        cls.pricelist_b = cls.env["product.pricelist"].create(
            {"name": "PL B (+15% over A)", "company_id": cls.company.id}
        )
        cls.pricelist_b.write(
            {
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "3_global",
                            "compute_price": "formula",
                            "base": "pricelist",
                            "base_pricelist_id": cls.pricelist_a.id,
                            "price_discount": -15.0,
                        },
                    )
                ]
            }
        )

    @classmethod
    def _add_fixed_items(cls, pricelist, rows):
        items = []
        for product, dfrom, dto, price in rows:
            items.append(
                (
                    0,
                    0,
                    {
                        "applied_on": "0_product_variant",
                        "compute_price": "fixed",
                        "product_id": product.id,
                        "fixed_price": price,
                        "date_start_consumption": dfrom,
                        "date_end_consumption": dto,
                    },
                )
            )
        pricelist.write({"item_ids": items})

    def test_constraint_requires_derived_item(self):
        bare = self.env["product.pricelist"].create(
            {"name": "Bare", "company_id": self.company.id}
        )
        with self.assertRaises(ValidationError):
            bare.wubook_flatten_to_daily = True

    def test_constraint_passes_with_derived_item(self):
        self.pricelist_b.wubook_flatten_to_daily = True
        self.assertTrue(self.pricelist_b.wubook_flatten_to_daily)

    def test_plan_type_forces_standard_when_flag_set(self):
        # Without the flag, B has a single virtual item -> wubook_plan_type
        # would only be "virtual" if pricelist_type != "daily"; since
        # pricelist_type defaults to "daily" the legacy compute leaves it False.
        self.pricelist_b.wubook_flatten_to_daily = True
        self.assertEqual(self.pricelist_b.wubook_plan_type, "standard")

    def test_get_flatten_parent_pricelists(self):
        parents = self.pricelist_b._get_flatten_parent_pricelists()
        self.assertEqual(parents, self.pricelist_a)

    def test_get_flatten_descendant_pricelists(self):
        self.pricelist_b.wubook_flatten_to_daily = True
        descendants = self.pricelist_a._get_flatten_descendant_pricelists()
        self.assertIn(self.pricelist_b, descendants)
        # A itself is not its own descendant
        self.assertNotIn(self.pricelist_a, descendants)

    def test_get_flatten_descendant_pricelists_multilevel(self):
        pricelist_c = self.env["product.pricelist"].create(
            {"name": "PL C (+10 over B)", "company_id": self.company.id}
        )
        pricelist_c.write(
            {
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "3_global",
                            "compute_price": "formula",
                            "base": "pricelist",
                            "base_pricelist_id": self.pricelist_b.id,
                            "price_surcharge": 10.0,
                        },
                    )
                ]
            }
        )
        pricelist_c.wubook_flatten_to_daily = True
        descendants = self.pricelist_a._get_flatten_descendant_pricelists()
        self.assertIn(pricelist_c, descendants)
        # B is not flagged so it is NOT included
        self.assertNotIn(self.pricelist_b, descendants)

    def test_get_flatten_chain_max_date(self):
        max_date = self.pricelist_b._get_flatten_chain_max_date()
        self.assertEqual(max_date, self.d1)

    def test_get_flatten_chain_max_date_no_parent(self):
        bare = self.env["product.pricelist"].create(
            {"name": "Bare2", "company_id": self.company.id}
        )
        self.assertFalse(bare._get_flatten_chain_max_date())

    def test_compute_flattened_items_two_levels(self):
        room_types = self.room_type_a | self.room_type_b
        items = self.pricelist_b._compute_flattened_items(
            self.pms_property, room_types, self.d0, self.d1
        )
        # 2 room types * 5 days = 10 items
        self.assertEqual(len(items), 10)
        for item in items:
            if item["room_type_id"] == self.room_type_a:
                self.assertAlmostEqual(item["fixed_price"], 115.0, places=2)
            else:
                self.assertAlmostEqual(item["fixed_price"], 92.0, places=2)
            self.assertGreaterEqual(item["date"], self.d0)
            self.assertLessEqual(item["date"], self.d1)

    def test_compute_flattened_items_three_levels(self):
        pricelist_c = self.env["product.pricelist"].create(
            {"name": "PL C (+10 over B)", "company_id": self.company.id}
        )
        pricelist_c.write(
            {
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "3_global",
                            "compute_price": "formula",
                            "base": "pricelist",
                            "base_pricelist_id": self.pricelist_b.id,
                            "price_surcharge": 10.0,
                        },
                    )
                ]
            }
        )
        items = pricelist_c._compute_flattened_items(
            self.pms_property, self.room_type_a, self.d0, self.d0
        )
        self.assertEqual(len(items), 1)
        # A(100) -> B(+15% = 115) -> C(+10 = 125)
        self.assertAlmostEqual(items[0]["fixed_price"], 125.0, places=2)

    def test_compute_flattened_items_falls_back_to_list_price(self):
        # Out-of-window date: A has no item for d_far
        d_far = self.d1 + timedelta(days=30)
        items = self.pricelist_b._compute_flattened_items(
            self.pms_property, self.room_type_a, d_far, d_far
        )
        self.assertEqual(len(items), 1)
        # A has no applicable rule, A returns list_price=100 -> B +15% = 115
        self.assertAlmostEqual(items[0]["fixed_price"], 115.0, places=2)

    def test_compute_flattened_items_empty_window(self):
        items = self.pricelist_b._compute_flattened_items(
            self.pms_property, self.room_type_a, self.d1, self.d0
        )
        self.assertEqual(items, [])

    def test_compute_flattened_items_no_room_types(self):
        items = self.pricelist_b._compute_flattened_items(
            self.pms_property,
            self.env["pms.room.type"].browse(),
            self.d0,
            self.d1,
        )
        self.assertEqual(items, [])


class TestPricelistFlattenWithBackend(TransactionComponentCase):
    """Integration tests for the flatten flow that require a Wubook backend
    and bindings. Adapter XMLRPC calls are mocked.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "TestFlatten Int Co"})
        cls.pricelist_default = cls.env["product.pricelist"].create(
            {"name": "Flatten Int Default", "company_id": cls.company.id}
        )
        cls.pms_property = cls.env["pms.property"].create(
            {
                "name": "TestFlatten Int Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist_default.id,
            }
        )
        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "RTC Flatten Int", "default_code": "RTCFI"}
        )
        cls.product_a = cls.env["product.product"].create(
            {"name": "RT-A int product", "type": "service", "list_price": 100.0}
        )
        cls.room_type_a = cls.env["pms.room.type"].create(
            {
                "name": "RT-A int",
                "default_code": "RTAI",
                "class_id": cls.room_type_class.id,
                "product_id": cls.product_a.id,
                "pms_property_ids": [(6, 0, [cls.pms_property.id])],
            }
        )

        # Backend setup. Many fields on channel.wubook.backend are required,
        # so we provide placeholder values for credentials / external ids.
        backend_type = cls.env["channel.wubook.backend.type"].create(
            {
                "name": "BT-int",
                "model_type_id": cls.env.ref(
                    "connector_pms_wubook.model_channel_wubook_backend_type"
                ).id,
                "direct_channel_type_id": cls.env.ref(
                    "connector_pms_wubook.main_pms_sale_channel_wubook"
                ).id,
                "room_type_class_ids": [
                    (
                        0,
                        0,
                        {
                            "wubook_room_type": "1",
                            "room_type_shortname": "RTCFI",
                        },
                    )
                ],
            }
        )
        payment_method_line = cls.env["account.payment.method.line"].search(
            [], limit=1
        )
        cls.backend = cls.env["channel.wubook.backend"].create(
            {
                "name": "Flatten Int Backend",
                "pms_property_id": cls.pms_property.id,
                "backend_type_id": backend_type.parent_id.id,
                "username": "X",
                "password": "X",
                "property_code": "X",
                "pkey": "X",
                "pricelist_external_id": 1,
                "wubook_payment_method_line_id": payment_method_line.id,
            }
        )
        # Bind room type with a fake external id so payload lookup works.
        cls.room_type_a_binding = cls.env[
            "channel.wubook.pms.room.type"
        ].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": "111",
            }
        )

        # Pricelist hierarchy: A (daily fixed) -> B (+15% formula, flatten)
        cls.d0 = date.today() + timedelta(days=5)
        cls.d1 = cls.d0 + timedelta(days=2)
        cls.pricelist_a = cls.env["product.pricelist"].create(
            {
                "name": "PL A int",
                "company_id": cls.company.id,
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "compute_price": "fixed",
                            "product_id": cls.product_a.id,
                            "fixed_price": 100.0,
                            "date_start_consumption": cls.d0,
                            "date_end_consumption": cls.d1,
                        },
                    )
                ],
            }
        )
        cls.pricelist_b = cls.env["product.pricelist"].create(
            {
                "name": "PL B int",
                "company_id": cls.company.id,
                "wubook_flatten_to_daily": True,
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "3_global",
                            "compute_price": "formula",
                            "base": "pricelist",
                            "base_pricelist_id": cls.pricelist_a.id,
                            "price_discount": -15.0,
                        },
                    )
                ],
            }
        )
        cls.pricelist_b_binding = cls.env[
            "channel.wubook.product.pricelist"
        ].create(
            {
                "odoo_id": cls.pricelist_b.id,
                "backend_id": cls.backend.id,
                "external_id": "5001",
            }
        )

    def test_get_flatten_default_window_caps_by_parent_chain(self):
        self.backend.flatten_window_days = 540
        date_from, date_to = (
            self.pricelist_b_binding._get_flatten_default_window()
        )
        self.assertEqual(date_from, date.today())
        # Capped by parent's last rule date (self.d1)
        self.assertEqual(date_to, self.d1)

    def test_get_flatten_default_window_zero_returns_empty(self):
        self.backend.flatten_window_days = 0
        date_from, date_to = (
            self.pricelist_b_binding._get_flatten_default_window()
        )
        self.assertIsNone(date_from)
        self.assertIsNone(date_to)

    def test_compute_flatten_payload_items_uses_room_type_external_id(self):
        items = self.pricelist_b_binding._compute_flatten_payload_items(
            self.d0, self.d1
        )
        # 1 room type * 3 days = 3 entries
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(item["rid"], 111)
            self.assertAlmostEqual(item["price"], 115.0, places=2)
            self.assertGreaterEqual(item["date"], self.d0)
            self.assertLessEqual(item["date"], self.d1)

    def test_compute_flatten_payload_items_skips_unbound_room_types(self):
        # Create a second room type WITHOUT a backend binding -> skipped
        product_b = self.env["product.product"].create(
            {"name": "RT-B int", "type": "service", "list_price": 80.0}
        )
        self.env["pms.room.type"].create(
            {
                "name": "RT-B int",
                "default_code": "RTBI",
                "class_id": self.room_type_class.id,
                "product_id": product_b.id,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
            }
        )
        items = self.pricelist_b_binding._compute_flatten_payload_items(
            self.d0, self.d0
        )
        # Only the bound room type contributes (3 dates -> 1 here)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["rid"], 111)

    def test_export_flattened_calls_adapter_write(self):
        with mock.patch(
            "odoo.addons.connector_pms_wubook.models.product_pricelist."
            "adapter.ChannelWubookProductPricelistAdapter.write"
        ) as mocked_write:
            self.pricelist_b_binding.export_flattened(
                date_from=self.d0, date_to=self.d1
            )
        mocked_write.assert_called_once()
        args, _kwargs = mocked_write.call_args
        external_id, payload = args[0], args[1]
        self.assertEqual(external_id, 5001)
        self.assertEqual(payload["type"], "standard")
        self.assertEqual(len(payload["items"]), 3)

    def test_export_flattened_caps_dto_by_chain_max(self):
        # Request a wider window than the parent provides; the helper must
        # cap at the parent chain's last dated rule (self.d1).
        d_far = self.d1 + timedelta(days=30)
        with mock.patch(
            "odoo.addons.connector_pms_wubook.models.product_pricelist."
            "adapter.ChannelWubookProductPricelistAdapter.write"
        ) as mocked_write:
            self.pricelist_b_binding.export_flattened(
                date_from=self.d0, date_to=d_far
            )
        args, _kwargs = mocked_write.call_args
        payload = args[1]
        max_date = max(item["date"] for item in payload["items"])
        self.assertEqual(max_date, self.d1)

    def test_export_flattened_falls_back_when_no_external_id(self):
        self.pricelist_b_binding.external_id = False
        with mock.patch.object(
            type(self.pricelist_b_binding), "export_record"
        ) as mocked_export_record:
            self.pricelist_b_binding.export_flattened(
                date_from=self.d0, date_to=self.d1
            )
        mocked_export_record.assert_called_once()

    def test_export_flattened_falls_back_when_flag_unchecked(self):
        # Flag was unchecked between enqueue and execution -> normal flow.
        # trap_jobs absorbs the job that the (newly active) pricelist
        # listener queues when toggling the flag.
        with trap_jobs():
            self.pricelist_b.wubook_flatten_to_daily = False
        with mock.patch.object(
            type(self.pricelist_b_binding), "export_record"
        ) as mocked_export_record:
            self.pricelist_b_binding.export_flattened(
                date_from=self.d0, date_to=self.d1
            )
        mocked_export_record.assert_called_once()

    def test_listener_on_parent_item_change_queues_descendant_export(self):
        # Change an item on the PARENT pricelist A -> listener should queue
        # an export_flattened call on B's binding scoped to the affected
        # room type and date range.
        item = self.pricelist_a.item_ids[:1]
        affected_room_type = item.product_id.room_type_id
        with trap_jobs() as trap:
            item.fixed_price = 120.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_b_binding.export_flattened,
            kwargs={
                "date_from": self.d0,
                "date_to": self.d1,
                "room_type_ids": [affected_room_type.id],
            },
        )

    def test_listener_on_own_item_change_queues_default_window(self):
        # Change an item on the FLATTEN pricelist B itself -> listener
        # should queue an export_flattened call with NO explicit window.
        item = self.pricelist_b.item_ids[:1]
        with trap_jobs() as trap:
            item.price_discount = -20.0
            self.env.cr.precommit.run()
        # The own-pricelist branch enqueues without dates; the cascade
        # branch sees no flatten descendant (B has none).
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_b_binding.export_flattened,
            kwargs={},
        )

    def test_listener_no_descendant_no_call(self):
        # Item on a pricelist that has neither the flag nor flatten
        # descendants -> nothing queued.
        unrelated = self.env["product.pricelist"].create(
            {"name": "PL Unrelated", "company_id": self.company.id}
        )
        with trap_jobs() as trap:
            self.env["product.pricelist.item"].create(
                {
                    "pricelist_id": unrelated.id,
                    "applied_on": "0_product_variant",
                    "compute_price": "fixed",
                    "product_id": self.product_a.id,
                    "fixed_price": 50.0,
                }
            )
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)

    def test_listener_coalesces_massive_change_into_one_job(self):
        # Massive change: many items modified in the same transaction on
        # the PARENT pricelist A. The listener buffers them and enqueues a
        # SINGLE job per affected binding at precommit, covering the
        # union of dates AND the union of affected room types.
        d0 = self.d0
        d_mid = self.d0 + timedelta(days=1)
        d_late = self.d1 + timedelta(days=10)
        self.pricelist_a.write(
            {
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "compute_price": "fixed",
                            "product_id": self.product_a.id,
                            "fixed_price": 105.0,
                            "date_start_consumption": d_mid,
                            "date_end_consumption": d_mid,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "compute_price": "fixed",
                            "product_id": self.product_a.id,
                            "fixed_price": 110.0,
                            "date_start_consumption": d_late,
                            "date_end_consumption": d_late,
                        },
                    ),
                ]
            }
        )
        items = self.pricelist_a.item_ids
        self.assertGreaterEqual(len(items), 3)
        affected_room_type = self.room_type_a
        with trap_jobs() as trap:
            for item in items:
                item.fixed_price = item.fixed_price + 1.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_b_binding.export_flattened,
            kwargs={
                "date_from": d0,
                "date_to": d_late,
                "room_type_ids": [affected_room_type.id],
            },
        )

    def test_listener_own_and_parent_change_uses_default_window(self):
        # Mixing an own-pricelist change (no dates -> default window) and
        # a parent change (with dates) in the same transaction must
        # collapse to ONE job per binding using the default window
        # (because (None, None) subsumes any explicit range).
        with trap_jobs() as trap:
            self.pricelist_b.item_ids[:1].price_discount = -25.0
            self.pricelist_a.item_ids[:1].fixed_price = 150.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_b_binding.export_flattened,
            kwargs={},
        )

    def test_mapper_items_flatten_returns_dicts(self):
        with self.backend.work_on(
            "channel.wubook.product.pricelist"
        ) as work:
            mapper = work.component(usage="export.mapper")
        result = mapper.items_flatten(self.pricelist_b_binding)
        self.assertIsInstance(result, dict)
        self.assertIn("items", result)
        # All synthetic items must have date/price/rid
        for item in result["items"]:
            self.assertIn("date", item)
            self.assertIn("price", item)
            self.assertIn("rid", item)

    def test_mapper_items_flatten_returns_none_when_flag_off(self):
        # Drop the flag and re-fetch the binding to ensure compute cache.
        # trap_jobs absorbs the queue.job that the pricelist listener now
        # enqueues whenever the flag changes.
        with trap_jobs():
            self.pricelist_b.wubook_flatten_to_daily = False
        with self.backend.work_on(
            "channel.wubook.product.pricelist"
        ) as work:
            mapper = work.component(usage="export.mapper")
        result = mapper.items_flatten(self.pricelist_b_binding)
        self.assertIsNone(result)

    def test_listener_scopes_room_type_when_parent_change_touches_one(self):
        """When the parent's bulk change touches only items of room type
        RT-A, the descendant's job must carry ``room_type_ids=[RT-A]`` —
        and the payload computation must skip RT-B entirely.
        """
        # Set up a second bound room type + product
        product_b = self.env["product.product"].create(
            {"name": "RT-B int product", "type": "service", "list_price": 80.0}
        )
        room_type_b = self.env["pms.room.type"].create(
            {
                "name": "RT-B int 2",
                "default_code": "RTB2",
                "class_id": self.room_type_class.id,
                "product_id": product_b.id,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
            }
        )
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": room_type_b.id,
                "backend_id": self.backend.id,
                "external_id": 222,
            }
        )
        # Add an A-item for RT-B so the parent has prices for both rooms.
        # Flush the precommit buffer here so the create's queued export
        # doesn't pollute the trap_jobs assertion below.
        self.pricelist_a.write(
            {
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "compute_price": "fixed",
                            "product_id": product_b.id,
                            "fixed_price": 200.0,
                            "date_start_consumption": self.d0,
                            "date_end_consumption": self.d1,
                        },
                    )
                ]
            }
        )
        self.env.cr.precommit.run()
        # Massive change touches ONLY items for RT-A
        rt_a_items = self.pricelist_a.item_ids.filtered(
            lambda i: i.product_id == self.product_a
        )
        with trap_jobs() as trap:
            for item in rt_a_items:
                item.fixed_price = item.fixed_price + 5.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_b_binding.export_flattened,
            kwargs={
                "date_from": self.d0,
                "date_to": self.d1,
                "room_type_ids": [self.room_type_a.id],
            },
        )

    def test_listener_unions_room_types_when_bulk_touches_multiple(self):
        """Massive change touching items of both RT-A and RT-B in the
        same transaction → single job, scoped to BOTH room types.
        """
        product_b = self.env["product.product"].create(
            {"name": "RT-B u product", "type": "service", "list_price": 80.0}
        )
        room_type_b = self.env["pms.room.type"].create(
            {
                "name": "RT-B u",
                "default_code": "RTBU",
                "class_id": self.room_type_class.id,
                "product_id": product_b.id,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
            }
        )
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": room_type_b.id,
                "backend_id": self.backend.id,
                "external_id": 333,
            }
        )
        self.pricelist_a.write(
            {
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "compute_price": "fixed",
                            "product_id": product_b.id,
                            "fixed_price": 200.0,
                            "date_start_consumption": self.d0,
                            "date_end_consumption": self.d1,
                        },
                    )
                ]
            }
        )
        with trap_jobs() as trap:
            # Modify items of both room types in the same transaction
            for item in self.pricelist_a.item_ids:
                item.fixed_price = item.fixed_price + 1.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        # Both RT ids must be in the room_type_ids scope (sorted)
        expected_ids = sorted([self.room_type_a.id, room_type_b.id])
        trap.assert_enqueued_job(
            self.pricelist_b_binding.export_flattened,
            kwargs={
                "date_from": self.d0,
                "date_to": self.d1,
                "room_type_ids": expected_ids,
            },
        )

    def test_compute_flatten_payload_intersects_with_bound_room_types(self):
        """``_compute_flatten_payload_items(room_type_ids=[...])`` must
        intersect with bound room types. Passing an unbound id yields an
        empty payload.
        """
        unbound_product = self.env["product.product"].create(
            {"name": "Unbound RT product", "type": "service"}
        )
        unbound_rt = self.env["pms.room.type"].create(
            {
                "name": "Unbound RT",
                "default_code": "UNBO",
                "class_id": self.room_type_class.id,
                "product_id": unbound_product.id,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
            }
        )
        items = self.pricelist_b_binding._compute_flatten_payload_items(
            self.d0, self.d0, room_type_ids=[unbound_rt.id]
        )
        self.assertEqual(items, [])
