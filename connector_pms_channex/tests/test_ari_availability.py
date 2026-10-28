# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

import odoo
from odoo.tests import tagged

from odoo.addons.queue_job.exception import RetryableJobError
from odoo.addons.queue_job.tests.common import trap_jobs

from ..models.common.ari import stage_ari
from .test_bookings import ChannexBookingCase

ROOM_TYPE_BINDING = "channel.channex.pms.room.type"


def _day(date):
    return datetime.date.fromisoformat(date) if isinstance(date, str) else date


class ChannexAvailabilityCase(ChannexBookingCase):
    """A property on Channex with one room type of two rooms, nothing pending."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.door = cls.env["pms.sale.channel"].create(
            {"name": "Door", "channel_type": "direct"}
        )
        cls.rooms = cls.env["pms.room"].search(
            [("room_type_id", "=", cls.room_type.id)], order="name"
        )

    def setUp(self):
        super().setUp()
        # Exporting the room type publishes its whole window, which is a test of
        # its own: every other one starts with nothing pending.
        self._publish()
        self.server.calls.clear()
        self.first, self.last = self.backend._channex_ari_horizon()
        self.night = self.first + datetime.timedelta(days=10)

    # -- helpers -------------------------------------------------------------

    def _commit(self):
        """What the end of a transaction does: merge what was noted and queue
        the sends. Returns the trap holding them."""
        with trap_jobs() as trap:
            self.env.cr.precommit.run()
        return trap

    def _publish(self):
        self._commit()
        return self.backend.channex_send_ari("availability")

    def _binding(self, room_type=None):
        return self.env[ROOM_TYPE_BINDING].search(
            [
                ("odoo_id", "=", (room_type or self.room_type).id),
                ("backend_id", "=", self.backend.id),
            ]
        )

    def _pending(self, room_type=None):
        binding = self._binding(room_type)
        binding.invalidate_recordset()
        return binding.channex_ari_pending_from, binding.channex_ari_pending_to

    def _available(self, night, room_type=None):
        binding = self._binding(room_type)
        return self.server.availability.get((binding.external_id, _day(night)))

    def _second_room_type(self, name="RT-CHX2", code="RTC2", room_name="CHX-201"):
        room_type = self._make_room_type(name, code)
        room = self.env["pms.room"].create(
            {
                "name": room_name,
                "pms_property_id": self.pms_property.id,
                "room_type_id": room_type.id,
                "capacity": 2,
            }
        )
        self._bind_and_export(ROOM_TYPE_BINDING, room_type)
        self._publish()
        self.server.calls.clear()
        return room_type, room

    def _reserve(self, checkin, nights=1, room=None, room_type=None):
        values = {
            "pms_property_id": self.pms_property.id,
            "room_type_id": (room_type or self.room_type).id,
            "checkin": checkin,
            "checkout": checkin + datetime.timedelta(days=nights),
            "partner_name": "A guest",
            "adults": 1,
            "sale_channel_origin_id": self.door.id,
        }
        if room:
            values["preferred_room_id"] = room.id
        return self.env["pms.reservation"].create(values)


@tagged("post_install", "-at_install")
class TestChannexAvailabilitySend(ChannexAvailabilityCase):
    """What a send computes, and what it does with Channex's answer."""

    def test_a_reservation_publishes_the_nights_it_takes(self):
        self._reserve(self.night, nights=2, room=self.rooms[0])
        self._publish()
        posts = self.server.calls_to("POST", "availability")
        self.assertEqual(len(posts), 1)
        self.assertEqual(
            posts[0][2]["values"],
            [
                {
                    "property_id": self.property_uuid,
                    "room_type_id": self.room_type_uuid,
                    "date_from": str(self.night),
                    "date_to": str(self.night + datetime.timedelta(days=1)),
                    "availability": 1,
                }
            ],
        )

    def test_every_room_type_goes_in_one_call(self):
        """Channex counts calls per property, and certification wants several
        changes in one: a send is per backend, never per room type."""
        other_type, other_room = self._second_room_type()
        self._reserve(self.night, room=self.rooms[0])
        self._reserve(self.night, room=other_room, room_type=other_type)
        self._publish()
        self.assertEqual(len(self.server.calls_to("POST", "availability")), 1)
        self.assertEqual(self._available(self.night), 1)
        self.assertEqual(self._available(self.night, other_type), 0)

    def test_nights_alike_go_as_one_range(self):
        self.backend.action_channex_publish_availability()
        self._publish()
        values = self.server.calls_to("POST", "availability")[0][2]["values"]
        self.assertEqual(
            values,
            [
                {
                    "property_id": self.property_uuid,
                    "room_type_id": self.room_type_uuid,
                    "date_from": str(self.first),
                    "date_to": str(self.last),
                    "availability": 2,
                }
            ],
        )

    def test_the_inventory_of_the_property_caps_the_availability(self):
        self.env["pms.inventory.rule"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "date_from": self.night,
                "date_to": self.night + datetime.timedelta(days=2),
                "max_avail": 1,
            }
        )
        self._publish()
        for offset in range(3):
            self.assertEqual(
                self._available(self.night + datetime.timedelta(days=offset)), 1
            )

    def test_a_send_with_nothing_pending_makes_no_call(self):
        self.assertEqual(self.backend.channex_send_ari("availability"), 0)
        self.assertFalse(self.server.calls_to("POST", "availability"))

    def test_the_send_clears_what_it_published(self):
        self._reserve(self.night, room=self.rooms[0])
        self._publish()
        self.assertEqual(self._pending(), (False, False))

    def test_with_exports_disabled_nothing_leaves(self):
        """A copy of a live database must not publish to the real Channex. What
        it had pending is dropped: publishing it later would be stale anyway."""
        self.backend.export_disabled = True
        self._reserve(self.night, room=self.rooms[0])
        self._publish()
        self.assertFalse(self.server.calls_to("POST", "availability"))
        self.assertEqual(self._pending(), (False, False))

    def test_what_channex_refuses_is_kept_and_not_retried(self):
        """Channex answers 200 and lists what it threw away. Sending it again
        would be refused again, so it is kept for someone to look at."""
        self.server.refuse_room_type(self.room_type_uuid)
        self._reserve(self.night, room=self.rooms[0])
        self._publish()
        warning = self.backend.channex_ari_warning_ids
        self.assertEqual(len(warning), 1)
        self.assertEqual(warning.kind, "availability")
        self.assertEqual(warning.count, 1)
        self.assertIn(self.room_type_uuid, warning.warnings)
        self.assertEqual(self._pending(), (False, False))


