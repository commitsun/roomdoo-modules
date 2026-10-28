# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .test_ari_availability import ChannexAvailabilityCase

RULES = "channel_availability_rules"


class ChannexCapsCase(ChannexAvailabilityCase):
    """The availability case, with an agency mapped to a Channex channel."""

    def setUp(self):
        super().setUp()
        self.ota_sale_channel = self.env["pms.sale.channel"].create(
            {"name": "OTAs", "channel_type": "indirect"}
        )
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
        self.agency = self._make_agency("Booking agency")
        self.channel = self._map_channel("chn-1", "BookingCom", self.agency)
        self._publish_caps()
        self.server.calls.clear()

    # -- helpers -------------------------------------------------------------

    def _make_agency(self, name):
        return (
            self.env["res.partner"]
            .with_company(self.company)
            .create(
                {
                    "name": name,
                    "is_agency": True,
                    "sale_channel_id": self.ota_sale_channel.id,
                    "property_product_pricelist": self.pricelist.id,
                }
            )
        )

    def _map_channel(self, external_id, code, agency):
        self.server.seed(
            "channels",
            [
                {
                    "id": external_id,
                    "channel": code,
                    "title": f"{code} - Channex Property",
                    "is_active": True,
                    "properties": [self.property_uuid],
                }
            ],
        )
        channel = self.env["channel.channex.channel"].create(
            {
                "backend_id": self.backend.id,
                "external_id": external_id,
                "code": code,
                "title": f"{code} - Channex Property",
                "ota_id": self.env["channel.channex.ota"]._channex_of_code(code).id,
            }
        )
        channel.agency_id = agency
        return channel

    def _publish_caps(self):
        self._commit()
        return self.backend.channex_send_ari("channel_caps")

    def _rules(self):
        return sorted(
            self.server.store.get(RULES, []), key=lambda rule: rule["start_date"]
        )

    def _ours(self):
        return [rule for rule in self._rules() if "[roomdoo:" in rule["title"]]

    def _writes(self):
        return [
            call
            for call in self.server.calls
            if call[0] in ("POST", "PUT", "DELETE") and call[1].startswith(RULES)
        ]

    def _agency_rule(self, date_from, date_to, **values):
        return self.env["pms.inventory.rule"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "agency_id": self.agency.id,
                "date_from": date_from,
                "date_to": date_to,
                **values,
            }
        )

    def _days(self, days):
        return self.night + datetime.timedelta(days=days)

    def _reserve_through(self, agency, checkin, nights=1):
        return self.env["pms.reservation"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "checkin": checkin,
                "checkout": checkin + datetime.timedelta(days=nights),
                "partner_name": "A guest",
                "adults": 1,
                "agency_id": agency.id,
                "sale_channel_origin_id": self.ota_sale_channel.id,
                "pricelist_id": self.pricelist.id,
            }
        )


