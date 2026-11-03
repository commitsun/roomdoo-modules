# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
import base64
import os
from unittest import mock

from odoo.exceptions import UserError

from ..models.ertzaintza_client import ErtzaintzaClient, ErtzaintzaTransportError
from ..models.ertzaintza_codes import ENTITY_PV, ENTITY_RH
from .common import TestErtzaintzaCommon

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "responses")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        return handle.read()


class TestCommunicationFlow(TestErtzaintzaCommon):
    def setUp(self):
        super().setUp()
        self._install_test_certificate()
        self.reservation = self._create_reservation()
        self.communication = self.env["pms.ertzaintza.communication"].search(
            [("reservation_id", "=", self.reservation.id)]
        )

    def _post_returns(self, name, status=200):
        """Patch the HTTP layer only: signing and parsing stay real."""
        return mock.patch.object(
            ErtzaintzaClient, "post", return_value=(status, fixture(name))
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def test_send_success(self):
        with self._post_returns("ok.xml"):
            self.assertTrue(self.communication._send())
        self.assertEqual(self.communication.state, "processed")
        self.assertEqual(
            self.communication.request_uuid, "ef669758-39ba-451f-a600-a09298794895"
        )
        self.assertEqual(self.communication.result_code, "0")
        self.assertEqual(self.communication.send_attempt_count, 1)
        self.assertTrue(self.communication.communication_xml)
        self.assertIn("BinarySecurityToken", self.communication.communication_soap)
        self.assertTrue(self.communication.processed_time)

    def test_send_duplicate_is_success(self):
        """CTO01 means the Ertzaintza already has the contract."""
        with self._post_returns("error_cto01.xml"):
            self.assertTrue(self.communication._send())
        self.assertEqual(self.communication.state, "processed")
        self.assertIn("CTO01", self.communication.errors)

    def test_send_data_errors_reject_the_communication(self):
        messages_before = len(self.reservation.message_ids)
        with self._post_returns("error_per43.xml"):
            self.assertFalse(self.communication._send())
        self.assertEqual(self.communication.state, "rejected")
        self.assertIn("PER43", self.communication.errors)
        self.assertGreater(len(self.reservation.message_ids), messages_before)

    def test_send_auth_error_can_be_retried(self):
        with self._post_returns("error_403.xml"):
            self.assertFalse(self.communication._send())
        self.assertEqual(self.communication.state, "error_sending")
        self.assertIn("403", self.communication.sending_result)

    def test_send_transient_error_can_be_retried(self):
        with self._post_returns("error_999.xml"):
            self.assertFalse(self.communication._send())
        self.assertEqual(self.communication.state, "error_sending")

    def test_send_soap_fault(self):
        with self._post_returns("soap_fault.xml", status=500):
            self.assertFalse(self.communication._send())
        self.assertEqual(self.communication.state, "error_sending")

    def test_send_transport_error_keeps_the_request(self):
        with mock.patch.object(
            ErtzaintzaClient, "post", side_effect=ErtzaintzaTransportError("timeout")
        ):
            self.assertFalse(self.communication._send())
        self.assertEqual(self.communication.state, "error_sending")
        self.assertIn("timeout", self.communication.sending_result)
        self.assertTrue(self.communication.communication_soap)
        self.assertEqual(self.communication.send_attempt_count, 1)

    def test_send_refuses_a_property_that_left_the_ertzaintza(self):
        self.pms_property1.institution = "ses"
        with self.assertRaises(UserError):
            self.communication._send()

    def test_service_timestamp_is_stored_in_utc(self):
        """The service stamps its answers in Spanish local time."""
        model = self.env["pms.ertzaintza.communication"]
        parsed = model._parse_service_datetime("2026-09-15T12:45:05.003")
        self.assertEqual(parsed.hour, 10)  # CEST is UTC+2 in September
        self.assertFalse(model._parse_service_datetime("not a date"))
        self.assertFalse(model._parse_service_datetime(""))

    def test_send_without_configuration(self):
        self.pms_property1.institution_lessor_id = False
        with self._post_returns("ok.xml") as post:
            self.assertFalse(self.communication._send())
        post.assert_not_called()
        self.assertEqual(self.communication.state, "error_sending")
        self.assertIn("lessor code", self.communication.sending_result)

    def test_traveller_report_waits_for_the_guests(self):
        reservation = self._create_reservation(adults=2)
        guest = self._create_guest(reservation)
        self._on_board(guest)
        report = self.env["pms.ertzaintza.communication"].search(
            [("reservation_id", "=", reservation.id), ("entity", "=", ENTITY_PV)]
        )
        with self._post_returns("ok.xml") as post:
            self.assertFalse(report._send())
        post.assert_not_called()
        self.assertEqual(report.state, "incomplete")
        self.assertIn("checked in", report.errors)

    def test_traveller_report_with_the_guests_on_board(self):
        reservation = self._create_reservation(adults=2)
        guest = self._create_guest(reservation)
        self._on_board(guest)
        report = self.env["pms.ertzaintza.communication"].search(
            [("reservation_id", "=", reservation.id), ("entity", "=", ENTITY_PV)]
        )
        with self._post_returns("ok.xml"):
            self.assertTrue(
                report.with_context(ertzaintza_ignore_not_onboard=True)._send()
            )
        self.assertEqual(report.state, "processed")
        self.assertEqual(report.checkin_partner_ids, guest)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def test_force_send_resets_the_attempt_counter(self):
        self.communication.write({"state": "error_sending", "send_attempt_count": 4})
        with self._post_returns("ok.xml"):
            self.communication.action_force_send()
        self.assertEqual(self.communication.state, "processed")
        self.assertEqual(self.communication.send_attempt_count, 1)

    def test_force_send_skips_final_states(self):
        self.communication.state = "cancelled"
        with self._post_returns("ok.xml") as post:
            self.communication.action_force_send()
        post.assert_not_called()
        self.assertEqual(self.communication.state, "cancelled")

    def test_cancel_and_mark_manually_sent(self):
        self.communication.action_cancel()
        self.assertEqual(self.communication.state, "cancelled")
        with self.assertRaises(UserError):
            self.communication.action_cancel()
        other = self.env["pms.ertzaintza.communication"].create(
            {
                "reservation_id": self.reservation.id,
                "entity": ENTITY_RH,
                "state": "rejected",
                "contract_reference": "%s-manual" % self.reservation.name,
            }
        )
        messages_before = len(self.reservation.message_ids)
        other.action_mark_manually_sent()
        self.assertEqual(other.state, "processed")
        self.assertTrue(other.manually_sent)
        self.assertGreater(len(self.reservation.message_ids), messages_before)

    def test_download_xml(self):
        action = self.communication.action_download_xml()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertTrue(self.communication.manual_export_date)
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", self.communication._name),
                ("res_id", "=", self.communication.id),
            ]
        )
        self.assertEqual(len(attachment), 1)
        content = base64.b64decode(attachment.datas).decode("utf-8")
        self.assertIn("<referencia>%s</referencia>" % self.reservation.name, content)
        self.assertTrue(self.communication.xml_filename.startswith("A19_RH_480354_"))

    def test_download_xml_of_mixed_selections_is_refused(self):
        other = self._create_reservation(pms_property=self.pms_property_ses)
        foreign = self.env["pms.ertzaintza.communication"].create(
            {"reservation_id": other.id, "entity": ENTITY_RH}
        )
        with self.assertRaises(UserError):
            (self.communication + foreign).action_download_xml()

    def test_test_connection_reports_an_accepted_certificate(self):
        with self._post_returns("error_pet07.xml"):
            action = self.pms_property1.action_ertzaintza_test_connection()
        self.assertEqual(action["params"]["type"], "success")
        with self._post_returns("error_403.xml"):
            action = self.pms_property1.action_ertzaintza_test_connection()
        self.assertEqual(action["params"]["type"], "danger")

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------
    def test_cron_picks_pending_and_retryable_communications(self):
        retryable = self.env["pms.ertzaintza.communication"].create(
            {
                "reservation_id": self.reservation.id,
                "entity": ENTITY_RH,
                "state": "error_sending",
                "send_attempt_count": 2,
            }
        )
        exhausted = self.env["pms.ertzaintza.communication"].create(
            {
                "reservation_id": self.reservation.id,
                "entity": ENTITY_RH,
                "state": "error_sending",
                "send_attempt_count": 5,
            }
        )
        other_entity = self.env["pms.ertzaintza.communication"].create(
            {
                "reservation_id": self.reservation.id,
                "entity": ENTITY_PV,
                "state": "to_send",
            }
        )
        with mock.patch.object(
            type(self.communication), "_send", return_value=True
        ) as send:
            self.env["pms.ertzaintza.communication"].cron_send_communications(ENTITY_RH)
        # the pending one and the retryable one, never the exhausted one nor
        # the traveller report of the other entity
        self.assertEqual(send.call_count, 2)
        self.assertTrue(
            retryable.exists() and exhausted.exists() and other_entity.exists()
        )

    def test_cron_skips_other_institutions(self):
        reservation = self._create_reservation(pms_property=self.pms_property_ses)
        self.env["pms.ertzaintza.communication"].create(
            {
                "reservation_id": reservation.id,
                "entity": ENTITY_RH,
                "state": "to_send",
            }
        )
        self.communication.state = "cancelled"
        with mock.patch.object(
            type(self.communication), "_send", return_value=True
        ) as send:
            self.env["pms.ertzaintza.communication"].cron_send_communications(ENTITY_RH)
        send.assert_not_called()

    def test_cron_sends_old_incomplete_reports_with_the_guests_on_board(self):
        reservation = self._create_reservation(adults=2)
        guest = self._create_guest(reservation)
        self._on_board(guest)
        report = self.env["pms.ertzaintza.communication"].search(
            [("reservation_id", "=", reservation.id), ("entity", "=", ENTITY_PV)]
        )
        self.env.cr.execute(
            "UPDATE pms_ertzaintza_communication "
            "SET create_date = now() - interval '30 hours' WHERE id = %s",
            (report.id,),
        )
        report.invalidate_recordset(["create_date"])
        messages_before = len(reservation.message_ids)
        with self._post_returns("ok.xml"):
            self.env[
                "pms.ertzaintza.communication"
            ].cron_send_incomplete_traveller_reports(20)
        self.assertEqual(report.state, "processed")
        self.assertGreater(len(reservation.message_ids), messages_before)

    def test_cron_queues_recent_reports_that_became_complete(self):
        reservation = self._create_reservation(adults=1)
        guest = self._create_guest(reservation)
        self._on_board(guest)
        report = self.env["pms.ertzaintza.communication"].search(
            [("reservation_id", "=", reservation.id), ("entity", "=", ENTITY_PV)]
        )
        report.state = "incomplete"
        self.env["pms.ertzaintza.communication"].cron_send_incomplete_traveller_reports(
            20
        )
        self.assertEqual(report.state, "to_send")
