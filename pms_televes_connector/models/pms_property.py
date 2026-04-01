# Copyright 2024 Commit [Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

import requests

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

_CHECKIN_ENDPOINT = "/pms/checkin"
_CHANGEDATA_ENDPOINT = "/pms/changedata"
_CHANGEROOM_ENDPOINT = "/pms/changeroom"
_CHECKOUT_ENDPOINT = "/pms/checkout"


class PmsProperty(models.Model):
    _inherit = "pms.property"

    televes_enabled = fields.Boolean(
        string="Enable Televes Integration",
        help="Enable the integration with the Televes/Arantia ATV3 IPTV system",
    )
    televes_url = fields.Char(
        string="Televes URL",
        help=(
            "Base URL of the Televes PMS Adapter API "
            "(e.g. http://atv3demo.arantia.com:8094)"
        ),
    )
    televes_base_path = fields.Char(
        string="Televes Base Path",
        default="/pms-adapter-backend-service",
        help="Base path of the Televes PMS Adapter API",
    )
    televes_pms_user = fields.Char(
        string="Televes PMS User",
        help="Username for Televes PMS Adapter API authentication",
    )
    televes_pms_password = fields.Char(
        string="Televes PMS Password",
        help="Password for Televes PMS Adapter API authentication",
    )

    def _televes_request(self, endpoint, payload):
        """Make a POST request to the Televes PMS Adapter API.

        Returns True on success, False on failure.
        Does not raise exceptions; errors are logged.
        """
        self.ensure_one()
        if not self.televes_enabled or not self.televes_url:
            return False
        url = self.televes_url.rstrip("/") + (self.televes_base_path or "") + endpoint
        auth = (self.televes_pms_user or "", self.televes_pms_password or "")
        try:
            response = requests.post(url, json=payload, auth=auth, timeout=10)
            response.raise_for_status()
            _logger.info("Televes API [%s]: success", endpoint)
            return True
        except Exception as exc:
            _logger.error("Televes API [%s] error: %s", endpoint, exc)
            return False

    def _televes_get_today_room(self, reservation):
        """Return today's assigned room for the reservation."""
        today = fields.Date.today()
        today_line = reservation.reservation_line_ids.filtered(
            lambda line: line.date == today
        )
        return today_line.room_id if today_line else reservation.preferred_room_id

    def _televes_build_guest_payload(self, reservation):
        """Build the common guest data payload for checkin/changedata.

        Returns a dict or None if the room has no televes_room_number configured.
        """
        room = reservation.televes_current_room_id or self._televes_get_today_room(
            reservation
        )
        if not room or not room.televes_room_number:
            _logger.warning(
                "Televes: room %s has no televes_room_number configured, skipping",
                room.name if room else "unknown",
            )
            return None

        # Resolve language ISO code from folio lang
        guest_language = None
        if reservation.folio_id.lang:
            lang_rec = self.env["res.lang"].search(
                [("code", "=", reservation.folio_id.lang)], limit=1
            )
            if lang_rec:
                guest_language = lang_rec.iso_code

        payload = {
            "roomNumber": room.televes_room_number,
            "reservationNumber": reservation.name,
            "guestArrivalDate": reservation.checkin.strftime("%Y-%m-%d"),
            "guestDepartureDate": reservation.checkout.strftime("%Y-%m-%d"),
        }

        checkin_partner = reservation.checkin_partner_ids.sorted("id")[:1]
        if checkin_partner:
            if checkin_partner.firstname:
                payload["guestName"] = checkin_partner.firstname
            if checkin_partner.lastname:
                payload["guestSurname"] = checkin_partner.lastname

        if guest_language:
            payload["guestLanguage"] = guest_language

        return payload

    def _televes_send_checkin(self, reservation):
        """Send a check-in event to Televes."""
        self.ensure_one()
        payload = self._televes_build_guest_payload(reservation)
        if payload is None:
            return False
        success = self._televes_request(_CHECKIN_ENDPOINT, payload)
        if not success:
            reservation.message_post(
                body=_("Televes API error: check-in notification could not be sent.")
            )
        return success

    def _televes_send_changedata(self, reservation):
        """Send a change-data event to Televes."""
        self.ensure_one()
        payload = self._televes_build_guest_payload(reservation)
        if payload is None:
            return False
        success = self._televes_request(_CHANGEDATA_ENDPOINT, payload)
        if not success:
            reservation.message_post(
                body=_("Televes API error: change-data notification could not be sent.")
            )
        return success

    def _televes_send_changeroom(self, reservation, from_room, to_room):
        """Send a change-room event to Televes."""
        self.ensure_one()
        if not from_room.televes_room_number or not to_room.televes_room_number:
            _logger.warning(
                "Televes: changeroom skipped - missing televes_room_number "
                "(from: %s, to: %s)",
                from_room.name,
                to_room.name,
            )
            return False
        payload = {
            "roomNumber": from_room.televes_room_number,
            "destRoomNumber": to_room.televes_room_number,
        }
        success = self._televes_request(_CHANGEROOM_ENDPOINT, payload)
        if not success:
            reservation.message_post(
                body=_("Televes API error: change-room notification could not be sent.")
                + f" ({from_room.name} \u2192 {to_room.name})"
            )
        return success

    def _televes_send_checkout(self, reservation):
        """Send a check-out event to Televes."""
        self.ensure_one()
        room = reservation.televes_current_room_id
        if not room or not room.televes_room_number:
            _logger.warning(
                "Televes: checkout skipped for reservation %s "
                "- no current room tracked",
                reservation.name,
            )
            return False
        payload = {"roomNumber": room.televes_room_number}
        success = self._televes_request(_CHECKOUT_ENDPOINT, payload)
        if not success:
            reservation.message_post(
                body=_("Televes API error: check-out notification could not be sent.")
            )
        return success

    def televes_cron_check_room_changes(self):
        """Cron: detect pre-planned room changes at noon and notify Televes."""
        today = fields.Date.today()
        onboard_reservations = self.env["pms.reservation"].search(
            [
                ("state", "=", "onboard"),
                ("televes_checkin_sent", "=", True),
            ]
        )
        for reservation in onboard_reservations:
            property_id = reservation.pms_property_id
            if not property_id.televes_enabled:
                continue
            today_line = reservation.reservation_line_ids.filtered(
                lambda line: line.date == today
            )
            today_room = (
                today_line.room_id if today_line else reservation.preferred_room_id
            )
            if not today_room:
                continue
            if today_room != reservation.televes_current_room_id:
                from_room = reservation.televes_current_room_id
                if from_room:
                    property_id._televes_send_changeroom(
                        reservation, from_room, today_room
                    )
                reservation.televes_current_room_id = today_room
