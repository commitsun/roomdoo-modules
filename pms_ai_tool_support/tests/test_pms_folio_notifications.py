import json

import pytz

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import GUEST_EMAIL, SupportToolsCase


@tagged("post_install", "-at_install")
class TestPmsFolioNotifications(SupportToolsCase):
    def _notifications(self, **kwargs):
        kwargs.setdefault("folio_id", self.folio.id)
        return self._run("pms_folio_notifications", **kwargs)

    def _rows(self, response):
        return {row["id"]: row for row in response["result"]["notifications"]}

    def test_logs_of_the_folio(self):
        response = self._notifications()
        self.assertEqual(response["tool"], "pms_folio_notifications")
        self.assertTrue(response["definitions"])
        self.assertIn(self.folio.name, response["summary"])
        rows = self._rows(response)
        expected = (
            self.log_sent
            | self.log_error
            | self.log_disabled
            | self.log_manual
            | self.log_late
            | self.log_waiting
            | self.log_overdue
            | self.log_cancelled
            | self.log_reservation
        )
        # The log of a reservation of the folio counts; the one of another
        # folio does not.
        self.assertEqual(set(rows), set(expected.ids))
        self.assertNotIn(self.log_other.id, rows)
        ids = [row["id"] for row in response["result"]["notifications"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        counts = {row["state"]: row["count"] for row in response["result"]["counts"]}
        self.assertEqual(
            counts, {"sent": 2, "error": 1, "skipped": 3, "pending": 2, "cancelled": 1}
        )

    def test_sent_log(self):
        sent = self._rows(self._notifications())[self.log_sent.id]
        self.assertEqual(sent["rule"], "AI confirmation rule")
        self.assertEqual(sent["template"], "AI confirmation")
        self.assertEqual(sent["template_code"], "ai_support_confirmation")
        self.assertEqual(sent["channel"], "email")
        self.assertEqual(sent["state_label"], "Sent")
        self.assertIn("Odoo does not know whether the guest", sent["explanation"])
        self.assertEqual(sent["recipient"], "j***@example.com")
        madrid = pytz.timezone("Europe/Madrid")
        self.assertEqual(
            sent["sent"],
            pytz.utc.localize(self.log_sent.sent_date)
            .astimezone(madrid)
            .isoformat(timespec="minutes"),
        )
        self.assertIsNone(sent["error"])
        self.assertIsNone(sent["http_status"])

    def test_error_log_is_explained_and_masked(self):
        response = self._notifications()
        error = self._rows(response)[self.log_error.id]
        self.assertEqual(error["state"], "error")
        self.assertIn("refused the request", error["explanation"])
        self.assertIn("***222", error["error"])
        self.assertIn("j***@example.com", error["error"])
        self.assertNotIn("600111222", error["error"])
        dump = json.dumps(response)
        self.assertNotIn(GUEST_EMAIL, dump)
        self.assertNotIn("34600111222", dump)

    def test_skipped_waiting_and_cancelled_logs(self):
        rows = self._rows(self._notifications())
        self.assertIn("BookAI is disabled", rows[self.log_disabled.id]["explanation"])
        self.assertIn("manual mode", rows[self.log_manual.id]["explanation"])
        late = rows[self.log_late.id]
        self.assertIn("no longer met the conditions", late["explanation"])
        self.assertIsNone(late["error"])
        self.assertTrue(
            rows[self.log_waiting.id]["explanation"].startswith(
                "Waiting: it will be processed from"
            )
        )
        self.assertTrue(
            rows[self.log_overdue.id]["explanation"].startswith(
                "Waiting for the next run"
            )
        )
        cancelled = rows[self.log_cancelled.id]
        self.assertIn("Cancelled by a person", cancelled["explanation"])
        self.assertIsNone(cancelled["rule"])

    def test_overdue_warning(self):
        warnings = self._notifications()["warnings"]
        self.assertTrue(
            any(
                text.startswith("1 logs are still waiting although they were due")
                for text in warnings
            )
        )

    def test_error_kinds_in_both_languages(self):
        tools = self.env["pms.ai.tools"]
        cases = (
            ("BookAI API endpoint/token is not configured.", "not_configured"),
            (
                "El punto final/token de la API BookAI no está configurado.",
                "not_configured",
            ),
            (
                "BookAI API request failed with status 422: template code",
                "api_refused",
            ),
            (
                "La solicitud de la API BookAI falló con el estado 500: x",
                "api_refused",
            ),
            ("BookAI API connection error: timeout", "api_unreachable"),
            (
                "Falta el teléfono/país del destinatario en el registro.",
                "no_phone",
            ),
            (
                "Phone must be in international format (e.g. +346...). Got: 6",
                "bad_phone",
            ),
            (
                "No se puede determinar el país del destinatario del teléfono '6'.",
                "bad_phone",
            ),
            (
                "Missing BookAI template code on notification template.",
                "template_code",
            ),
            ("Something unknown", None),
        )
        for text, kind in cases:
            self.assertEqual(tools._support_error_kind(text), kind, text)

    def test_rows_are_limited(self):
        self.env["ir.config_parameter"].sudo().set_param("pms_ai_tool.max_rows", "3")
        response = self._notifications()
        self.assertEqual(len(response["result"]["notifications"]), 3)
        self.assertIn("Only the first 3 of 9 rows are shown.", response["warnings"])
        self.assertEqual(sum(row["count"] for row in response["result"]["counts"]), 9)

    def test_scope_and_arguments(self):
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._notifications(user=self.stranger)
        with self.assertRaisesRegex(UserError, "either folio_id or folio_code"):
            self._run("pms_folio_notifications")
        with self.assertRaisesRegex(UserError, "Unknown arguments"):
            self._notifications(limit=500)
        by_code = self._run("pms_folio_notifications", folio_code=self.folio.name)
        self.assertEqual(by_code["result"]["folio"]["id"], self.folio.id)
