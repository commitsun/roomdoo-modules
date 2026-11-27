# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .test_bookings import ChannexBookingCase

PRICELIST_BINDING = "channel.channex.product.pricelist"
ROOM_TYPE_BINDING = "channel.channex.pms.room.type"
RATE_PLANS = "rate_plans"


class ChannexRatePlanCase(ChannexBookingCase):
    """A property on Channex with one room type, and a pricelist that sells it
    room only and with breakfast."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.room_only = cls.env.ref("pms.product_template_board_room_only")
        cls.breakfast = cls.env["product.template"].create(
            {
                "name": "Breakfast",
                "type": "service",
                "is_board_service": True,
                "channex_meal_type": "breakfast",
            }
        )
        cls.pricelist.board_service_ids = [(6, 0, [cls.room_only.id, cls.breakfast.id])]

    # -- helpers -------------------------------------------------------------

    def _connect(self, pricelist=None):
        """The way a hotel does it: the connection wizard of the pricelist."""
        pricelist = pricelist or self.pricelist
        action = pricelist.action_open_channel_connect_wizard()
        wizard = self.env["channel.connect.wizard"].browse(action["res_id"])
        wizard.backend_id = self.backend.parent_id
        with trap_jobs() as trap:
            wizard.action_connect()
        self.server.calls.clear()
        return wizard, trap

    def _settle(self, action=None):
        """Do something, and run the jobs it queues and the ones they queue."""
        with trap_jobs() as trap:
            if action:
                action()
            for _round in range(3):
                if not trap.enqueued_jobs:
                    break
                trap.perform_enqueued_jobs()
        return trap

    def _sync(self):
        with trap_jobs() as trap:
            result = self.backend.channex_sync_rate_plans()
        return result, trap

    def _plans(self):
        return sorted(
            self.server.store.get(RATE_PLANS, []),
            key=lambda plan: (plan["room_type_id"], plan["title"]),
        )

    def _plan(self, title, room_type_uuid=None):
        room_type_uuid = room_type_uuid or self.room_type_uuid
        [plan] = [
            plan
            for plan in self.server.store.get(RATE_PLANS, [])
            if plan["title"] == title and plan["room_type_id"] == room_type_uuid
        ]
        return plan

    def _rate_plans(self):
        return self.env["channel.channex.rate.plan"].search(
            [("backend_id", "=", self.backend.id)]
        )

    def _writes(self):
        return [
            (call[0], call[1])
            for call in self.server.calls
            if call[0] in ("POST", "PUT", "DELETE") and call[1].startswith(RATE_PLANS)
        ]

    def _seed_plan(self, title, **values):
        self.server.seed(
            RATE_PLANS,
            [
                {
                    "id": f"hotel-{title}",
                    "property_id": self.property_uuid,
                    "room_type_id": self.room_type_uuid,
                    "title": title,
                    "currency": self.pricelist.currency_id.name,
                    "sell_mode": "per_person",
                    "rate_mode": "manual",
                    "meal_type": "none",
                    "children_fee": "0.00",
                    "options": [{"occupancy": 2, "is_primary": True, "rate": 0}],
                    **values,
                }
            ],
        )


@tagged("post_install", "-at_install")
class TestChannexRatePlans(ChannexRatePlanCase):
    """What a connected pricelist becomes on Channex."""

    def test_the_wizard_only_creates_a_pricelist(self):
        """A pricelist is never one record on Channex to connect it to."""
        action = self.pricelist.action_open_channel_connect_wizard()
        wizard = self.env["channel.connect.wizard"].browse(action["res_id"])
        self.assertFalse(wizard.connects_to_existing)
        self.assertEqual(wizard.mode, "new")

    def test_connecting_a_pricelist_makes_its_rate_plans(self):
        self._connect()
        title = "Channex PL – Breakfast"
        self.assertEqual(
            [plan["title"] for plan in self._plans()],
            ["Channex PL – Breakfast", "Channex PL – Room Only"],
        )
        plan = self._plan(title)
        self.assertEqual(plan["property_id"], self.property_uuid)
        self.assertEqual(plan["sell_mode"], "per_person")
        self.assertEqual(plan["rate_mode"], "manual")
        self.assertEqual(plan["currency"], self.pricelist.currency_id.name)
        self.assertEqual(plan["meal_type"], "breakfast")
        self.assertEqual(plan["children_fee"], "0.00")
        self.assertEqual(
            plan["options"],
            [
                {"occupancy": 1, "is_primary": False, "rate": 0},
                {"occupancy": 2, "is_primary": True, "rate": 0},
            ],
        )
        self.assertEqual(self._plan("Channex PL – Room Only")["meal_type"], "room_only")
        rate_plans = self._rate_plans()
        self.assertEqual(
            sorted(rate_plans.mapped("external_id")),
            sorted(plan["id"] for plan in self._plans()),
        )
        self.assertEqual(self.pricelist.channel_backend_ids, self.backend.parent_id)

    def test_a_board_service_with_no_meal_plan_tells_nothing(self):
        self.breakfast.channex_meal_type = False
        self._connect()
        self.assertEqual(self._plan("Channex PL – Breakfast")["meal_type"], "none")

    def test_the_primary_occupancy_is_the_one_the_price_is_given_for(self):
        self.room_type.default_occupancy = 1
        self._connect()
        options = self._plan("Channex PL – Breakfast")["options"]
        self.assertEqual([o["occupancy"] for o in options if o["is_primary"]], [1])

    def test_the_children_fee_is_the_pricelist_s_for_the_room_type(self):
        self.env["pms.pricelist.occupancy"].create(
            {
                "pricelist_id": self.pricelist.id,
                "room_type_id": self.room_type.id,
                "children_fee": 12.5,
            }
        )
        self._connect()
        self.assertEqual(self._plan("Channex PL – Breakfast")["children_fee"], "12.50")

    def test_the_title_is_in_the_hotel_s_language(self):
        """Not in that of whoever happens to trigger the send: the title is
        also how a rate plan is recognised."""
        self.env["res.lang"]._activate_lang("es_ES")
        self.pms_property.lang = "es_ES"
        self.pricelist.with_context(lang="es_ES").name = "Tarifa"
        self.breakfast.with_context(lang="es_ES").name = "Desayuno"
        self.room_only.with_context(lang="es_ES").name = "Solo alojamiento"
        self._connect()
        self.assertEqual(
            [plan["title"] for plan in self._plans()],
            ["Tarifa – Desayuno", "Tarifa – Solo alojamiento"],
        )

    def test_every_room_type_on_channex_gets_the_rate_plans(self):
        room_type = self._make_room_type("RT-CHX2", "RTC2")
        self.env["pms.room"].create(
            {
                "name": "CHX-201",
                "pms_property_id": self.pms_property.id,
                "room_type_id": room_type.id,
                "capacity": 3,
            }
        )
        binding = self._bind_and_export(ROOM_TYPE_BINDING, room_type)
        self._connect()
        self.assertEqual(len(self._plans()), 4)
        options = self._plan("Channex PL – Breakfast", binding.external_id)["options"]
        self.assertEqual([o["occupancy"] for o in options], [1, 2, 3])

    def test_a_board_service_of_another_property_is_left_out(self):
        other = self.env["pms.property"].create(
            {
                "name": "Other property",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist.id,
            }
        )
        self.breakfast.pms_property_ids = [(6, 0, [other.id])]
        self._connect()
        self.assertEqual(
            [plan["title"] for plan in self._plans()], ["Channex PL – Room Only"]
        )

    def test_the_hotel_s_own_rate_plans_are_left_alone(self):
        self._seed_plan("Non refundable")
        self._connect()
        self.assertEqual(self._plan("Non refundable")["id"], "hotel-Non refundable")
        self.assertNotIn(
            "hotel-Non refundable", self._rate_plans().mapped("external_id")
        )
        self._settle(lambda: setattr(self.pricelist, "name", "Renamed"))
        self.assertFalse(
            [call for call in self.server.calls if "hotel-" in call[1]],
        )


@tagged("post_install", "-at_install")
class TestChannexRatePlanSync(ChannexRatePlanCase):
    """How the rate plans follow the pricelist, and stay one each."""

    def setUp(self):
        super().setUp()
        self._connect()

    def test_nothing_changed_writes_nothing(self):
        self._sync()
        self.assertEqual(self._writes(), [])

    def test_renaming_the_pricelist_rewrites_the_titles(self):
        before = {plan["id"] for plan in self._plans()}
        self._settle(lambda: setattr(self.pricelist, "name", "BAR"))
        self.assertEqual(
            [plan["title"] for plan in self._plans()],
            ["BAR – Breakfast", "BAR – Room Only"],
        )
        self.assertEqual({plan["id"] for plan in self._plans()}, before)
        self.assertEqual([method for method, _path in self._writes()], ["PUT", "PUT"])
        # What changed and nothing else.
        self.assertEqual(
            [set(call[2]["rate_plan"]) for call in self.server.calls_to("PUT")],
            [{"title"}, {"title"}],
        )

    def test_a_new_meal_plan_reaches_the_rate_plans_of_the_board_service(self):
        self._settle(lambda: setattr(self.breakfast, "channex_meal_type", "half_board"))
        plan = self._plan("Channex PL – Breakfast")
        self.assertEqual(plan["meal_type"], "half_board")
        self.assertEqual(self._writes(), [("PUT", f"{RATE_PLANS}/{plan['id']}")])

    def test_a_new_children_fee_reaches_the_rate_plans(self):
        self._settle(
            lambda: self.env["pms.pricelist.occupancy"].create(
                {
                    "pricelist_id": self.pricelist.id,
                    "room_type_id": self.room_type.id,
                    "children_fee": 8,
                }
            )
        )
        self.assertEqual({plan["children_fee"] for plan in self._plans()}, {"8.00"})

    def test_another_product_changing_queues_nothing(self):
        product = self.env["product.template"].create({"name": "Parking"})
        trap = self._settle(lambda: setattr(product, "name", "Garage"))
        self.assertEqual(self.server.calls, [])
        self.assertFalse(trap.calls)

    def test_a_board_service_added_to_the_pricelist_gets_its_rate_plan(self):
        half_board = self.env["product.template"].create(
            {"name": "Half Board", "type": "service", "is_board_service": True}
        )
        self._settle(
            lambda: self.pricelist.write({"board_service_ids": [(4, half_board.id)]})
        )
        self.assertEqual(len(self._plans()), 3)
        self.assertEqual(len(self._rate_plans()), 3)

    def test_a_room_type_reaching_channex_gets_the_rate_plans(self):
        room_type = self._make_room_type("RT-CHX2", "RTC2")
        self._settle(
            lambda: self.env["pms.room"].create(
                {
                    "name": "CHX-201",
                    "pms_property_id": self.pms_property.id,
                    "room_type_id": room_type.id,
                    "capacity": 2,
                }
            )
        )
        binding = self.env[ROOM_TYPE_BINDING].search([("odoo_id", "=", room_type.id)])
        self.assertTrue(binding.external_id)
        self.assertEqual(
            len([p for p in self._plans() if p["room_type_id"] == binding.external_id]),
            2,
        )

    def test_a_room_type_leaving_channex_takes_its_rate_plans(self):
        """Channex removes them with the room type, so they are only
        forgotten here."""
        rate_plans = self._rate_plans()
        self._settle(self.room_type.action_archive)
        self.assertFalse(rate_plans.exists())
        self.assertEqual(self._plans(), [])
        self.assertFalse(self.server.calls_to("DELETE", RATE_PLANS))

    def test_a_rate_plan_removed_by_hand_on_channex_is_made_again(self):
        plan = self._plan("Channex PL – Breakfast")
        self.server.store[RATE_PLANS].remove(plan)
        result, _trap = self._sync()
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(self._plans()), 2)
        rate_plan = self._rate_plans().filtered(
            lambda r: r.board_service_id == self.breakfast
        )
        self.assertEqual(
            rate_plan.external_id, self._plan("Channex PL – Breakfast")["id"]
        )


@tagged("post_install", "-at_install")
class TestChannexRatePlanAdoption(ChannexRatePlanCase):
    """A rate plan already on Channex under the same name is taken over."""

    def test_a_rate_plan_the_hotel_made_by_hand_is_taken_over(self):
        """With whatever is mapped to it: it is the same rate plan."""
        self._seed_plan("Channex PL – Breakfast", meal_type="none")
        self._connect()
        self.assertEqual(len(self._plans()), 2)
        rate_plan = self._rate_plans().filtered(
            lambda r: r.board_service_id == self.breakfast
        )
        self.assertEqual(rate_plan.external_id, "hotel-Channex PL – Breakfast")
        # And it is brought in line.
        self.assertEqual(self._plan("Channex PL – Breakfast")["meal_type"], "breakfast")

    def test_one_priced_another_way_is_refused_and_not_made_twice(self):
        self._seed_plan("Channex PL – Breakfast", sell_mode="per_room")
        with self.assertRaisesRegex(UserError, "priced another way"):
            self._connect()
        self.assertEqual(
            len([p for p in self._plans() if p["title"] == "Channex PL – Breakfast"]),
            1,
        )

    def test_a_send_failing_halfway_does_not_make_them_twice(self):
        """What Channex made before the failure is never recorded, as the
        transaction is rolled back; it is taken over the next time."""
        self.server.refuse_rate_plan("Channex PL – Breakfast")
        with self.assertRaises(UserError):
            self._connect()
        self.server.refuse_rate_plan("Channex PL – Breakfast", refused=False)
        self.assertEqual(len(self._plans()), 1)
        self.assertFalse(self._rate_plans())
        self._connect()
        self.assertEqual(len(self._plans()), 2)
        self.assertEqual(
            sorted(self._rate_plans().mapped("external_id")),
            sorted(plan["id"] for plan in self._plans()),
        )


@tagged("post_install", "-at_install")
class TestChannexRatePlanRemoval(ChannexRatePlanCase):
    """Taking down what is no longer sold, never forcing Channex."""

    def setUp(self):
        super().setUp()
        self._connect()
        self.breakfast_plan = self._rate_plans().filtered(
            lambda r: r.board_service_id == self.breakfast
        )

    def _drop_breakfast(self):
        self.pricelist.write({"board_service_ids": [(3, self.breakfast.id)]})

    def test_a_board_service_no_longer_sold_takes_its_rate_plan_down(self):
        external_id = self.breakfast_plan.external_id
        self._settle(self._drop_breakfast)
        self.assertFalse(self.breakfast_plan.exists())
        self.assertEqual(
            [plan["title"] for plan in self._plans()], ["Channex PL – Room Only"]
        )
        self.assertEqual(self._writes(), [("DELETE", f"{RATE_PLANS}/{external_id}")])

    def test_one_mapped_to_a_channel_stays_until_it_is_unmapped(self):
        self.server.map_rate_plan(self.breakfast_plan.external_id)
        with trap_jobs() as trap:
            self._drop_breakfast()
            trap.perform_enqueued_jobs()
            # The removal fails on its own job, and only it.
            with self.assertRaisesRegex(UserError, "unmap it on Channex"):
                trap.perform_enqueued_jobs()
        self.assertTrue(self.breakfast_plan.channex_to_remove)
        self.assertEqual(len(self._plans()), 2)
        # Bringing the rest in line does not queue it again.
        result, trap = self._sync()
        self.assertEqual(result["removing"], 0)
        self.assertFalse(trap.enqueued_jobs)
        # Unmapped, running the job again takes it down.
        self.server.map_rate_plan(self.breakfast_plan.external_id, mapped=False)
        self.breakfast_plan.channex_remove()
        self.assertFalse(self.breakfast_plan.exists())
        self.assertEqual(len(self._plans()), 1)

    def test_sold_again_before_the_removal_runs_it_stays(self):
        with trap_jobs() as trap:
            self._drop_breakfast()
            trap.perform_enqueued_jobs()
            [removal] = trap.enqueued_jobs
            self.pricelist.write({"board_service_ids": [(4, self.breakfast.id)]})
            self.backend.channex_sync_rate_plans()
            removal.perform()
        self.assertFalse(self.breakfast_plan.channex_to_remove)
        self.assertEqual(len(self._plans()), 2)
        self.assertFalse(self.server.calls_to("DELETE", RATE_PLANS))

    def test_disconnecting_the_pricelist_takes_its_rate_plans_down(self):
        self._settle(lambda: self.pricelist.channel_channex_bind_ids.unlink())
        self.assertEqual(self._plans(), [])
        self.assertFalse(self._rate_plans())
        self.assertFalse(self.pricelist.channel_backend_ids)

    def test_archiving_the_pricelist_takes_its_rate_plans_down(self):
        self._settle(lambda: setattr(self.pricelist, "active", False))
        self.assertEqual(self._plans(), [])
        self.assertFalse(self._rate_plans())
