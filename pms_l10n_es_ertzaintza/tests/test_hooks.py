# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from datetime import timedelta

from odoo import fields

from ..models.ertzaintza_codes import ENTITY_PV, ENTITY_RH
from .common import TestErtzaintzaCommon


class TestReservationHooks(TestErtzaintzaCommon):
    def _communications(self, reservation, entity=None):
        domain = [("reservation_id", "=", reservation.id)]
        if entity:
            domain.append(("entity", "=", entity))
        return self.env["pms.ertzaintza.communication"].search(domain, order="id")

    def _tomorrow_reservation(self, **values):
        today = fields.Date.today()
        return self._create_reservation(
            checkin=today + timedelta(days=1),
            checkout=today + timedelta(days=3),
            **values,
        )

    def test_create_reservation_queues_the_reservation_report(self):
        reservation = self._create_reservation()
        communications = self._communications(reservation)
        self.assertEqual(len(communications), 1)
        self.assertEqual(communications.entity, ENTITY_RH)
        self.assertEqual(communications.state, "to_send")
        self.assertEqual(communications.contract_reference, reservation.name)
        self.assertEqual(communications.pms_property_id, self.pms_property1)
        self.assertTrue(reservation.is_ertzaintza)
        self.assertEqual(reservation.ertzaintza_status_reservation, "to_send")
        self.assertEqual(reservation.ertzaintza_status_traveller_report, "error_create")

    def test_ses_property_creates_nothing(self):
        reservation = self._create_reservation(pms_property=self.pms_property_ses)
        self.assertFalse(self._communications(reservation))
        self.assertFalse(reservation.is_ertzaintza)
        self.assertEqual(reservation.ertzaintza_status_reservation, "not_applicable")

    def test_out_of_service_creates_nothing(self):
        reservation = self._create_reservation(
            reservation_type="out", closure_reason_id=self.closure_reason.id
        )
        self.assertFalse(self._communications(reservation))

    def test_cancel_before_sending_cancels_the_communication(self):
        reservation = self._tomorrow_reservation()
        reservation.action_cancel()
        communication = self._communications(reservation)
        self.assertEqual(communication.state, "cancelled")

    def test_cancel_after_sending_only_warns(self):
        reservation = self._tomorrow_reservation()
        communication = self._communications(reservation)
        communication.state = "processed"
        messages_before = len(reservation.message_ids)
        reservation.action_cancel()
        self.assertEqual(communication.state, "processed")
        self.assertGreater(len(reservation.message_ids), messages_before)
        self.assertIn(
            "no cancellation operation",
            reservation.message_ids.sorted("id")[-1].body,
        )

    def test_reactivating_a_reservation_queues_a_new_report(self):
        reservation = self._tomorrow_reservation()
        reservation.action_cancel()
        reservation.action_confirm()
        communications = self._communications(reservation, ENTITY_RH)
        self.assertEqual(len(communications), 2)
        self.assertEqual(communications[-1].state, "to_send")

    def test_change_after_sending_only_warns(self):
        reservation = self._create_reservation()
        communication = self._communications(reservation)
        communication.state = "processed"
        messages_before = len(reservation.message_ids)
        reservation.write({"checkout": reservation.checkout + timedelta(days=1)})
        self.assertEqual(len(self._communications(reservation)), 1)
        self.assertGreater(len(reservation.message_ids), messages_before)
        self.assertIn(
            "no modification operation",
            reservation.message_ids.sorted("id")[-1].body,
        )

    def test_change_before_sending_is_silent(self):
        reservation = self._create_reservation()
        messages_before = len(reservation.message_ids)
        reservation.write({"checkout": reservation.checkout + timedelta(days=1)})
        communications = self._communications(reservation)
        self.assertEqual(len(communications), 1)
        self.assertEqual(communications.state, "to_send")
        self.assertEqual(len(reservation.message_ids), messages_before)


class TestCheckinHooks(TestErtzaintzaCommon):
    def _traveller_reports(self, reservation):
        return self.env["pms.ertzaintza.communication"].search(
            [("reservation_id", "=", reservation.id), ("entity", "=", ENTITY_PV)],
            order="id",
        )

    def test_first_guest_on_board_opens_the_report(self):
        reservation = self._create_reservation(adults=2)
        first = self._create_guest(reservation)
        self._on_board(first)
        reports = self._traveller_reports(reservation)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports.state, "incomplete")
        self.assertEqual(reports.contract_reference, reservation.name)

    def test_report_is_queued_when_everybody_is_on_board(self):
        reservation = self._create_reservation(adults=2)
        first = self._create_guest(reservation)
        second = self._create_guest(reservation, firstname="Ane", gender="female")
        self._on_board(first)
        self.assertEqual(self._traveller_reports(reservation).state, "incomplete")
        self._on_board(second)
        report = self._traveller_reports(reservation)
        self.assertEqual(len(report), 1)
        self.assertEqual(report.state, "to_send")
        self.assertEqual(reservation.ertzaintza_status_traveller_report, "to_send")

    def test_ses_property_does_not_open_a_report(self):
        reservation = self._create_reservation(pms_property=self.pms_property_ses)
        guest = self._create_guest(reservation)
        self._on_board(guest)
        self.assertFalse(self._traveller_reports(reservation))

    def test_late_guest_gets_a_supplementary_report(self):
        reservation = self._create_reservation(adults=2)
        first = self._create_guest(reservation)
        self._on_board(first)
        report = self._traveller_reports(reservation)
        report.write({"state": "processed", "checkin_partner_ids": [(6, 0, first.ids)]})
        late = self._create_guest(reservation, firstname="Ane", gender="female")
        self._on_board(late)
        reports = self._traveller_reports(reservation)
        self.assertEqual(len(reports), 2)
        supplementary = reports[-1]
        self.assertEqual(supplementary.contract_reference, "%s-2" % reservation.name)
        self.assertEqual(supplementary._pv_pending_checkin_partners(), late)