@tagged("post_install", "-at_install")
class TestChannexChannelCaps(ChannexCapsCase):
    """What becomes a rule, and how the rules follow the caps."""

    def test_a_cap_stricter_than_the_property_becomes_a_rule(self):
        self._agency_rule(self.night, self._days(2), max_avail=1)
        self._publish_caps()
        [rule] = self._rules()
        self.assertEqual(rule["type"], "max_availability")
        self.assertEqual(rule["value"], 1)
        self.assertEqual(rule["start_date"], str(self.night))
        self.assertEqual(rule["end_date"], str(self._days(2)))
        self.assertEqual(rule["affected_channels"], ["chn-1"])
        self.assertEqual(rule["affected_room_types"], [self.room_type_uuid])
        self.assertTrue(
            rule["title"].endswith(f"[roomdoo:{self.agency.id}:{self.room_type.id}]")
        )

    def test_a_cap_no_stricter_than_the_property_publishes_nothing(self):
        """Channex sells the lowest of the room type and the rule, and the room
        type already carries what the property declares."""
        self.env["pms.inventory.rule"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "date_from": self.night,
                "date_to": self._days(2),
                "max_avail": 1,
            }
        )
        self._agency_rule(self.night, self._days(2), max_avail=1)
        self._publish_caps()
        self.assertFalse(self._rules())

    def test_a_closed_agency_is_a_close_out(self):
        self._agency_rule(self.night, self.night, max_avail=0)
        self._publish_caps()
        [rule] = self._rules()
        self.assertEqual(rule["type"], "close_out")
        self.assertNotIn("value", rule)

    def test_a_sale_through_the_agency_spends_its_quota(self):
        """One rule of three nights becomes three: the night sold has one less
        left. The rule already there is rewritten, not deleted and made again."""
        self._agency_rule(self.night, self._days(2), quota=2)
        self._publish_caps()
        self.server.calls.clear()
        self._reserve_through(self.agency, self._days(1))
        self._publish_caps()
        self.assertEqual(
            [(r["start_date"], r["end_date"], r["value"]) for r in self._rules()],
            [
                (str(self.night), str(self.night), 2),
                (str(self._days(1)), str(self._days(1)), 1),
                (str(self._days(2)), str(self._days(2)), 2),
            ],
        )
        self.assertEqual(
            sorted(call[0] for call in self._writes()), ["POST", "POST", "PUT"]
        )

    def test_removing_the_cap_takes_the_rule_down(self):
        rule = self._agency_rule(self.night, self._days(2), max_avail=1)
        self._publish_caps()
        rule.unlink()
        self._publish_caps()
        self.assertFalse(self._rules())

    def test_shortening_the_cap_shortens_the_rule(self):
        rule = self._agency_rule(self.night, self._days(4), max_avail=1)
        self._publish_caps()
        rule.date_to = self._days(1)
        self._publish_caps()
        [published] = self._rules()
        self.assertEqual(published["end_date"], str(self._days(1)))

    def test_the_rules_the_hotel_made_are_left_alone(self):
        self.server.seed(
            RULES,
            [
                {
                    "id": "manual-1",
                    "title": "Christmas closed on Booking",
                    "type": "close_out",
                    "affected_channels": ["chn-1"],
                    "affected_room_types": [self.room_type_uuid],
                    "start_date": str(self.night),
                    "end_date": str(self._days(3)),
                    "property_id": self.property_uuid,
                }
            ],
        )
        rule = self._agency_rule(self.night, self._days(2), max_avail=1)
        self._publish_caps()
        rule.unlink()
        self._publish_caps()
        self.assertEqual([r["id"] for r in self._rules()], ["manual-1"])

    def test_a_rule_already_on_channex_is_not_sent_again(self):
        """Channex is asked what it has on every send, so a send that is
        retried, or a full publication, only sends what differs."""
        self._agency_rule(self.night, self._days(2), max_avail=1)
        self._publish_caps()
        self.server.calls.clear()
        self.backend._channex_publish_everything()
        self._publish_caps()
        self.assertFalse(self._writes())
        self.assertEqual(len(self._rules()), 1)

    def test_unmapping_the_channel_takes_its_rules_down(self):
        self._agency_rule(self.night, self._days(2), max_avail=1)
        self._publish_caps()
        self.channel.agency_id = False
        self._publish_caps()
        self.assertFalse(self._rules())

    def test_a_channel_gone_from_channex_takes_its_rules_down(self):
        """A channel removed from Channex is archived, not deleted, and an
        archived channel sells nothing."""
        self._agency_rule(self.night, self._days(2), max_avail=1)
        self._publish_caps()
        self.channel.active = False
        self._publish_caps()
        self.assertFalse(self._rules())

    def test_the_channels_of_one_agency_share_its_rules(self):
        """A hotel can connect the same OTA twice; both sell the same cap."""
        self._map_channel("chn-2", "BookingCom", self.agency)
        self._agency_rule(self.night, self.night, max_avail=1)
        self._publish_caps()
        [rule] = self._rules()
        self.assertEqual(sorted(rule["affected_channels"]), ["chn-1", "chn-2"])

    def test_with_exports_disabled_nothing_leaves(self):
        self.backend.export_disabled = True
        self._agency_rule(self.night, self._days(2), max_avail=1)
        self._publish_caps()
        self.assertFalse(self._rules())
        self.assertFalse(self._writes())

    def test_what_channex_refuses_is_kept_and_the_rest_goes_on(self):
        """A rule refused must not take the others down with it."""
        other_type, _room = self._second_room_type()
        self.server.store["channels"] = []
        self._agency_rule(self.night, self.night, max_avail=1)
        self.env["pms.inventory.rule"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": other_type.id,
                "agency_id": self.agency.id,
                "date_from": self.night,
                "date_to": self.night,
                "max_avail": 0,
            }
        )
        self._publish_caps()
        warnings = self.backend.channex_ari_warning_ids.filtered(
            lambda warning: warning.kind == "channel_caps"
        )
        self.assertEqual(len(warnings), 2)
        self.assertIn("affected_channels", warnings[0].warnings)


