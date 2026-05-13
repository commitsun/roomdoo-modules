# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from datetime import date, timedelta
from unittest import mock

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.queue_job.tests.common import trap_jobs


def _make_backend_environment(cls):
    """Shared scaffolding: company, property, room type class, room type,
    Wubook backend, and a backend type with one room type class entry.
    Sets attributes on ``cls`` so tests can use them.
    """
    cls.company = cls.env["res.company"].create({"name": "Master Sync Co"})
    cls.pricelist_default = cls.env["product.pricelist"].create(
        {"name": "Master Sync Default", "company_id": cls.company.id}
    )
    cls.pms_property = cls.env["pms.property"].create(
        {
            "name": "Master Sync Property",
            "company_id": cls.company.id,
            "default_pricelist_id": cls.pricelist_default.id,
        }
    )
    cls.room_type_class = cls.env["pms.room.type.class"].create(
        {"name": "RTC MS", "default_code": "RTCMS"}
    )
    cls.product_a = cls.env["product.product"].create(
        {"name": "RT-MS product", "type": "service", "list_price": 100.0}
    )
    cls.room_type_a = cls.env["pms.room.type"].create(
        {
            "name": "RT-MS",
            "default_code": "RTMS",
            "class_id": cls.room_type_class.id,
            "product_id": cls.product_a.id,
            "pms_property_ids": [(6, 0, [cls.pms_property.id])],
        }
    )

    backend_type = cls.env["channel.wubook.backend.type"].create(
        {
            "name": "BT-MS",
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
                        "room_type_shortname": "RTCMS",
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
            "name": "Master Sync Backend",
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


class TestWubookConnectMixin(TransactionComponentCase):
    """Connection state computed field + action helpers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)

    def test_connection_state_disconnected_by_default(self):
        self.assertEqual(
            self.room_type_a.wubook_connection_state, "disconnected"
        )

    def test_connection_state_flips_to_connected_after_binding(self):
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 999,
            }
        )
        self.room_type_a.invalidate_recordset()
        self.assertEqual(
            self.room_type_a.wubook_connection_state, "connected"
        )

    def test_action_open_wizard_creates_pre_saved_wizard(self):
        action = self.room_type_a.action_open_wubook_connect_wizard()
        self.assertEqual(action["res_model"], "channel.wubook.connect.wizard")
        self.assertTrue(action.get("res_id"))
        wizard = self.env["channel.wubook.connect.wizard"].browse(
            action["res_id"]
        )
        self.assertEqual(wizard.res_model, "pms.room.type")
        self.assertEqual(wizard.res_id, self.room_type_a.id)
        self.assertEqual(wizard.backend_id, self.backend)

    def test_action_view_connection_requires_binding(self):
        with self.assertRaises(UserError):
            self.room_type_a.action_view_wubook_connection()

    def test_action_view_connection_opens_binding_form(self):
        binding = self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 555,
            }
        )
        self.room_type_a.invalidate_recordset()
        action = self.room_type_a.action_view_wubook_connection()
        self.assertEqual(action["res_model"], "channel.wubook.pms.room.type")
        self.assertEqual(action["res_id"], binding.id)


class TestWubookConnectWizard(TransactionComponentCase):
    """Wizard end-to-end: existing/manual/new modes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)

    def _open_wizard(self, record, mode="existing"):
        return self.env["channel.wubook.connect.wizard"].create(
            {
                "res_model": record._name,
                "res_id": record.id,
                "backend_id": self.backend.id,
                "mode": mode,
            }
        )

    def test_binding_model_resolved_from_res_model(self):
        wiz = self._open_wizard(self.room_type_a)
        self.assertEqual(
            wiz.binding_model, "channel.wubook.pms.room.type"
        )

    def test_manual_mode_creates_binding(self):
        wiz = self._open_wizard(self.room_type_a, mode="manual")
        wiz.manual_external_id = 4242
        with trap_jobs() as trap:
            wiz.action_connect()
        # No job: manual connect must not trigger an export
        trap.assert_jobs_count(0)
        binding = self.env["channel.wubook.pms.room.type"].search(
            [
                ("odoo_id", "=", self.room_type_a.id),
                ("backend_id", "=", self.backend.id),
            ]
        )
        self.assertEqual(len(binding), 1)
        self.assertEqual(binding.external_id, 4242)
        # Marked as already synced so the listener does not re-push
        self.assertTrue(binding.sync_date_export)

    def test_manual_mode_requires_external_id(self):
        wiz = self._open_wizard(self.room_type_a, mode="manual")
        with self.assertRaises(UserError):
            wiz.action_connect()

    def test_existing_mode_requires_selection(self):
        wiz = self._open_wizard(self.room_type_a, mode="existing")
        with self.assertRaises(UserError):
            wiz.action_connect()

    def test_existing_mode_creates_binding_from_candidate(self):
        wiz = self._open_wizard(self.room_type_a, mode="existing")
        candidate = self.env[
            "channel.wubook.connect.wizard.candidate"
        ].create(
            {
                "wizard_id": wiz.id,
                "external_id": 314,
                "name": "Some WuBook room [#314]",
            }
        )
        wiz.selected_candidate_id = candidate
        wiz.action_connect()
        binding = self.env["channel.wubook.pms.room.type"].search(
            [
                ("odoo_id", "=", self.room_type_a.id),
                ("backend_id", "=", self.backend.id),
            ]
        )
        self.assertEqual(binding.external_id, 314)

    def test_double_connection_refused(self):
        # First connection
        wiz1 = self._open_wizard(self.room_type_a, mode="manual")
        wiz1.manual_external_id = 1001
        wiz1.action_connect()
        # Second attempt
        wiz2 = self._open_wizard(self.room_type_a, mode="manual")
        wiz2.manual_external_id = 1002
        with self.assertRaises(UserError):
            wiz2.action_connect()

    def test_load_candidates_excludes_already_bound(self):
        """A WuBook record already mapped by another Odoo record on the
        same backend must NOT appear as a candidate (and therefore not be
        selectable in the Selection dropdown).
        """
        # Pre-bind external_id=7777 to a first room type
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 7777,
            }
        )
        # Open wizard on a DIFFERENT room type and ask for candidates
        other_product = self.env["product.product"].create(
            {"name": "Other RT product", "type": "service"}
        )
        other_rt = self.env["pms.room.type"].create(
            {
                "name": "RT-MS-2",
                "default_code": "RTMS2",
                "class_id": self.room_type_class.id,
                "product_id": other_product.id,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
            }
        )
        wiz = self._open_wizard(other_rt, mode="existing")
        with mock.patch(
            "odoo.addons.connector_pms_wubook.wizards.wizard_connect."
            "ChannelWubookConnectWizard._fetch_external_records",
            return_value=[
                {"id": 7777, "name": "Already bound"},
                {"id": 8888, "name": "Free"},
            ],
        ):
            wiz.reload_candidates()
        external_ids = wiz.candidate_ids.mapped("external_id")
        self.assertIn(8888, external_ids)
        self.assertNotIn(7777, external_ids)

    def test_new_mode_creates_empty_binding_then_exports(self):
        wiz = self._open_wizard(self.room_type_a, mode="new")
        binding_model = self.env["channel.wubook.pms.room.type"]
        with mock.patch.object(
            type(binding_model), "export_record"
        ) as mocked_export:
            wiz.action_connect()
        mocked_export.assert_called_once()
        args, _kw = mocked_export.call_args
        self.assertEqual(args[0], self.backend)
        self.assertEqual(args[1], self.room_type_a)
        # The empty binding must already exist so the exporter can find it
        binding = binding_model.search(
            [
                ("odoo_id", "=", self.room_type_a.id),
                ("backend_id", "=", self.backend.id),
            ]
        )
        self.assertEqual(len(binding), 1)
        self.assertFalse(binding.external_id)


