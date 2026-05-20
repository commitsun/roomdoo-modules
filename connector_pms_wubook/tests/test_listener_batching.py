# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Tests for the per-pricelist / per-plan staging refactor of the
``pms.availability.plan.rule`` and ``product.pricelist.item`` listeners.

The previous implementation iterated ``channel_wubook_bind_ids`` /
``_get_flatten_descendant_pricelists`` once per touched record. These
tests pin the new behavior — the same set of queue jobs but with the
binding/descendant resolution happening exactly once per pricelist (or
per ``(plan, property)`` pair) at precommit.
"""

from datetime import date, timedelta

from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.queue_job.tests.common import trap_jobs

from .test_master_sync import _make_backend_environment


def _make_second_backend(cls, name_suffix="2"):
    """Add a second Wubook backend + property + room-type binding sharing
    the same pricelist / plan as the first one. Useful for scope tests.
    """
    cls.pms_property_b = cls.env["pms.property"].create(
        {
            "name": f"Master Sync Property {name_suffix}",
            "company_id": cls.company.id,
            "default_pricelist_id": cls.pricelist_default.id,
        }
    )
    payment_method_line = cls.env["account.payment.method.line"].search([], limit=1)
    cls.backend_b = cls.env["channel.wubook.backend"].create(
        {
            "name": f"Master Sync Backend {name_suffix}",
            "pms_property_id": cls.pms_property_b.id,
            "backend_type_id": cls.backend.backend_type_id.id,
            "username": "X",
            "password": "X",
            "property_code": "X",
            "pkey": "X",
            "pricelist_external_id": 2,
            "wubook_payment_method_line_id": payment_method_line.id,
        }
    )


@tagged("post_install", "-at_install")
class TestPricelistItemPerPricelistBatching(TransactionComponentCase):
    """The pricelist-item listener stages contributions per-pricelist and
    flushes once at precommit. The number of queue jobs emitted must be
    independent of the number of items touched in the transaction.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.room_type_a_binding = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        cls.pricelist = cls.env["product.pricelist"].create(
            {"name": "PL", "company_id": cls.company.id}
        )
        cls.pricelist_binding = cls.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": cls.pricelist.id,
                "backend_id": cls.backend.id,
                "external_id": 7777,
            }
        )

    def _make_items(self, n, start_offset=1):
        d0 = date.today() + timedelta(days=start_offset)
        return self.env["product.pricelist.item"].create(
            [
                {
                    "pricelist_id": self.pricelist.id,
                    "applied_on": "0_product_variant",
                    "compute_price": "fixed",
                    "product_id": self.product_a.id,
                    "fixed_price": 10.0 + i,
                    "date_start_consumption": d0 + timedelta(days=i),
                    "date_end_consumption": d0 + timedelta(days=i),
                }
                for i in range(n)
            ]
        )

    def test_500_create_in_one_transaction_emit_one_job(self):
        """500 items created in a single transaction collapse to ONE
        ``export_record`` job for the pricelist binding — independent of
        N. This is the core invariant the refactor preserves.
        """
        with trap_jobs() as trap:
            self._make_items(500)
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )

    def test_500_writes_in_one_transaction_emit_one_job(self):
        items = self._make_items(500)
        self.env.cr.precommit.run()
        with trap_jobs() as trap:
            for i, it in enumerate(items):
                it.fixed_price = 1000.0 + i
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)

    def test_unlink_emits_one_job(self):
        items = self._make_items(20)
        self.env.cr.precommit.run()
        with trap_jobs() as trap:
            items.unlink()
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)

    def test_separate_transactions_each_emit_one_job(self):
        """Two separate transactions each flush their own buffer
        independently. The identity_key is set on the queue side so
        bursts across transactions still collapse there, but tests use
        ``trap_jobs`` which captures pre-enqueue and therefore sees
        both.
        """
        with trap_jobs() as trap1:
            self._make_items(5)
            self.env.cr.precommit.run()
        with trap_jobs() as trap2:
            self._make_items(5, start_offset=100)
            self.env.cr.precommit.run()
        trap1.assert_jobs_count(1)
        trap2.assert_jobs_count(1)


