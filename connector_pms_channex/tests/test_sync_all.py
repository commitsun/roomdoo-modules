# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import ChannexConnectorCase
from .test_rate_plans import ChannexRatePlanCase


@tagged("post_install", "-at_install")
class TestChannexSyncAllNeedsTheProperty(ChannexConnectorCase):
    def test_a_property_not_on_channex_is_not_synchronised(self):
        """It may well be on Channex already, made by hand: connecting it is
        where the hotel says whether to take that one over."""
        with self.assertRaisesRegex(UserError, "Connect"):
            self.backend.action_channex_sync_all()
        self.assertFalse(self.server.calls)


@tagged("post_install", "-at_install")
class TestChannexSyncAll(ChannexRatePlanCase):
    """A property on Channex with a pricelist sold, and a second room type it
    has rooms of that never reached Channex."""

    def setUp(self):
        super().setUp()
        self.env["channel.channex.pms.property"].search(
            [("backend_id", "=", self.backend.id)]
        ).state_length = 40
        self._connect()
        self.other_room_type = self._make_room_type("RT-CHX-2", "RTC2")
        self.env["pms.room"].create(
            {
                "name": "CHX-201",
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.other_room_type.id,
                "capacity": 2,
            }
        )
        self.server.calls.clear()

    def _sync_all(self):
        """Press the button, and end the transaction."""
        with trap_jobs() as trap:
            self.backend.action_channex_sync_all()
            self.env.cr.precommit.run()
        return trap

    def _queued(self, trap):
        return [job.method_name for job in trap.enqueued_jobs]

    # -- what is sent ---------------------------------------------------------

    def test_the_property_is_brought_in_line(self):
        self.pms_property.with_context(connector_no_export=True).name = "Renamed"
        self._sync_all()
        [call] = self.server.calls_to("PUT", "properties")
        self.assertEqual(call[2]["property"]["title"], "Renamed")

    def test_a_room_type_never_sent_is_made(self):
        self._sync_all()
        binding = self.other_room_type.channel_channex_bind_ids
        self.assertTrue(binding.external_id)
        self.assertIn(
            "RT-CHX-2",
            [
                call[2]["room_type"]["title"]
                for call in self.server.calls_to("POST", "room_types")
            ],
        )

    def test_its_rate_plans_are_made_with_it(self):
        self._sync_all()
        made = [
            plan
            for plan in self._plans()
            if plan["room_type_id"]
            == self.other_room_type.channel_channex_bind_ids.external_id
        ]
        self.assertEqual(len(made), 2)

    def test_another_property_is_left_alone(self):
        """A room type two hotels share is brought in line for this one only."""
        other = self.env["pms.property"].create(
            {
                "name": "Channex Property 2",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist.id,
                "tz": "Europe/Madrid",
            }
        )
        other_backend = self.env["channel.channex.backend"].create(
            {
                "name": "Channex Backend 2",
                "pms_property_id": other.id,
                "backend_type_id": self.backend.backend_type_id.id,
                "api_key": "test-key",
                "environment": "staging",
                "group_id": self.backend.group_id,
            }
        )
        self.env["channel.channex.pms.property"].export_record(other_backend, other)
        self.room_type.pms_property_ids = [(4, other.id)]
        self.env["pms.room"].create(
            {
                "name": "CHX-901",
                "pms_property_id": other.id,
                "room_type_id": self.room_type.id,
                "capacity": 2,
            }
        )
        self.server.calls.clear()
        self._sync_all()
        other_uuid = other_backend._channex_bound_property_id()
        self.assertFalse(
            [
                call
                for call in self.server.calls_to("POST", "room_types")
                if call[2]["room_type"]["property_id"] == other_uuid
            ]
        )

    def test_the_channels_connected_on_channex_are_read(self):
        self._sync_all()
        self.assertTrue(self.server.calls_to("GET", "channels"))

    def test_the_full_sync_is_one_call_for_each(self):
        """What Channex's certification checks: the availability of every room
        type in one call, the rates and restrictions of every rate plan in
        another."""
        self._sync_all()
        self.backend.channex_send_ari("availability")
        self.backend.channex_send_ari("restrictions")
        self.assertEqual(len(self.server.calls_to("POST", "availability")), 1)
        self.assertEqual(len(self.server.calls_to("POST", "restrictions")), 1)

    def test_the_bookings_waiting_are_read_last(self):
        trap = self._sync_all()
        self.assertIn("channex_import_booking_revisions", self._queued(trap))

    # -- once a day ---------------------------------------------------------

    def test_when_it_was_done_is_kept(self):
        self._sync_all()
        self.assertTrue(self.backend.channex_full_sync_date)

    def test_in_production_it_is_refused_within_a_day(self):
        self.backend.environment = "production"
        self.backend.channex_full_sync_date = (
            fields.Datetime.now() - datetime.timedelta(hours=23)
        )
        with self.assertRaisesRegex(UserError, "once a day"):
            self.backend.action_channex_sync_all()
        self.assertFalse(self.server.calls)

    def test_in_production_it_is_let_through_a_day_later(self):
        self.backend.environment = "production"
        last = fields.Datetime.now() - datetime.timedelta(hours=25)
        self.backend.channex_full_sync_date = last
        self._sync_all()
        self.assertTrue(self.server.calls_to("PUT", "properties"))
        self.assertGreater(self.backend.channex_full_sync_date, last)

    def test_staging_is_not_held_back(self):
        """Where it is tried over and over, and nothing is sold."""
        self.backend.channex_full_sync_date = fields.Datetime.now()
        self._sync_all()
        self.assertTrue(self.server.calls_to("PUT", "properties"))