class TestMasterListeners(TransactionComponentCase):
    """Listeners post-binding for room types / pricelists / plans."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)

    def test_room_type_no_binding_no_job(self):
        with trap_jobs() as trap:
            self.room_type_a.list_price = 200.0
        trap.assert_jobs_count(0)

    def test_room_type_with_binding_enqueues_export(self):
        binding = self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 111,
            }
        )
        with trap_jobs() as trap:
            self.room_type_a.list_price = 200.0
        trap.assert_jobs_count(1)
        # The base listener enqueues export_record(backend, odoo_record)
        trap.assert_enqueued_job(
            binding.export_record,
            args=(self.backend, self.room_type_a),
        )

    def test_pricelist_with_binding_enqueues_export_on_metadata_change(self):
        pricelist = self.env["product.pricelist"].create(
            {"name": "Test PL listener", "company_id": self.company.id}
        )
        binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": pricelist.id,
                "backend_id": self.backend.id,
                "external_id": 4040,
            }
        )
        with trap_jobs() as trap:
            pricelist.name = "Renamed PL"
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            binding.export_record,
            args=(self.backend, pricelist),
        )

    def test_plan_with_binding_enqueues_export_on_name_change(self):
        plan = self.env["pms.availability.plan"].create(
            {"name": "Test Plan listener"}
        )
        binding = self.env["channel.wubook.pms.availability.plan"].create(
            {
                "odoo_id": plan.id,
                "backend_id": self.backend.id,
                "external_id": 5050,
            }
        )
        with trap_jobs() as trap:
            plan.name = "Renamed Plan"
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            binding.export_record,
            args=(self.backend, plan),
        )

    def test_pricelist_item_write_triggers_only_item_listener(self):
        """Writing items via ``pricelist.write({"item_ids": [...]})`` must
        trigger the **item** listener (which enqueues one ``export_record``
        per pricelist binding via the regular-pricelist buffer) but NOT
        the pricelist-level listener that would emit a redundant
        ``update_plan_name``. The pricelist-level listener filters by
        relevant fields (``name``) and ignores ``item_ids`` writes.
        """
        pricelist = self.env["product.pricelist"].create(
            {"name": "Test PL no-spurious", "company_id": self.company.id}
        )
        binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": pricelist.id,
                "backend_id": self.backend.id,
                "external_id": 6060,
            }
        )
        with trap_jobs() as trap:
            pricelist.write(
                {
                    "item_ids": [
                        (
                            0,
                            0,
                            {
                                "applied_on": "0_product_variant",
                                "compute_price": "fixed",
                                "product_id": self.product_a.id,
                                "fixed_price": 10.0,
                                "date_start_consumption": date.today()
                                + timedelta(days=1),
                                "date_end_consumption": date.today()
                                + timedelta(days=1),
                            },
                        )
                    ]
                }
            )
            self.env.cr.precommit.run()
        # Exactly ONE job from the item listener (regular-pricelist
        # buffer). No additional job from the pricelist-level listener.
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            binding.export_record,
            args=(self.backend, pricelist),
        )

    def test_room_type_irrelevant_field_write_does_not_trigger(self):
        """Writing a field that the Wubook room type mapper doesn't
        consume (e.g. ``note``) must not generate a job.
        """
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 222,
            }
        )
        with trap_jobs() as trap:
            self.room_type_a.write({"description_sale": "Some note"})
        trap.assert_jobs_count(0)


class TestPlanRuleCoalescing(TransactionComponentCase):
    """Massive rule changes collapse to one job per plan binding."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.room_type_a_binding = cls.env[
            "channel.wubook.pms.room.type"
        ].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        cls.plan = cls.env["pms.availability.plan"].create(
            {"name": "Coalescing Plan"}
        )
        cls.plan_binding = cls.env[
            "channel.wubook.pms.availability.plan"
        ].create(
            {
                "odoo_id": cls.plan.id,
                "backend_id": cls.backend.id,
                "external_id": 6060,
            }
        )

    def _create_rules(self, n):
        d0 = date.today() + timedelta(days=5)
        rule_vals = []
        for i in range(n):
            rule_vals.append(
                {
                    "availability_plan_id": self.plan.id,
                    "room_type_id": self.room_type_a.id,
                    "date": d0 + timedelta(days=i),
                    "quota": 5,
                    "pms_property_id": self.pms_property.id,
                }
            )
        # First create them with the buffer trap closed (so we don't count
        # the creation jobs here).
        return self.env["pms.availability.plan.rule"].create(rule_vals)

    def test_massive_rule_change_one_job_per_plan_binding(self):
        rules = self._create_rules(20)
        self.env.cr.precommit.run()  # flush creation buffer

        with trap_jobs() as trap:
            for r in rules:
                r.quota = r.quota + 1
            self.env.cr.precommit.run()
        # Despite 20 rule writes, only ONE job is enqueued for the plan binding
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.plan_binding.export_record,
            args=(self.backend, self.plan),
        )

    def test_rule_without_plan_binding_no_job(self):
        bare_plan = self.env["pms.availability.plan"].create(
            {"name": "Bare Plan no binding"}
        )
        with trap_jobs() as trap:
            self.env["pms.availability.plan.rule"].create(
                {
                    "availability_plan_id": bare_plan.id,
                    "room_type_id": self.room_type_a.id,
                    "date": date.today() + timedelta(days=1),
                    "quota": 1,
                    "pms_property_id": self.pms_property.id,
                }
            )
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)


