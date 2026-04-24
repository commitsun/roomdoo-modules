# Copyright 2024 Commit [Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import datetime
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import TestTelevesConnector


def _mock_response(status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    return mock


@tagged("post_install", "-at_install")
class TestCheckin(TestTelevesConnector):
    def test_checkin_sends_request(self):
        """Setting reservation state to onboard triggers POST to checkin."""
        reservation = self._create_reservation()
        checkin_partner = self._create_checkin_partner(reservation)
        checkin_partner.write(
            {
                "firstname": "Donald",
                "lastname": "Duck",
                "state": "onboard",
            }
        )

        with patch("requests.post", return_value=_mock_response()) as mock_post:
            reservation.write({"state": "onboard"})

        self.assertTrue(mock_post.called, "requests.post should have been called")
        call_kwargs = mock_post.call_args
        url = call_kwargs[0][0]
        self.assertIn("/pms/checkin", url)
        payload = call_kwargs[1]["json"]
        self.assertEqual(payload["roomNumber"], 5000)
        self.assertEqual(payload["reservationNumber"], reservation.name)
        self.assertEqual(payload["guestName"], "Donald")
        self.assertEqual(payload["guestSurname"], "Duck")

    def test_checkin_sets_televes_fields(self):
        """After successful checkin, televes_checkin_sent and room are set."""
        reservation = self._create_reservation()
        checkin_partner = self._create_checkin_partner(reservation)
        checkin_partner.write({"state": "onboard"})

        with patch("requests.post", return_value=_mock_response()):
            reservation.write({"state": "onboard"})

        self.assertTrue(reservation.televes_checkin_sent)
        self.assertEqual(reservation.televes_current_room_id, self.room1)

    def test_checkin_disabled_property_no_request(self):
        """No request is sent when Televes is disabled on the property."""
        self.pms_property1.televes_enabled = False
        reservation = self._create_reservation()
        checkin_partner = self._create_checkin_partner(reservation)
        checkin_partner.write({"state": "onboard"})

        with patch("requests.post") as mock_post:
            reservation.write({"state": "onboard"})

        self.assertFalse(mock_post.called)
        self.pms_property1.televes_enabled = True  # restore

    def test_checkin_not_sent_twice(self):
        """televes_checkin_sent=True prevents re-sending checkin."""
        reservation = self._create_reservation()
        checkin_partner = self._create_checkin_partner(reservation)
        checkin_partner.write({"state": "onboard"})

        # Simulate reservation where checkin was already sent (e.g. prior sync)
        reservation.with_context(televes_skip=True).write(
            {"televes_checkin_sent": True}
        )

        with patch("requests.post") as mock_post:
            reservation.write({"state": "onboard"})

        self.assertFalse(mock_post.called, "Checkin should not be sent again")


@tagged("post_install", "-at_install")
class TestCheckout(TestTelevesConnector):
    def _setup_onboard_reservation(self):
        """Create a checkout-ready reservation tracked in Televes."""
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        reservation = self._create_reservation(
            checkin=yesterday, checkout=datetime.date.today()
        )
        checkin_partner = self._create_checkin_partner(reservation)
        arrival_dt = datetime.datetime.combine(yesterday, datetime.time(14, 0))
        checkin_partner.write({"state": "onboard", "arrival": arrival_dt})
        reservation.with_context(televes_skip=True).write(
            {
                "state": "onboard",
                "televes_checkin_sent": True,
                "televes_current_room_id": self.room1.id,
            }
        )
        return reservation

    def test_checkout_sends_request(self):
        """action_reservation_checkout() triggers POST to checkout endpoint."""
        reservation = self._setup_onboard_reservation()

        with patch("requests.post", return_value=_mock_response()) as mock_post:
            reservation.action_reservation_checkout()

        self.assertTrue(mock_post.called)
        call_kwargs = mock_post.call_args
        url = call_kwargs[0][0]
        self.assertIn("/pms/checkout", url)
        payload = call_kwargs[1]["json"]
        self.assertEqual(payload["roomNumber"], 5000)

    def test_checkout_resets_televes_sent_flag(self):
        """After checkout, televes_checkin_sent is reset to False."""
        reservation = self._setup_onboard_reservation()

        with patch("requests.post", return_value=_mock_response()):
            reservation.action_reservation_checkout()

        self.assertFalse(reservation.televes_checkin_sent)

    def test_checkout_not_sent_if_no_televes_checkin(self):
        """No checkout request if checkin was never sent to Televes."""
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        reservation = self._create_reservation(
            checkin=yesterday, checkout=datetime.date.today()
        )
        checkin_partner = self._create_checkin_partner(reservation)
        arrival_dt = datetime.datetime.combine(yesterday, datetime.time(14, 0))
        checkin_partner.write({"state": "onboard", "arrival": arrival_dt})
        reservation.with_context(televes_skip=True).write({"state": "onboard"})
        # televes_checkin_sent remains False

        with patch("requests.post") as mock_post:
            reservation.action_reservation_checkout()

        self.assertFalse(mock_post.called)


@tagged("post_install", "-at_install")
class TestChangeData(TestTelevesConnector):
    def _setup_onboard_reservation(self):
        reservation = self._create_reservation()
        checkin_partner = self._create_checkin_partner(reservation)
        checkin_partner.write({"state": "onboard"})
        reservation.with_context(televes_skip=True).write(
            {
                "state": "onboard",
                "televes_checkin_sent": True,
                "televes_current_room_id": self.room1.id,
            }
        )
        return reservation

    def test_changedata_on_checkout_date_change(self):
        """Changing checkout date on onboard reservation triggers changedata."""
        reservation = self._setup_onboard_reservation()
        new_checkout = datetime.date.today() + datetime.timedelta(days=3)

        with patch("requests.post", return_value=_mock_response()) as mock_post:
            reservation.write({"checkout": new_checkout})

        self.assertTrue(mock_post.called)
        call_kwargs = mock_post.call_args
        url = call_kwargs[0][0]
        self.assertIn("/pms/changedata", url)
        payload = call_kwargs[1]["json"]
        self.assertEqual(
            payload["guestDepartureDate"], new_checkout.strftime("%Y-%m-%d")
        )

    def test_no_changedata_if_not_onboard(self):
        """No changedata request when reservation is not in onboard state."""
        reservation = self._create_reservation()
        new_checkout = datetime.date.today() + datetime.timedelta(days=3)

        with patch("requests.post") as mock_post:
            reservation.write({"checkout": new_checkout})

        self.assertFalse(mock_post.called)

    def test_no_changedata_if_televes_not_sent(self):
        """No changedata if televes_checkin_sent is False."""
        reservation = self._create_reservation()
        checkin_partner = self._create_checkin_partner(reservation)
        checkin_partner.write({"state": "onboard"})
        reservation.with_context(televes_skip=True).write({"state": "onboard"})
        new_checkout = datetime.date.today() + datetime.timedelta(days=3)

        with patch("requests.post") as mock_post:
            reservation.write({"checkout": new_checkout})

        self.assertFalse(mock_post.called)


@tagged("post_install", "-at_install")
class TestChangeRoom(TestTelevesConnector):
    def _setup_onboard_reservation(self):
        reservation = self._create_reservation()
        checkin_partner = self._create_checkin_partner(reservation)
        checkin_partner.write({"state": "onboard"})
        reservation.with_context(televes_skip=True).write(
            {
                "state": "onboard",
                "televes_checkin_sent": True,
                "televes_current_room_id": self.room1.id,
            }
        )
        return reservation

    def test_changeroom_on_line_write(self):
        """Changing room_id on a reservation line triggers changeroom."""
        reservation = self._setup_onboard_reservation()
        today_line = reservation.reservation_line_ids.filtered(
            lambda line: line.date == datetime.date.today()
        )
        self.assertTrue(today_line, "No reservation line found for today")

        with patch("requests.post", return_value=_mock_response()) as mock_post:
            today_line.write({"room_id": self.room2.id})

        self.assertTrue(mock_post.called)
        call_kwargs = mock_post.call_args
        url = call_kwargs[0][0]
        self.assertIn("/pms/changeroom", url)
        payload = call_kwargs[1]["json"]
        self.assertEqual(payload["roomNumber"], 5000)
        self.assertEqual(payload["destRoomNumber"], 5001)

    def test_changeroom_updates_current_room(self):
        """After changeroom, televes_current_room_id is updated."""
        reservation = self._setup_onboard_reservation()
        today_line = reservation.reservation_line_ids.filtered(
            lambda line: line.date == datetime.date.today()
        )
        with patch("requests.post", return_value=_mock_response()):
            today_line.write({"room_id": self.room2.id})

        self.assertEqual(reservation.televes_current_room_id, self.room2)

    def test_future_line_change_does_not_send_changeroom(self):
        """Changing room on a future line must NOT send changeroom immediately.

        The guest is currently in room1 (today's night). Modifying the room
        for a future night should be deferred to the cron, not sent now.
        """
        reservation = self._create_reservation(
            checkout=datetime.date.today() + datetime.timedelta(days=3)
        )
        checkin_partner = self._create_checkin_partner(reservation)
        checkin_partner.write({"state": "onboard"})
        reservation.with_context(televes_skip=True).write(
            {
                "state": "onboard",
                "televes_checkin_sent": True,
                "televes_current_room_id": self.room1.id,
            }
        )
        future_line = reservation.reservation_line_ids.filtered(
            lambda line: line.date > datetime.date.today()
        )
        self.assertTrue(future_line, "No future reservation line found")

        with patch("requests.post") as mock_post:
            future_line[0].write({"room_id": self.room2.id})

        self.assertFalse(
            mock_post.called,
            "changeroom must not be sent for a future line change",
        )

    def test_cron_detects_room_change(self):
        """Cron detects pre-planned room for today differs from Televes."""
        reservation = self._setup_onboard_reservation()
        today_line = reservation.reservation_line_ids.filtered(
            lambda line: line.date == datetime.date.today()
        )
        # Simulate a pre-planned room change in the DB (skip Televes notify)
        today_line.with_context(televes_skip=True).write({"room_id": self.room2.id})
        reservation.with_context(televes_skip=True).write(
            {"televes_current_room_id": self.room1.id}
        )

        with patch("requests.post", return_value=_mock_response()) as mock_post:
            self.pms_property1.televes_cron_check_room_changes()

        self.assertTrue(mock_post.called)
        call_kwargs = mock_post.call_args
        url = call_kwargs[0][0]
        self.assertIn("/pms/changeroom", url)


@tagged("post_install", "-at_install")
class TestErrorHandling(TestTelevesConnector):
    def _setup_onboard_reservation(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        reservation = self._create_reservation(
            checkin=yesterday, checkout=datetime.date.today()
        )
        checkin_partner = self._create_checkin_partner(reservation)
        arrival_dt = datetime.datetime.combine(yesterday, datetime.time(14, 0))
        checkin_partner.write({"state": "onboard", "arrival": arrival_dt})
        reservation.with_context(televes_skip=True).write(
            {
                "state": "onboard",
                "televes_checkin_sent": True,
                "televes_current_room_id": self.room1.id,
            }
        )
        return reservation

    def test_api_error_posts_chatter_message(self):
        """When Televes API fails, a note is posted on the reservation chatter."""
        reservation = self._setup_onboard_reservation()

        with patch("requests.post", side_effect=Exception("Connection refused")):
            reservation.action_reservation_checkout()

        messages = reservation.message_ids.filtered(
            lambda m: "Televes API error" in (m.body or "")
        )
        self.assertTrue(messages, "Expected a Televes error note in the chatter")

    def test_checkout_does_not_raise_on_api_error(self):
        """Checkout completes even if Televes API is unreachable."""
        reservation = self._setup_onboard_reservation()

        with patch("requests.post", side_effect=Exception("Connection refused")):
            try:
                reservation.action_reservation_checkout()
            except Exception:
                self.fail(
                    "action_reservation_checkout() raised an exception" " on API error"
                )

        self.assertEqual(reservation.state, "done")