@tagged("post_install", "-at_install")
class TestPricelistItemPropertyScope(TransactionComponentCase):
    """Property-scoped items must only enqueue jobs on bindings whose
    backend covers one of their declared ``pms_property_ids``. Items
    with empty ``pms_property_ids`` apply globally, and the buffer
    upgrades the aggregated scope to ``global`` if any pending item is
    global.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        _make_second_backend(cls)
        # Bind room type to both backends.
        cls.room_type_a_binding = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        cls.room_type_a_binding_b = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend_b.id,
                "external_id": 222,
            }
        )
        cls.pricelist = cls.env["product.pricelist"].create(
            {"name": "PL scope", "company_id": cls.company.id}
        )
        cls.pricelist_binding_a = cls.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": cls.pricelist.id,
                "backend_id": cls.backend.id,
                "external_id": 7777,
            }
        )
        cls.pricelist_binding_b = cls.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": cls.pricelist.id,
                "backend_id": cls.backend_b.id,
                "external_id": 8888,
            }
        )

    def _add_item(self, day_offset, property_ids=None):
        vals = {
            "pricelist_id": self.pricelist.id,
            "applied_on": "0_product_variant",
            "compute_price": "fixed",
            "product_id": self.product_a.id,
            "fixed_price": 10.0 + day_offset,
            "date_start_consumption": date.today() + timedelta(days=day_offset),
            "date_end_consumption": date.today() + timedelta(days=day_offset),
        }
        if property_ids is not None:
            vals["pms_property_ids"] = [(6, 0, property_ids)]
        return self.env["product.pricelist.item"].create(vals)

    def test_item_scoped_to_property_a_targets_only_binding_a(self):
        with trap_jobs() as trap:
            self._add_item(1, property_ids=[self.pms_property.id])
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding_a.export_record,
            args=(self.backend, self.pricelist),
        )

    def test_item_scoped_to_property_b_targets_only_binding_b(self):
        with trap_jobs() as trap:
            self._add_item(1, property_ids=[self.pms_property_b.id])
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding_b.export_record,
            args=(self.backend_b, self.pricelist),
        )

    def test_items_scoped_to_disjoint_properties_target_both_bindings(self):
        with trap_jobs() as trap:
            self._add_item(1, property_ids=[self.pms_property.id])
            self._add_item(2, property_ids=[self.pms_property_b.id])
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)

    def test_global_item_targets_both_bindings(self):
        """``pms_property_ids = []`` means global; both bindings get a
        job."""
        with trap_jobs() as trap:
            self._add_item(1, property_ids=[])
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)

    def test_mix_global_and_specific_targets_both_bindings(self):
        """One global item in the buffer upgrades the aggregated scope
        to ``global`` and both bindings get a job — even if the other
        item was scoped to property A only."""
        with trap_jobs() as trap:
            self._add_item(1, property_ids=[self.pms_property.id])
            self._add_item(2, property_ids=[])
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)


@tagged("post_install", "-at_install")
class TestPricelistItemConnectorNoExport(TransactionComponentCase):
    """The ``connector_no_export`` context flag must short-circuit the
    listener entirely — no buffer staging, no precommit work.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.room_type_a_binding = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        cls.pricelist = cls.env["product.pricelist"].create(
            {"name": "PL ne", "company_id": cls.company.id}
        )
        cls.pricelist_binding = cls.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": cls.pricelist.id,
                "backend_id": cls.backend.id,
                "external_id": 7777,
            }
        )

    def test_no_export_create_does_not_enqueue(self):
        with trap_jobs() as trap:
            self.env["product.pricelist.item"].with_context(
                connector_no_export=True
            ).create(
                {
                    "pricelist_id": self.pricelist.id,
                    "applied_on": "0_product_variant",
                    "compute_price": "fixed",
                    "product_id": self.product_a.id,
                    "fixed_price": 50.0,
                    "date_start_consumption": date.today() + timedelta(days=1),
                    "date_end_consumption": date.today() + timedelta(days=1),
                }
            )
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)