class TestRegularPricelistItemListener(TransactionComponentCase):
    """Item changes on a regular (non-flatten) connected pricelist must
    enqueue ONE ``export_record`` job per affected binding via
    transactional coalescence — replacing the legacy
    ``_scheduler_export_pricelist_items`` cron.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.room_type_a_binding = cls.env[
            "channel.wubook.pms.room.type"
        ].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        cls.pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Reg PL",
                "company_id": cls.company.id,
            }
        )
        cls.pricelist_binding = cls.env[
            "channel.wubook.product.pricelist"
        ].create(
            {
                "odoo_id": cls.pricelist.id,
                "backend_id": cls.backend.id,
                "external_id": 7777,
            }
        )

    def _add_items(self, n, start_offset=1):
        d0 = date.today() + timedelta(days=start_offset)
        return self.env["product.pricelist.item"].create([
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
        ])

    def test_single_item_write_enqueues_one_export_record(self):
        items = self._add_items(1)
        self.env.cr.precommit.run()  # flush creation buffer
        with trap_jobs() as trap:
            items.fixed_price = 99.0
            self.env.cr.precommit.run()
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )

    def test_massive_item_change_coalesces_to_one_job(self):
        items = self._add_items(50)
        self.env.cr.precommit.run()
        with trap_jobs() as trap:
            for i, it in enumerate(items):
                it.fixed_price = 100.0 + i
            self.env.cr.precommit.run()
        # 50 item writes → still ONE job for the pricelist binding
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )

    def test_unconnected_pricelist_does_not_enqueue(self):
        other_pl = self.env["product.pricelist"].create(
            {"name": "Unconnected PL", "company_id": self.company.id}
        )
        with trap_jobs() as trap:
            self.env["product.pricelist.item"].create({
                "pricelist_id": other_pl.id,
                "applied_on": "0_product_variant",
                "compute_price": "fixed",
                "product_id": self.product_a.id,
                "fixed_price": 50.0,
                "date_start_consumption": date.today() + timedelta(days=1),
                "date_end_consumption": date.today() + timedelta(days=1),
            })
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)

    def test_binding_without_external_id_does_not_enqueue(self):
        # Empty binding (wizard's pre-create stage) must not generate jobs
        # until external_id is set.
        bare_pl = self.env["product.pricelist"].create(
            {"name": "Bare PL", "company_id": self.company.id}
        )
        self.env["channel.wubook.product.pricelist"].create({
            "odoo_id": bare_pl.id,
            "backend_id": self.backend.id,
            # no external_id
        })
        with trap_jobs() as trap:
            self.env["product.pricelist.item"].create({
                "pricelist_id": bare_pl.id,
                "applied_on": "0_product_variant",
                "compute_price": "fixed",
                "product_id": self.product_a.id,
                "fixed_price": 12.0,
                "date_start_consumption": date.today() + timedelta(days=1),
                "date_end_consumption": date.today() + timedelta(days=1),
            })
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)


class TestParentWithFlattenDescendantBothBuffers(TransactionComponentCase):
    """When an item change affects a parent pricelist that is itself
    connected as a regular daily pricelist AND has a flatten descendant,
    BOTH buffers fire: one regular export for the parent, one flatten
    export for the descendant. Single transaction → exactly 2 jobs.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.room_type_a_binding = cls.env[
            "channel.wubook.pms.room.type"
        ].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        # Parent regular daily pricelist + binding
        cls.parent = cls.env["product.pricelist"].create(
            {"name": "Parent", "company_id": cls.company.id}
        )
        cls.parent_binding = cls.env[
            "channel.wubook.product.pricelist"
        ].create(
            {
                "odoo_id": cls.parent.id,
                "backend_id": cls.backend.id,
                "external_id": 8001,
            }
        )
        # Flatten descendant + binding
        cls.flat = cls.env["product.pricelist"].create(
            {
                "name": "Flat",
                "company_id": cls.company.id,
                "wubook_flatten_to_daily": True,
                "item_ids": [
                    (0, 0, {
                        "applied_on": "3_global",
                        "compute_price": "formula",
                        "base": "pricelist",
                        "base_pricelist_id": cls.parent.id,
                        "price_discount": -10.0,
                    })
                ],
            }
        )
        cls.flat_binding = cls.env[
            "channel.wubook.product.pricelist"
        ].create(
            {
                "odoo_id": cls.flat.id,
                "backend_id": cls.backend.id,
                "external_id": 8002,
            }
        )
        cls.d = date.today() + timedelta(days=3)

    def test_parent_item_change_fires_both_buffers(self):
        item = self.env["product.pricelist.item"].create({
            "pricelist_id": self.parent.id,
            "applied_on": "0_product_variant",
            "compute_price": "fixed",
            "product_id": self.product_a.id,
            "fixed_price": 50.0,
            "date_start_consumption": self.d,
            "date_end_consumption": self.d,
        })
        self.env.cr.precommit.run()
        with trap_jobs() as trap:
            item.fixed_price = 75.0
            self.env.cr.precommit.run()
        # One regular export for the parent + one flatten export for the
        # descendant.
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.parent_binding.export_record,
            args=(self.backend, self.parent),
        )
        trap.assert_enqueued_job(
            self.flat_binding.export_flattened,
            kwargs={
                "date_from": self.d,
                "date_to": self.d,
                "room_type_ids": [self.room_type_a.id],
            },
        )