@tagged("post_install", "-at_install")
class TestChannexPricelistsFromTheBackend(ChannexRatePlanCase):
    """Choosing on the backend the pricelists sold through Channex."""

    def _rate_plans_sold(self):
        return self._rate_plans().filtered(lambda r: not r.channex_to_remove)

    def test_a_pricelist_chosen_is_sold(self):
        with trap_jobs():
            self.backend.channex_pricelist_ids = self.pricelist
        self.assertEqual(
            self.env["channel.channex.product.pricelist"]
            .search([("backend_id", "=", self.backend.id)])
            .odoo_id,
            self.pricelist,
        )
        # Made there and then, not queued.
        self.assertEqual(len(self._rate_plans_sold()), 2)
        self.assertEqual(len(self._plans()), 2)

    def test_one_connected_from_the_pricelist_shows_here(self):
        self._connect()
        self.assertEqual(self.backend.channex_pricelist_ids, self.pricelist)

    def test_a_pricelist_taken_out_stops_being_sold(self):
        with trap_jobs():
            self.backend.channex_pricelist_ids = self.pricelist
        with trap_jobs() as trap:
            self.backend.channex_pricelist_ids = False
        self.assertFalse(
            self.env["channel.channex.product.pricelist"].search(
                [("backend_id", "=", self.backend.id)]
            )
        )
        self.assertFalse(self._rate_plans_sold())
        self.assertEqual(
            [job.method_name for job in trap.enqueued_jobs].count("channex_remove"), 2
        )

    def test_an_archived_pricelist_keeps_its_connection(self):
        """It is not shown, so leaving it out is not taking it out."""
        archived = self.env["product.pricelist"].create(
            {"name": "Channex PL archived", "company_id": self.company.id}
        )
        with trap_jobs():
            self.backend.channex_pricelist_ids = self.pricelist | archived
            archived.active = False
            self.backend.invalidate_recordset()
            self.backend.channex_pricelist_ids = False
        self.assertEqual(
            self.env["channel.channex.product.pricelist"]
            .with_context(active_test=False)
            .search([("backend_id", "=", self.backend.id)])
            .odoo_id,
            archived,
        )

    def test_what_channex_refuses_is_said_on_saving(self):
        self.server.refuse_rate_plan("Channex PL – Breakfast")
        with self.assertRaises(UserError):
            self.backend.channex_pricelist_ids = self.pricelist
        self.assertFalse(
            self.env["channel.channex.product.pricelist"].search(
                [("backend_id", "=", self.backend.id)]
            )
        )
