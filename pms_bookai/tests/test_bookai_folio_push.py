from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import requests

from odoo import fields
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import TestBookaiCommon

SESSION_PATH = "odoo.addons.pms_bookai.models.pms_folio.requests.Session"
FOLIO_LOGGER = "odoo.addons.pms_bookai.models.pms_folio"
LOG_CLASS_PATH = "odoo.addons.pms_bookai.models.pms_notification_log.PmsNotificationLog"


def _response(status_code):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    return response


@tagged("post_install", "-at_install")
class TestBookaiFolioPush(TestBookaiCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "BookAI Double",
                "default_code": "BKD",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": f"BookAI {number}",
                    "short_name": f"B{number}",
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for number in (101, 102, 103, 104)
            ]
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "BookAI direct", "channel_type": "direct"}
        )
        cls.guest = cls.env["res.partner"].create({"name": "BookAI Guest"})
        # Folios the database already had queued must not reach the mocks.
        cls.env["pms.folio"].search(
            [("bookai_push_pending", "=", True)]
        )._bookai_mark_pushed()

    def _reservation(self, folio=None, offset=1, nights=2):
        checkin = fields.Date.today() + timedelta(days=offset)
        vals = {
            "pms_property_id": self.pms_property1.id,
            "room_type_id": self.room_type.id,
            "partner_id": self.guest.id,
            "sale_channel_origin_id": self.sale_channel.id,
            "checkin": checkin,
            "checkout": checkin + timedelta(days=nights),
        }
        if folio:
            vals["folio_id"] = folio.id
        return self.env["pms.reservation"].create(vals)

    def _set_state(self, reservations, state):
        # Skip the PMS state machine: only the derived status is under test.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE pms_reservation SET state = %s WHERE id IN %s",
            (state, tuple(reservations.ids)),
        )
        reservations.invalidate_recordset(["state"], flush=False)

    def _known_folio(self):
        folio = self._reservation().folio_id
        folio._bookai_mark_known()
        return folio

    def _run_cron(self, *responses):
        with patch(SESSION_PATH) as session_class:
            http = session_class.return_value.__enter__.return_value
            http.patch.side_effect = list(responses)
            self.env["pms.folio"]._cron_bookai_push_folios()
        return session_class, http

    # ---------------------------------------------------------------
    # Stay status and payload
    # ---------------------------------------------------------------
    def test_stay_status_follows_the_most_current_reservation(self):
        first = self._reservation()
        folio = first.folio_id
        second = self._reservation(folio=folio)
        cases = [
            (("onboard", "done"), "onboard"),
            (("departure_delayed", "cancel"), "onboard"),
            (("confirm", "done"), "confirm"),
            (("arrival_delayed", "cancel"), "confirm"),
            (("draft", "done"), "draft"),
            (("done", "cancel"), "done"),
            (("cancel", "cancel"), "cancel"),
        ]
        for (first_state, second_state), expected in cases:
            with self.subTest(states=(first_state, second_state)):
                self._set_state(first, first_state)
                self._set_state(second, second_state)
                self.assertEqual(folio._bookai_stay_status(), expected)

    def test_a_folio_without_reservations_uses_its_own_state(self):
        self.assertEqual(self.folio._bookai_stay_status(), "draft")

    def test_payload_has_the_dates_and_the_pending_amount(self):
        reservation = self._reservation(offset=3, nights=4)
        folio = reservation.folio_id

        payload = folio._bookai_folio_payload()

        self.assertEqual(payload["checkin_date"], str(reservation.checkin))
        self.assertEqual(payload["checkout_date"], str(reservation.checkout))
        self.assertEqual(payload["pending_payment_currency"], folio.currency_id.name)
        self.assertEqual(
            payload["pending_payment_amount"],
            folio.currency_id.round(folio.pending_amount),
        )
        self.assertIn(payload["status"], ("draft", "confirm"))

    # ---------------------------------------------------------------
    # Marking
    # ---------------------------------------------------------------
    def test_only_folios_bookai_knows_are_marked_when_they_change(self):
        known = self._reservation()
        unknown = self._reservation()
        known.folio_id._bookai_mark_known()
        known.folio_id._bookai_mark_pushed()
        self.assertFalse(known.folio_id.bookai_push_pending)

        (known | unknown).action_cancel()

        self.assertTrue(known.folio_id.bookai_push_pending)
        self.assertFalse(unknown.folio_id.bookai_push_pending)
        self.assertFalse(unknown.folio_id.bookai_known)

    def test_a_new_reservation_marks_its_known_folio(self):
        folio = self._known_folio()
        folio._bookai_mark_pushed()

        self._reservation(folio=folio, offset=5)

        self.assertTrue(folio.bookai_push_pending)

    def test_a_sent_template_makes_its_folio_known(self):
        folio = self._reservation().folio_id
        log = self.env["pms.notification.log"].create(
            {
                "name": "BookAI folio push log",
                "template_id": self.bookai_template.id,
                "channel": "bookai_whatsapp",
                "origin_model": "pms.folio",
                "origin_res_id": folio.id,
                "property_id": self.pms_property1.id,
                "recipient_mode": "template",
            }
        )
        log.write(
            {
                "state": "pending",
                "whatsapp_phone": "+34600000000",
                "whatsapp_country": "ES",
                "bookai_origin_folio_id": folio.id,
            }
        )
        with patch(
            f"{LOG_CLASS_PATH}._bookai_check_property_mode",
            return_value=(True, False, False),
        ), patch(f"{LOG_CLASS_PATH}._bookai_prepare_payload_fields"), patch(
            f"{LOG_CLASS_PATH}._send_bookai_request",
            return_value={"message_id": "msg-1", "status": "sent"},
        ):
            log.action_send_bookai_whatsapp()

        self.assertEqual(log.state, "sent")
        self.assertTrue(folio.bookai_known)
        self.assertTrue(folio.bookai_push_pending)

    # ---------------------------------------------------------------
    # Cron
    # ---------------------------------------------------------------
    def test_cron_pushes_a_known_folio_and_clears_the_mark(self):
        folio = self._known_folio()

        _session, http = self._run_cron(_response(200))

        http.patch.assert_called_once()
        self.assertEqual(
            http.patch.call_args.args[0],
            "https://bookai.test/api/v1/folios/" + quote(folio.name, safe="/"),
        )
        kwargs = http.patch.call_args.kwargs
        self.assertEqual(kwargs["json"], folio._bookai_folio_payload())
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token-123")
        self.assertFalse(folio.bookai_push_pending)
        self.assertTrue(folio.bookai_known)

    def test_cron_forgets_a_folio_bookai_does_not_have(self):
        folio = self._known_folio()

        self._run_cron(_response(404))

        self.assertFalse(folio.bookai_push_pending)
        self.assertFalse(folio.bookai_known)

    @mute_logger(FOLIO_LOGGER)
    def test_cron_keeps_the_mark_when_bookai_fails(self):
        first = self._known_folio()
        second = self._known_folio()

        _session, http = self._run_cron(_response(500), _response(200))

        http.patch.assert_called_once()
        self.assertTrue(first.bookai_push_pending)
        self.assertTrue(second.bookai_push_pending)

    @mute_logger(FOLIO_LOGGER)
    def test_cron_stops_when_bookai_is_unreachable(self):
        folio = self._known_folio()

        self._run_cron(requests.ConnectionError("unreachable"))

        self.assertTrue(folio.bookai_push_pending)
        self.assertTrue(folio.bookai_known)

    def test_cron_does_nothing_without_bookai(self):
        folio = self._known_folio()
        self.icp.set_param("pms_bookai.api_endpoint", "")

        session_class, _http = self._run_cron()

        session_class.assert_not_called()
        self.assertTrue(folio.bookai_push_pending)