class TestExportDependencies(TransactionComponentCase):
    """`_export_dependencies()` walks referenced room types / parents."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)

    def _make_pricelist_with_item(self):
        d0 = date.today() + timedelta(days=2)
        return self.env["product.pricelist"].create(
            {
                "name": "PL with room type item",
                "company_id": self.company.id,
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "compute_price": "fixed",
                            "product_id": self.product_a.id,
                            "fixed_price": 50.0,
                            "date_start_consumption": d0,
                            "date_end_consumption": d0,
                        },
                    )
                ],
            }
        )

    def _build_pricelist_exporter(self, pricelist):
        with self.backend.work_on(
            "channel.wubook.product.pricelist"
        ) as work:
            exporter = work.component(usage="direct.record.exporter")
        binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": pricelist.id,
                "backend_id": self.backend.id,
            }
        )
        exporter.binding = binding
        return exporter

    def test_pricelist_exporter_walks_only_bound_room_types(self):
        """When the room type referenced by an item is already bound on
        this backend, the cascade re-exports it.
        """
        pricelist = self._make_pricelist_with_item()
        # Pre-bind the referenced room type so the cascade picks it up
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 9999,
            }
        )
        exporter = self._build_pricelist_exporter(pricelist)
        with mock.patch.object(
            exporter, "_export_dependency"
        ) as mocked_dep:
            exporter._export_dependencies()
        called_models = [c.args[1] for c in mocked_dep.call_args_list]
        self.assertIn("channel.wubook.pms.room.type", called_models)

    def test_pricelist_exporter_skips_unbound_room_types(self):
        """An unconnected room type must NOT be auto-created from a
        cascade. The user is expected to connect it explicitly first.
        """
        pricelist = self._make_pricelist_with_item()
        exporter = self._build_pricelist_exporter(pricelist)
        with mock.patch.object(
            exporter, "_export_dependency"
        ) as mocked_dep:
            exporter._export_dependencies()
        mocked_dep.assert_not_called()

    def test_pricelist_exporter_skips_items_of_other_property(self):
        """Items restricted to a different property must NOT trigger any
        dependency export on this backend's cascade.
        """
        other_property = self.env["pms.property"].create(
            {
                "name": "Other Property",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist_default.id,
            }
        )
        # Pre-bind the room type so it would be a candidate
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 9999,
            }
        )
        d0 = date.today() + timedelta(days=2)
        pricelist = self.env["product.pricelist"].create(
            {
                "name": "PL only for other property",
                "company_id": self.company.id,
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "compute_price": "fixed",
                            "product_id": self.product_a.id,
                            "fixed_price": 50.0,
                            "date_start_consumption": d0,
                            "date_end_consumption": d0,
                            "pms_property_ids": [(6, 0, [other_property.id])],
                        },
                    )
                ],
            }
        )
        exporter = self._build_pricelist_exporter(pricelist)
        with mock.patch.object(
            exporter, "_export_dependency"
        ) as mocked_dep:
            exporter._export_dependencies()
        mocked_dep.assert_not_called()


class TestWubookDateValid(TransactionComponentCase):
    """Both bounds of ``wubook_date_valid``: max 2 days back, max ~2 years
    ahead. Items / rules outside that window must be filtered out by the
    mapper's ``skip_item``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.pricelist = cls.env["product.pricelist"].create(
            {"name": "Date valid PL", "company_id": cls.company.id}
        )
        cls.plan = cls.env["pms.availability.plan"].create(
            {"name": "Date valid plan"}
        )

    def _make_item(self, dt):
        return self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "compute_price": "fixed",
                "product_id": self.product_a.id,
                "fixed_price": 10.0,
                "date_start_consumption": dt,
                "date_end_consumption": dt,
            }
        )

    def test_item_today_is_valid(self):
        item = self._make_item(date.today())
        self.assertTrue(item.wubook_date_valid())

    def test_item_one_day_back_is_valid(self):
        item = self._make_item(date.today() - timedelta(days=1))
        self.assertTrue(item.wubook_date_valid())

    def test_item_five_days_back_is_invalid(self):
        item = self._make_item(date.today() - timedelta(days=5))
        self.assertFalse(item.wubook_date_valid())

    def test_item_one_year_ahead_is_valid(self):
        item = self._make_item(date.today() + timedelta(days=365))
        self.assertTrue(item.wubook_date_valid())

    def test_item_two_years_ahead_is_valid(self):
        item = self._make_item(date.today() + timedelta(days=730))
        self.assertTrue(item.wubook_date_valid())

    def test_item_beyond_two_years_is_invalid(self):
        item = self._make_item(date.today() + timedelta(days=731))
        self.assertFalse(item.wubook_date_valid())

    def test_rule_beyond_two_years_is_invalid(self):
        rule = self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan.id,
                "room_type_id": self.room_type_a.id,
                "date": date.today() + timedelta(days=900),
                "pms_property_id": self.pms_property.id,
            }
        )
        self.assertFalse(rule.wubook_date_valid())

    def test_rule_within_window_is_valid(self):
        rule = self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan.id,
                "room_type_id": self.room_type_a.id,
                "date": date.today() + timedelta(days=400),
                "pms_property_id": self.pms_property.id,
            }
        )
        self.assertTrue(rule.wubook_date_valid())