@tagged("post_install", "-at_install")
class TestChannexChannelCapsStaging(ChannexCapsCase):
    """Which changes leave the caps of an agency pending."""

    def _cap(self, agency=None, room_type=None):
        cap = self.env["channel.channex.inventory.cap"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("agency_id", "=", (agency or self.agency).id),
                ("room_type_id", "=", (room_type or self.room_type).id),
            ]
        )
        cap.invalidate_recordset()
        return cap

    def _pending_cap(self, agency=None, room_type=None):
        cap = self._cap(agency, room_type)
        return cap.channex_ari_pending_from, cap.channex_ari_pending_to

    def test_a_sale_through_any_channel_leaves_the_night_pending(self):
        """The quota of the property is spent by any sale, and it decides
        whether the agency is capped any lower."""
        self._reserve(self.night, room=self.rooms[0])
        self._commit()
        self.assertEqual(self._pending_cap(), (self.night, self.night))

    def test_synchronising_everything_leaves_every_night_pending(self):
        """Room types are published again as they are brought in line, but what
        the agencies set aside is only published with the full sync."""
        with trap_jobs():
            self.backend.action_channex_sync_all()
        self._commit()
        self.assertEqual(self._pending_cap(), (self.first, self.last))

    def test_a_cap_of_another_agency_leaves_this_one_alone(self):
        other = self._make_agency("Another agency")
        self._map_channel("chn-2", "Expedia", other)
        self._publish_caps()
        self.env["pms.inventory.rule"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "agency_id": other.id,
                "date_from": self.night,
                "date_to": self.night,
                "max_avail": 1,
            }
        )
        self._commit()
        self.assertEqual(self._pending_cap(other), (self.night, self.night))
        self.assertEqual(self._pending_cap(), (False, False))

    def test_mapping_a_channel_leaves_the_whole_window_pending(self):
        other = self._make_agency("Another agency")
        self._map_channel("chn-2", "Expedia", other)
        self._commit()
        self.assertEqual(self._pending_cap(other), (self.first, self.last))

    def test_moving_the_agency_to_another_sale_channel_leaves_it_pending(self):
        self.agency.sale_channel_id = self.env["pms.sale.channel"].create(
            {"name": "Other OTAs", "channel_type": "indirect"}
        )
        self._commit()
        self.assertEqual(self._pending_cap(), (self.first, self.last))

    def test_the_inventory_a_room_type_falls_back_on_leaves_it_pending(self):
        self.room_type.default_quota = 3
        self._commit()
        self.assertEqual(self._pending_cap(), (self.first, self.last))

    def test_an_imported_booking_leaves_its_nights_pending(self):
        """A booking from an OTA spends the quota of that very agency, and the
        import silences every listener."""
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
            self._pending_cap(), (checkin, checkin + datetime.timedelta(days=1))
        )