@tagged("post_install", "-at_install")
class TestChannexAvailabilityStaging(ChannexAvailabilityCase):
    """Which changes leave nights pending, and how the sends are queued."""

    def test_one_send_is_queued_per_backend_and_kind(self):
        self._reserve(self.night, room=self.rooms[0])
        self._reserve(self.night + datetime.timedelta(days=5), room=self.rooms[1])
        trap = self._commit()
        self.assertEqual(len(trap.enqueued_jobs), 1)
        job = trap.enqueued_jobs[0]
        self.assertEqual(job.method_name, "channex_send_ari")
        self.assertEqual(job.args, ("availability",))
        self.assertEqual(
            job.identity_key, f"channex_ari:{self.backend.id}:availability"
        )
        self.assertTrue(job.eta, "A send waits a moment, so a burst is one call")
        self.assertEqual(job.channel, "root.pms.channex.ari")
        self.assertEqual(
            self._pending(), (self.night, self.night + datetime.timedelta(days=5))
        )

    def test_nights_outside_the_window_are_left_out(self):
        """A past night is not on sale, and one beyond the window is published
        the day it enters it."""
        stage_ari(
            self.env,
            "availability",
            (self.pms_property.id, self.room_type.id),
            self.first - datetime.timedelta(days=5),
            self.last + datetime.timedelta(days=5),
        )
        self._commit()
        self.assertEqual(self._pending(), (self.first, self.last))

    def test_nothing_entirely_out_of_the_window_queues_a_send(self):
        stage_ari(
            self.env,
            "availability",
            (self.pms_property.id, self.room_type.id),
            self.first - datetime.timedelta(days=5),
            self.first - datetime.timedelta(days=1),
        )
        trap = self._commit()
        self.assertFalse(trap.enqueued_jobs)
        self.assertEqual(self._pending(), (False, False))

    def test_a_cancellation_gives_the_nights_back(self):
        reservation = self._reserve(self.night, nights=2, room=self.rooms[0])
        self._publish()
        reservation.action_cancel()
        self._publish()
        self.assertEqual(self._available(self.night), 2)
        self.assertEqual(self._available(self.night + datetime.timedelta(days=1)), 2)

    def test_moving_a_reservation_publishes_the_room_type_it_leaves(self):
        """The rooms of its nights are recomputed where no event is raised, and
        the room type it leaves is gone from it after the write."""
        other_type, other_room = self._second_room_type()
        reservation = self._reserve(self.night, room=self.rooms[0])
        self._publish()
        reservation.write(
            {"room_type_id": other_type.id, "preferred_room_id": other_room.id}
        )
        self._publish()
        self.assertEqual(self._available(self.night), 2)
        self.assertEqual(self._available(self.night, other_type), 0)

    def test_moving_a_night_publishes_the_room_it_leaves(self):
        """Moved twice, so the second move leaves a room type that is neither
        the one sold nor the one it lands on: only the night as it was before
        the write can tell."""
        second_type, second_room = self._second_room_type()
        third_type, third_room = self._second_room_type("RT-CHX3", "RTC3", "CHX-301")
        reservation = self._reserve(self.night, nights=2, room=self.rooms[0])
        night = reservation.reservation_line_ids.sorted("date")[0]
        night.room_id = second_room
        self._publish()
        self.assertEqual(self._available(self.night, second_type), 0)
        night.room_id = third_room
        self._publish()
        self.assertEqual(self._available(self.night), 2)
        self.assertEqual(self._available(self.night, second_type), 1)
        self.assertEqual(self._available(self.night, third_type), 0)
        self.assertEqual(self._available(self.night + datetime.timedelta(days=1)), 1)

    def test_shortening_a_rule_publishes_the_nights_it_stops_capping(self):
        rule = self.env["pms.inventory.rule"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "date_from": self.night,
                "date_to": self.night + datetime.timedelta(days=4),
                "max_avail": 1,
            }
        )
        self._publish()
        rule.date_to = self.night + datetime.timedelta(days=1)
        self._publish()
        self.assertEqual(self._available(self.night + datetime.timedelta(days=1)), 1)
        self.assertEqual(self._available(self.night + datetime.timedelta(days=4)), 2)

    def test_a_rule_of_an_agency_is_not_the_availability_of_the_room_type(self):
        """What an agency has set aside is a cap of its own, published apart."""
        # An agency in pms needs an indirect sale channel and a pricelist that
        # is available for pms, which in turn needs an availability plan.
        self.pricelist.write(
            {
                "is_pms_available": True,
                "availability_plan_id": self.env["pms.availability.plan"]
                .create({"name": "Channex AP"})
                .id,
            }
        )
        agency = (
            self.env["res.partner"]
            .with_company(self.company)
            .create(
                {
                    "name": "An agency",
                    "is_agency": True,
                    "sale_channel_id": self.env["pms.sale.channel"]
                    .create({"name": "OTAs", "channel_type": "indirect"})
                    .id,
                    "property_product_pricelist": self.pricelist.id,
                }
            )
        )
        self.env["pms.inventory.rule"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "agency_id": agency.id,
                "date_from": self.night,
                "date_to": self.night,
                "max_avail": 0,
            }
        )
        self._commit()
        self.assertEqual(self._pending(), (False, False))

    def test_archiving_a_room_publishes_the_whole_window(self):
        self.rooms[0].active = False
        self._commit()
        self.assertEqual(self._pending(), (self.first, self.last))

    def test_a_room_part_of_another_takes_both_off_sale(self):
        """pms takes the room a reservation occupies off sale, and the room it
        is part of along with it."""
        suite_type, suite = self._second_room_type("RT-SUITE", "RTSU", "CHX-301")
        self.rooms[0].parent_id = suite
        self._publish()
        self._reserve(self.night, room=self.rooms[0])
        self._commit()
        self.assertEqual(self._pending(suite_type), (self.night, self.night))

    def test_the_inventory_a_room_type_falls_back_on_publishes_its_window(self):
        self.room_type.default_max_avail = 1
        self._commit()
        self.assertEqual(self._pending(), (self.first, self.last))

    def test_a_room_type_reaching_channex_publishes_its_window(self):
        other_type = self._make_room_type("RT-NEW", "RTNW")
        self.env["pms.room"].create(
            {
                "name": "CHX-401",
                "pms_property_id": self.pms_property.id,
                "room_type_id": other_type.id,
                "capacity": 2,
            }
        )
        self._bind_and_export(ROOM_TYPE_BINDING, other_type)
        self._commit()
        self.assertEqual(self._pending(other_type), (self.first, self.last))

    def test_the_full_publication_covers_every_room_type(self):
        other_type, _other_room = self._second_room_type()
        self.backend.action_channex_publish_availability()
        self._commit()
        self.assertEqual(self._pending(), (self.first, self.last))
        self.assertEqual(self._pending(other_type), (self.first, self.last))

    def test_the_daily_run_publishes_the_nights_entering_the_window(self):
        self.env["channel.channex.backend"].channex_cron_ari_horizon()
        self._commit()
        self.assertEqual(
            self._pending(), (self.last - datetime.timedelta(days=6), self.last)
        )

    def test_an_imported_booking_publishes_its_nights(self):
        """The import silences every listener, so it notes its nights itself."""
        checkin = self.first + datetime.timedelta(days=20)
        checkout = checkin + datetime.timedelta(days=2)
        self._seed_booking(
            arrival_date=str(checkin),
            departure_date=str(checkout),
            rooms=[
                self._room(
                    checkin_date=str(checkin),
                    checkout_date=str(checkout),
                    days={
                        str(checkin): "76.50",
                        str(checkin + datetime.timedelta(days=1)): "76.50",
                    },
                )
            ],
        )
        self._import()
        self._commit()
        self.assertEqual(
            self._pending(), (checkin, checkin + datetime.timedelta(days=1))
        )


@tagged("post_install", "-at_install")
class TestChannexAriConcurrency(ChannexBookingCase):
    """Apart from the other cases, which send in their ``setUp``.

    A test class runs in one transaction, rolled back to a savepoint after each
    test, and a savepoint does not release an advisory lock: once a send ran in
    the class, its transaction holds the lock until the class ends.
    """

    def test_a_busy_send_steps_back_without_spending_a_retry(self):
        """Two sends of the same kind never talk to Channex at once."""
        # Exporting the room type in ``setUp`` left its availability pending.
        with trap_jobs():
            self.env.cr.precommit.run()
        lock = f"channex_ari:{self.backend.id}:availability"
        with odoo.sql_db.db_connect(self.env.cr.dbname).cursor() as other:
            # Never the waiting variant here: if anything held the lock, the
            # test would hang instead of failing.
            other.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", (lock,))
            self.assertTrue(other.fetchone()[0], "Nothing else holds the lock")
            with self.assertRaises(RetryableJobError) as caught:
                self.backend.channex_send_ari("availability")
        self.assertTrue(caught.exception.ignore_retry)
        self.assertFalse(self.server.calls_to("POST", "availability"))