class TestFlattenWindowCap(TransactionComponentCase):
    """The flatten default window must never exceed Wubook's 2-year ceiling
    even if ``flatten_window_days`` on the backend is configured higher.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        pricelist_a = cls.env["product.pricelist"].create(
            {"name": "FW A", "company_id": cls.company.id}
        )
        pricelist_b = cls.env["product.pricelist"].create(
            {
                "name": "FW B",
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
                            "base_pricelist_id": pricelist_a.id,
                            "price_discount": -10.0,
                        },
                    )
                ],
            }
        )
        cls.binding_b = cls.env[
            "channel.wubook.product.pricelist"
        ].create(
            {
                "odoo_id": pricelist_b.id,
                "backend_id": cls.backend.id,
                "external_id": 1234,
            }
        )

    def test_default_window_capped_to_two_years(self):
        # Backend configured way beyond Wubook's limit
        self.backend.flatten_window_days = 5000
        date_from, date_to = self.binding_b._get_flatten_default_window()
        self.assertEqual(date_from, date.today())
        self.assertEqual(date_to, date.today() + timedelta(days=730))

    def test_default_window_under_cap_left_alone(self):
        self.backend.flatten_window_days = 100
        date_from, date_to = self.binding_b._get_flatten_default_window()
        self.assertEqual(date_to - date_from, timedelta(days=99))


class TestRoomTypeConnectTriggersDependents(TransactionComponentCase):
    """Connecting a room type AFTER pricelists / plans were already
    connected must re-enqueue an export for every dependent binding so
    items / rules previously skipped (because the room type wasn't
    bound) can now be pushed.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        # Pricelist + plan already connected on this backend
        cls.pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Dep PL",
                "company_id": cls.company.id,
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "compute_price": "fixed",
                            "product_id": cls.product_a.id,
                            "fixed_price": 50.0,
                            "date_start_consumption": date.today()
                            + timedelta(days=2),
                            "date_end_consumption": date.today()
                            + timedelta(days=2),
                        },
                    )
                ],
            }
        )
        cls.pricelist_binding = cls.env[
            "channel.wubook.product.pricelist"
        ].create(
            {
                "odoo_id": cls.pricelist.id,
                "backend_id": cls.backend.id,
                "external_id": 7001,
            }
        )
        cls.plan = cls.env["pms.availability.plan"].create(
            {"name": "Dep plan"}
        )
        cls.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": cls.plan.id,
                "room_type_id": cls.room_type_a.id,
                "date": date.today() + timedelta(days=2),
                "pms_property_id": cls.pms_property.id,
            }
        )
        cls.plan_binding = cls.env[
            "channel.wubook.pms.availability.plan"
        ].create(
            {
                "odoo_id": cls.plan.id,
                "backend_id": cls.backend.id,
                "external_id": 7002,
            }
        )

    def test_manual_connect_triggers_dependent_reexports(self):
        wiz = self.env["channel.wubook.connect.wizard"].create(
            {
                "res_model": "pms.room.type",
                "res_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "mode": "manual",
                "manual_external_id": 5050,
            }
        )
        with trap_jobs() as trap:
            wiz.action_connect()
            self.env.cr.precommit.run()
        # One job per dependent binding (1 pricelist + 1 plan = 2)
        trap.assert_jobs_count(2)
        trap.assert_enqueued_job(
            self.pricelist_binding.export_record,
            args=(self.backend, self.pricelist),
        )
        trap.assert_enqueued_job(
            self.plan_binding.export_record,
            args=(self.backend, self.plan),
        )

    def test_no_trigger_when_no_dependent_references(self):
        # Other room type with no pricelist/plan referencing it
        other_product = self.env["product.product"].create(
            {"name": "Other dep product", "type": "service"}
        )
        other_rt = self.env["pms.room.type"].create(
            {
                "name": "RT-orphan",
                "default_code": "ORP",
                "class_id": self.room_type_class.id,
                "product_id": other_product.id,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
            }
        )
        wiz = self.env["channel.wubook.connect.wizard"].create(
            {
                "res_model": "pms.room.type",
                "res_id": other_rt.id,
                "backend_id": self.backend.id,
                "mode": "manual",
                "manual_external_id": 5051,
            }
        )
        with trap_jobs() as trap:
            wiz.action_connect()
            self.env.cr.precommit.run()
        trap.assert_jobs_count(0)


