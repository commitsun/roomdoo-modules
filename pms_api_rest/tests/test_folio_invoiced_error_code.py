# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""External clients that hit the ``/folios`` PUT endpoints need a stable,
machine-readable error when the target folio cannot be modified/cancelled
because it has invoiced lines. Otherwise every backend ``UserError`` looks
the same and callers cannot distinguish this specific business condition
from any other validation failure.

These tests cover the two API-side pieces that make the code
``FOLIO_HAS_INVOICED_LINES`` reach the client:

* ``_folio_has_locked_invoiced_lines`` — the guard used to decide whether
  the current failure is caused by invoicing.
* ``_raise_folio_invoiced_error`` — the wrapper that turns the underlying
  ``UserError`` into a ``ValidationError`` whose message is a JSON payload
  with the ``code`` field.
* the ``update_put_folio`` end-to-end flow, forcing the update path to
  raise a ``UserError`` on a folio that already has a posted invoice.
"""
import datetime
import json
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestFolioInvoicedErrorCode(TestPms, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Use admin (uid=1) so record rules do not get in the way; the API
        # endpoint itself sudo()es its lookups.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))

        cls.simplified_journal = cls.env["account.journal"].create(
            {
                "name": "Simplified journal",
                "code": "SMPT",
                "type": "sale",
                "company_id": cls.env.ref("base.main_company").id,
            }
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Property for invoiced-error tests",
                "company_id": cls.env.ref("base.main_company").id,
                "default_pricelist_id": cls.pricelist1.id,
                "journal_simplified_invoice_id": cls.simplified_journal.id,
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": "Double Test",
                "default_code": "DBL_ERR",
                "class_id": cls.room_type_class1.id,
                "list_price": 25,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Room 101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Guest",
                "vat": "45224522J",
                "country_id": cls.env.ref("base.es").id,
                "city": "Madrid",
                "zip": "28013",
                "street": "Calle Falsa 1",
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct", "channel_type": "direct"}
        )

    def _make_folio(self):
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": datetime.datetime.now(),
                "checkout": datetime.datetime.now() + datetime.timedelta(days=2),
                "adults": 2,
                "room_type_id": self.room_type.id,
                "partner_id": self.partner.id,
                "sale_channel_origin_id": self.sale_channel.id,
            }
        )
        return reservation.folio_id

    def _folio_service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="folios")

    # ---- helper ------------------------------------------------------------

    def test_helper_false_when_folio_has_no_invoice(self):
        folio = self._make_folio()
        self.assertFalse(self._folio_service()._folio_has_locked_invoiced_lines(folio))

    def test_helper_false_when_invoice_is_only_draft(self):
        folio = self._make_folio()
        folio._create_invoices()
        # A draft move alone does not lock the folio: qty_invoiced is only
        # incremented when the move is posted.
        self.assertEqual(folio.move_ids.state, "draft")
        self.assertFalse(self._folio_service()._folio_has_locked_invoiced_lines(folio))

    def test_helper_true_when_invoice_is_posted(self):
        folio = self._make_folio()
        folio._create_invoices()
        folio.move_ids.action_post()
        self.assertTrue(self._folio_service()._folio_has_locked_invoiced_lines(folio))

    def test_helper_false_for_empty_recordset(self):
        empty = self.env["pms.folio"]
        self.assertFalse(self._folio_service()._folio_has_locked_invoiced_lines(empty))

    # ---- structured error --------------------------------------------------

    def test_raise_folio_invoiced_error_shape(self):
        service = self._folio_service()
        with self.assertRaises(ValidationError) as cm:
            service._raise_folio_invoiced_error(UserError("locked line"))
        payload = json.loads(cm.exception.args[0])
        self.assertEqual(payload["code"], "FOLIO_HAS_INVOICED_LINES")
        self.assertEqual(payload["message"], "locked line")

    # ---- endpoint ----------------------------------------------------------

    def _build_put_payload(self, folio):
        reservation = folio.reservation_ids[0]
        ReservationInfo = self.env.datamodels["pms.reservation.info"]
        reservation_info = ReservationInfo(partial=True)
        reservation_info.id = reservation.id
        reservation_info.checkin = reservation.checkin.strftime("%Y-%m-%d")
        reservation_info.checkout = reservation.checkout.strftime("%Y-%m-%d")

        FolioInfo = self.env.datamodels["pms.folio.info"]
        folio_info = FolioInfo(partial=True)
        folio_info.state = "cancel"
        folio_info.pmsPropertyId = self.property.id
        folio_info.reservations = [reservation_info]
        return folio_info

    def test_put_folio_returns_structured_code_when_invoiced(self):
        folio = self._make_folio()
        folio._create_invoices()
        folio.move_ids.action_post()
        service = self._folio_service()
        payload = self._build_put_payload(folio)

        # Force ``update_folio_values`` to raise a UserError, mimicking the
        # real failure raised by ``folio.sale.line.write/unlink`` when a
        # posted invoice blocks the change. The endpoint must catch it and
        # re-raise a ValidationError whose body is the structured JSON.
        with patch.object(
            type(service),
            "update_folio_values",
            side_effect=UserError("cannot modify invoiced line"),
        ):
            with self.assertRaises(ValidationError) as cm:
                service.update_put_folio(folio.id, payload)

        body = json.loads(cm.exception.args[0])
        self.assertEqual(body["code"], "FOLIO_HAS_INVOICED_LINES")
        self.assertIn("cannot modify invoiced line", body["message"])

    def test_put_folio_keeps_generic_error_when_not_invoiced(self):
        # Same UserError, but the folio has no invoice: the endpoint must
        # fall back to the generic wrapper so we do not mislabel unrelated
        # failures as "invoiced".
        folio = self._make_folio()
        service = self._folio_service()
        payload = self._build_put_payload(folio)

        with patch.object(
            type(service),
            "update_folio_values",
            side_effect=UserError("unrelated failure"),
        ):
            with self.assertRaises(ValidationError) as cm:
                service.update_put_folio(folio.id, payload)

        # Not a JSON code — falls back to the pre-existing wrapper message.
        with self.assertRaises(ValueError):
            json.loads(cm.exception.args[0])
        self.assertIn("unrelated failure", cm.exception.args[0])