@tagged("post_install", "-at_install")
class TestPlanRulePerPlanPropertyBatching(TransactionComponentCase):
    """The plan-rule listener stages contributions per (plan, property)
    pair and resolves bindings once at precommit. The job count is
    independent of the number of rules touched.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.room_type_a_binding = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        cls.plan = cls.env["pms.availability.plan"].create(
            {"name": "OTA Plan", "company_id": cls.company.id}
        )
        cls.plan_binding = cls.env["channel.wubook.pms.availability.plan"].create(
            {
                "odoo_id": cls.plan.id,
                "backend_id": cls.backend.id,
                "external_id": 9999,
            }
        )

    def _make_rules(self, n, start_offset=1):
        d0 = date.today() + timedelta(days=start_offset)
        return self.env["pms.availability.plan.rule"].create(
            [
                {
                    "availability_plan_id": self.plan.id,
                    "pms_property_id": self.pms_property.id,
                    "room_type_id": self.room_type_a.id,
                    "date": d0 + timedelta(days=i),
                    "min_stay": i,
                }
                for i in range(n)
            ]
        )

    def test_500_rule_create_collapses_to_one_plan_job(self):
        with trap_jobs() as trap:
            self._make_rules(500)
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.plan_binding.export_record,
            args=(self.backend, self.plan),
        )

    def test_500_rule_business_field_writes_collapse_to_one_plan_job(self):
        rules = self._make_rules(500)
        self.env.cr.precommit.run()
        with trap_jobs() as trap:
            for i, r in enumerate(rules):
                r.min_stay = 100 + i
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)

    def test_irrelevant_field_write_does_not_enqueue(self):
        """Writes to fields outside ``_RULE_PLAN_FIELDS`` /
        ``_RULE_AVAIL_FIELDS`` must not stage anything.
        """
        rules = self._make_rules(3)
        self.env.cr.precommit.run()
        with trap_jobs() as trap:
            # ``write({})`` is the cleanest non-relevant write: no
            # fields in the changed set.
            rules.write({})
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)

    def test_unlink_emits_one_job(self):
        rules = self._make_rules(10)
        self.env.cr.precommit.run()
        with trap_jobs() as trap:
            rules.unlink()
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)


@tagged("post_install", "-at_install")
class TestPlanRulePropertyScope(TransactionComponentCase):
    """A plan shared across properties must only enqueue jobs on
    bindings whose backend covers a touched property.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        _make_second_backend(cls)
        cls.room_type_a_binding = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        cls.room_type_a_binding_b = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend_b.id,
                "external_id": 222,
            }
        )
        cls.plan = cls.env["pms.availability.plan"].create(
            {"name": "Shared Plan", "company_id": cls.company.id}
        )
        cls.plan_binding_a = cls.env["channel.wubook.pms.availability.plan"].create(
            {
                "odoo_id": cls.plan.id,
                "backend_id": cls.backend.id,
                "external_id": 9001,
            }
        )
        cls.plan_binding_b = cls.env["channel.wubook.pms.availability.plan"].create(
            {
                "odoo_id": cls.plan.id,
                "backend_id": cls.backend_b.id,
                "external_id": 9002,
            }
        )

    def _make_rule(self, pms_property, day_offset=1):
        return self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan.id,
                "pms_property_id": pms_property.id,
                "room_type_id": self.room_type_a.id,
                "date": date.today() + timedelta(days=day_offset),
                "min_stay": 2,
            }
        )

    def test_rule_on_property_a_targets_only_binding_a(self):
        with trap_jobs() as trap:
            self._make_rule(self.pms_property)
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.plan_binding_a.export_record,
            args=(self.backend, self.plan),
        )

    def test_rules_in_both_properties_target_both_bindings(self):
        with trap_jobs() as trap:
            self._make_rule(self.pms_property, day_offset=1)
            self._make_rule(self.pms_property_b, day_offset=2)
            self.env.cr.precommit.run()
        trap.assert_jobs_count(2)