@tagged("post_install", "-at_install")
class TestHotfixNameUpdates(TransactionComponentCase):
    """The two ``update_*_name`` XMLRPC calls were commented out behind a
    HOTFIX. Phase 3 reactivates them; here we assert the adapter wires the
    call to the right endpoint name. Real XMLRPC traffic is mocked.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)

    def _build_adapter(self, binding_model):
        with self.backend.work_on(binding_model) as work:
            return work.component(usage="backend.adapter")

    def test_pricelist_adapter_calls_update_plan_name(self):
        adapter = self._build_adapter("channel.wubook.product.pricelist")
        with mock.patch.object(adapter, "_exec") as mocked_exec:
            adapter.write(
                42,
                {"type": "standard", "name": "Renamed PL", "daily": 1},
            )
        called_endpoints = [c.args[0] for c in mocked_exec.call_args_list]
        self.assertIn("update_plan_name", called_endpoints)

    def test_plan_adapter_calls_rplan_rename(self):
        adapter = self._build_adapter("channel.wubook.pms.availability.plan")
        with mock.patch.object(adapter, "_exec") as mocked_exec:
            adapter.write(42, {"name": "Renamed plan"})
        called_endpoints = [c.args[0] for c in mocked_exec.call_args_list]
        self.assertIn("rplan_rename_rplan", called_endpoints)


class TestNameNotResentWhenUnchanged(TransactionComponentCase):
    """The pricelist / plan mappers must skip ``name`` when the value
    matches ``wubook_last_synced_name`` so the scheduler-driven re-export
    triggered by item / rule changes doesn't fire a redundant
    ``update_plan_name`` / ``rplan_rename_rplan`` XMLRPC call.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)

    def test_pricelist_mapper_skips_name_when_unchanged(self):
        pricelist = self.env["product.pricelist"].create(
            {"name": "Stable PL", "company_id": self.company.id}
        )
        binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": pricelist.id,
                "backend_id": self.backend.id,
                "external_id": 8001,
                "wubook_last_synced_name": "Stable PL",
            }
        )
        with self.backend.work_on(
            "channel.wubook.product.pricelist"
        ) as work:
            mapper = work.component(usage="export.mapper")
        result = mapper.name(binding)
        self.assertIsNone(result)

    def test_pricelist_mapper_emits_name_when_changed(self):
        pricelist = self.env["product.pricelist"].create(
            {"name": "Renamed PL", "company_id": self.company.id}
        )
        binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": pricelist.id,
                "backend_id": self.backend.id,
                "external_id": 8002,
                "wubook_last_synced_name": "Old PL name",
            }
        )
        with self.backend.work_on(
            "channel.wubook.product.pricelist"
        ) as work:
            mapper = work.component(usage="export.mapper")
        result = mapper.name(binding)
        self.assertEqual(result, {"name": "Renamed PL"})

    def test_pricelist_mapper_emits_name_when_never_synced(self):
        pricelist = self.env["product.pricelist"].create(
            {"name": "Brand new PL", "company_id": self.company.id}
        )
        binding = self.env["channel.wubook.product.pricelist"].create(
            {
                "odoo_id": pricelist.id,
                "backend_id": self.backend.id,
            }
        )
        with self.backend.work_on(
            "channel.wubook.product.pricelist"
        ) as work:
            mapper = work.component(usage="export.mapper")
        result = mapper.name(binding)
        self.assertEqual(result, {"name": "Brand new PL"})

    def test_plan_mapper_skips_name_when_unchanged(self):
        plan = self.env["pms.availability.plan"].create(
            {"name": "Stable Plan"}
        )
        binding = self.env["channel.wubook.pms.availability.plan"].create(
            {
                "odoo_id": plan.id,
                "backend_id": self.backend.id,
                "external_id": 9001,
                "wubook_last_synced_name": "Stable Plan",
            }
        )
        with self.backend.work_on(
            "channel.wubook.pms.availability.plan"
        ) as work:
            mapper = work.component(usage="export.mapper")
        result = mapper.name(binding)
        self.assertIsNone(result)

    def test_plan_mapper_emits_name_when_changed(self):
        plan = self.env["pms.availability.plan"].create(
            {"name": "Renamed Plan"}
        )
        binding = self.env["channel.wubook.pms.availability.plan"].create(
            {
                "odoo_id": plan.id,
                "backend_id": self.backend.id,
                "external_id": 9002,
                "wubook_last_synced_name": "Original Plan",
            }
        )
        with self.backend.work_on(
            "channel.wubook.pms.availability.plan"
        ) as work:
            mapper = work.component(usage="export.mapper")
        result = mapper.name(binding)
        self.assertEqual(result, {"name": "Renamed Plan"})
