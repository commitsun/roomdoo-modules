# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import ChannexConnectorCase


@tagged("post_install", "-at_install")
class TestChannexChannels(ChannexConnectorCase):
    """Channels are created by the hotel inside Channex; Odoo discovers them
    and adds the one thing Channex cannot know: which partner they are."""

    def setUp(self):
        super().setUp()
        self.env["channel.channex.pms.property"].export_record(
            self.backend, self.pms_property
        )
        self.property_uuid = self.server.store["properties"][0]["id"]
        # A plain partner: what makes one a valid agency is pms business, and
        # the field domain is a UI hint the ORM does not enforce.
        self.agency = self.env["res.partner"].create({"name": "An agency"})

    def _seed_channel(self, code="BookingCom", uuid="c1", properties=None, **values):
        self.server.seed(
            "channels",
            [
                {
                    "id": uuid,
                    "channel": code,
                    "title": f"{code} - Channex Property",
                    "is_active": True,
                    "properties": [
                        self.property_uuid if properties is None else properties
                    ],
                    "settings": {"hotel_id": "12152494"},
                    **values,
                }
            ],
        )

    def _channels(self, backend=None):
        return (
            self.env["channel.channex.channel"]
            .with_context(active_test=False)
            .search([("backend_id", "=", (backend or self.backend).id)])
        )

    # -- discovery ---------------------------------------------------------

    def test_sync_brings_in_a_channel_created_on_channex(self):
        self._seed_channel()
        self.backend.channex_sync_channels()
        channel = self._channels()
        self.assertEqual(len(channel), 1)
        self.assertEqual(channel.external_id, "c1")
        self.assertEqual(channel.code, "BookingCom")
        self.assertEqual(channel.title, "BookingCom - Channex Property")
        self.assertEqual(channel.hotel_id, "12152494")
        self.assertTrue(channel.channex_active)
        self.assertTrue(channel.sync_date)

    def test_a_channel_of_another_property_is_not_brought_in(self):
        """One Channex account holds the channels of every hotel in it."""
        self._seed_channel(uuid="c1")
        self._seed_channel(uuid="c2", properties="99999999-9999-9999-9999-999999999999")
        self.backend.channex_sync_channels()
        self.assertEqual(self._channels().mapped("external_id"), ["c1"])

    def test_syncing_twice_updates_instead_of_duplicating(self):
        self._seed_channel()
        self.backend.channex_sync_channels()
        self.server.store["channels"][0]["is_active"] = False
        self.server.store["channels"][0]["title"] = "Renamed"
        self.backend.channex_sync_channels()
        channel = self._channels()
        self.assertEqual(len(channel), 1)
        self.assertEqual(channel.title, "Renamed")
        self.assertFalse(channel.channex_active)

    def test_the_same_ota_twice_is_two_channels_and_one_mapping(self):
        """A hotel can sell two of its OTA accounts through one property."""
        self._seed_channel(uuid="c1")
        self._seed_channel(uuid="c2")
        self.server.store["channels"][1]["settings"] = {"hotel_id": "7777"}
        self.backend.channex_sync_channels()
        channels = self._channels()
        self.assertEqual(len(channels), 2)
        self.assertEqual(len(channels.ota_id), 1)
        channels[0].agency_id = self.agency
        self.assertEqual(channels[1].agency_id, self.agency)

    def test_a_channel_gone_from_channex_keeps_its_mapping(self):
        """Bookings already attributed to it still have to resolve."""
        self._seed_channel()
        self.backend.channex_sync_channels()
        self._channels().agency_id = self.agency
        self.server.store["channels"] = []
        self.backend.channex_sync_channels()
        channel = self._channels()
        self.assertFalse(channel.active)
        self.assertEqual(channel.agency_id, self.agency)

    def test_sync_reports_what_it_did(self):
        self._seed_channel()
        self.assertEqual(
            self.backend.channex_sync_channels(), {"total": 1, "unmapped": 1}
        )

    # -- the guided screen -------------------------------------------------

    def test_it_opens_on_connecting_when_there_is_nothing_to_map(self):
        self.assertEqual(
            self.backend.action_open_channex_channels()["params"]["step"], "connect"
        )

    def test_it_opens_on_mapping_once_there_is_something_to_map(self):
        """What makes it a screen to come back to rather than a one-shot wizard."""
        self._seed_channel()
        self.backend.channex_sync_channels()
        self.assertEqual(
            self.backend.action_open_channex_channels()["params"]["step"], "map"
        )

    # -- mapping -----------------------------------------------------------

    def test_no_partner_is_invented_for_an_unknown_channel(self):
        self._seed_channel(code="SomethingNew")
        self.backend.channex_sync_channels()
        channel = self._channels()
        self.assertFalse(channel.agency_id)
        self.assertEqual(channel.ota_id.code, "SomethingNew")
        self.assertEqual(self.backend.unmapped_channel_count, 1)

    def test_mapping_the_agency_clears_the_warning(self):
        self._seed_channel()
        self.backend.channex_sync_channels()
        self.assertEqual(self.backend.unmapped_channel_count, 1)
        self._channels().agency_id = self.agency
        self.assertEqual(self.backend.unmapped_channel_count, 0)

    def test_a_channel_gone_from_channex_stops_warning(self):
        self._seed_channel()
        self.backend.channex_sync_channels()
        self.server.store["channels"] = []
        self.backend.channex_sync_channels()
        self.assertEqual(self.backend.unmapped_channel_count, 0)

    def test_the_agency_is_shared_by_every_property(self):
        """Booking.com is the same partner in all of the hotels, so the second
        one does not have to be mapped by hand."""
        self._seed_channel(uuid="c1")
        self.backend.channex_sync_channels()
        self._channels().agency_id = self.agency

        other_property = self.env["pms.property"].create(
            {
                "name": "Channex Second",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist.id,
                "tz": "Europe/Madrid",
            }
        )
        other_backend = self.env["channel.channex.backend"].create(
            {
                "name": "Channex Backend 2",
                "pms_property_id": other_property.id,
                "backend_type_id": self.backend_type.parent_id.id,
                "api_key": "test-key",
                "environment": "staging",
                "group_id": self.backend.group_id,
            }
        )
        self.env["channel.channex.pms.property"].export_record(
            other_backend, other_property
        )
        second_uuid = self.server.store["properties"][-1]["id"]
        self._seed_channel(uuid="c2", properties=second_uuid)
        other_backend.channex_sync_channels()

        self.assertEqual(self._channels(other_backend).agency_id, self.agency)
        self.assertEqual(other_backend.unmapped_channel_count, 0)

    # -- pushing the rate logic --------------------------------------------

    def _mapped_channel(self, mappings, agency=True):
        """A discovered channel whose mappings the hotel already built."""
        self._seed_channel(rate_plans=mappings)
        self.backend.channex_sync_channels()
        channel = self._channels()
        if agency:
            channel.agency_id = self.agency
        return channel

    def _remote_settings(self):
        return self.server.store["channels"][0]["rate_plans"][0]["settings"]

    def test_every_rate_logic_reaches_the_mapping(self):
        channel = self._mapped_channel(
            [{"rate_plan_id": "rp1", "settings": {"room_code": "R1"}}]
        )
        for modifier_type, expected in (
            ("increase_amount", "increase_by_amount"),
            ("decrease_amount", "decrease_by_amount"),
            ("increase_percent", "increase_by_percent"),
            ("decrease_percent", "decrease_by_percent"),
        ):
            self.agency.write(
                {
                    "ota_price_modifier_type": modifier_type,
                    "ota_price_modifier_value": 10,
                }
            )
            channel.action_push_price_modifier()
            self.assertEqual(
                self._remote_settings()["derived_option"],
                {"rate": [[expected, "10"]]},
            )

    def test_the_hotels_own_mapping_survives_the_push(self):
        """Odoo reaches into one key of a mapping the hotel built; the room and
        rate codes in it are none of its business."""
        channel = self._mapped_channel(
            [
                {
                    "rate_plan_id": "rp1",
                    "settings": {"room_code": "R1", "rate_code": "RT1"},
                }
            ]
        )
        self.agency.write(
            {
                "ota_price_modifier_type": "increase_percent",
                "ota_price_modifier_value": 12.5,
            }
        )
        channel.action_push_price_modifier()
        settings = self._remote_settings()
        self.assertEqual(settings["room_code"], "R1")
        self.assertEqual(settings["rate_code"], "RT1")
        self.assertEqual(
            settings["derived_option"], {"rate": [["increase_by_percent", "12.5"]]}
        )

    def test_clearing_the_rate_logic_clears_it_on_channex(self):
        """Cleared in Odoo means cleared there, not quietly left behind."""
        channel = self._mapped_channel(
            [
                {
                    "rate_plan_id": "rp1",
                    "settings": {
                        "room_code": "R1",
                        "derived_option": {"rate": [["increase_by_percent", "10"]]},
                    },
                }
            ]
        )
        channel.action_push_price_modifier()
        settings = self._remote_settings()
        self.assertNotIn("derived_option", settings)
        self.assertEqual(settings["room_code"], "R1")

    def test_a_channel_without_an_agency_is_not_pushed(self):
        self._seed_channel(rate_plans=[{"rate_plan_id": "rp1", "settings": {}}])
        self.backend.channex_sync_channels()
        channel = self._channels()
        calls_before = len(self.server.calls)
        channel.action_push_price_modifier()
        self.assertEqual(len(self.server.calls), calls_before)

    def test_a_channel_the_hotel_has_not_mapped_is_not_written(self):
        channel = self._mapped_channel([])
        self.agency.write(
            {
                "ota_price_modifier_type": "increase_percent",
                "ota_price_modifier_value": 10,
            }
        )
        channel.action_push_price_modifier()
        self.assertFalse(self.server.calls_to("PUT", "channels"))

    def test_pushing_what_is_already_there_writes_nothing(self):
        channel = self._mapped_channel(
            [{"rate_plan_id": "rp1", "settings": {"room_code": "R1"}}]
        )
        self.agency.write(
            {
                "ota_price_modifier_type": "increase_percent",
                "ota_price_modifier_value": 10,
            }
        )
        channel.action_push_price_modifier()
        channel.action_push_price_modifier()
        self.assertEqual(len(self.server.calls_to("PUT", "channels")), 1)
