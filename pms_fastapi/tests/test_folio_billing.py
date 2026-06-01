import datetime

from fastapi import status

from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestFolioBilling(CommonTestPmsApi):
    """Billing-related folio endpoints: sale-lines, detail and contacts.

    Covers the module's own transformation logic (sale-line invoice
    aggregation, reservation state mapping, contact collection) rather than
    plain ORM/serialization behaviour.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.test_company
        if not company.chart_template_id:
            coa = cls.env.ref("l10n_generic_coa.configurable_chart_template", False)
            if not coa:
                coa = cls.env["account.chart.template"].search(
                    [("visible", "=", True)], limit=1
                )
            if not coa:
                cls.skipTest(cls, "No chart of accounts available.")
            coa.try_loading(company=company, install_demo=False)
        cls.env.user.write(
            {
                "company_ids": [Command.link(company.id)],
                "company_id": company.id,
            }
        )
        cls.env = cls.env(
            context=dict(cls.env.context, allowed_company_ids=company.ids)
        )
        cls.journal_sale = cls.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", company.id)], limit=1
        )

        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "Standard", "default_code": "STD"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.test_property.id],
                "name": "Double Test",
                "default_code": "DBL_Test",
                "class_id": cls.room_type_class.id,
            }
        )
        cls.room1 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.test_property.id,
                "name": "101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {"firstname": "John", "lastname": "Doe"}
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Test", "channel_type": "direct"}
        )
        cls.agency_channel = cls.env["pms.sale.channel"].create(
            {"name": "Agency Channel", "channel_type": "indirect"}
        )
        cls.agency = cls.env["res.partner"].create(
            {
                "name": "Test Agency",
                "is_agency": True,
                "sale_channel_id": cls.agency_channel.id,
            }
        )

    def _create_folio_with_reservation(self, partner=None, agency=None):
        today = fields.date.today()
        partner = partner or self.partner
        folio_vals = {
            "pms_property_id": self.test_property.id,
            "partner_name": partner.name,
            "partner_id": partner.id,
        }
        if agency:
            folio_vals["agency_id"] = agency.id
        folio = self.env["pms.folio"].create(folio_vals)
        channel = self.agency_channel if agency else self.sale_channel
        self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "room_type_id": self.room_type.id,
                "partner_id": partner.id,
                "adults": 1,
                "sale_channel_origin_id": channel.id,
                "reservation_line_ids": [
                    (0, False, {"date": today + datetime.timedelta(days=i)})
                    for i in range(2)
                ],
            }
        )
        return folio

    def _room_sale_line(self, folio):
        return folio.sale_line_ids.filtered(
            lambda line: not line.display_type and not line.is_downpayment
        )[:1]

    def _invoice_sale_line(self, sale_line, move_type="out_invoice", qty=1.0):
        move = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": sale_line.folio_id.partner_id.id,
                "pms_property_id": self.test_property.id,
                "journal_id": self.journal_sale.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Invoiced room",
                            "quantity": qty,
                            "price_unit": 100.0,
                            "tax_ids": [Command.clear()],
                            "folio_line_ids": [Command.set(sale_line.ids)],
                        }
                    )
                ],
            }
        )
        move.action_post()
        return move

    # ------------------------------------------------------------------
    # GET /folios/{id}/sale-lines
    # ------------------------------------------------------------------
    def test_sale_lines_exclude_sections_notes_and_downpayments(self):
        folio = self._create_folio_with_reservation()
        section = self.env["folio.sale.line"].create(
            {"folio_id": folio.id, "display_type": "line_section", "name": "Section"}
        )
        note = self.env["folio.sale.line"].create(
            {"folio_id": folio.id, "display_type": "line_note", "name": "Note"}
        )
        downpayment = self.env["folio.sale.line"].create(
            {
                "folio_id": folio.id,
                "name": "Down payment",
                "is_downpayment": True,
                "product_uom_qty": 1,
                "price_unit": 50.0,
            }
        )
        expected_ids = set(
            folio.sale_line_ids.filtered(
                lambda line: not line.display_type and not line.is_downpayment
            ).ids
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}/sale-lines")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        returned_ids = {line["id"] for line in response.json()}
        self.assertEqual(returned_ids, expected_ids)
        self.assertNotIn(section.id, returned_ids)
        self.assertNotIn(note.id, returned_ids)
        self.assertNotIn(downpayment.id, returned_ids)

    def test_sale_line_room_type_and_pending_state(self):
        folio = self._create_folio_with_reservation()
        room_line = self._room_sale_line(folio)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}/sale-lines")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        line = next(d for d in response.json() if d["id"] == room_line.id)
        self.assertEqual(line["lineType"], "room")
        self.assertEqual(line["invoiceState"], "pending")
        self.assertEqual(line["invoices"], [])

    def test_sale_line_invoice_and_refund_net_quantity(self):
        folio = self._create_folio_with_reservation()
        room_line = self._room_sale_line(folio)
        invoice = self._invoice_sale_line(room_line, "out_invoice", qty=1.0)
        refund = self._invoice_sale_line(room_line, "out_refund", qty=1.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}/sale-lines")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        line = next(d for d in response.json() if d["id"] == room_line.id)
        invoices_by_id = {inv["id"]: inv for inv in line["invoices"]}
        self.assertEqual(set(invoices_by_id), {invoice.id, refund.id})
        self.assertEqual(invoices_by_id[invoice.id]["quantityInvoiced"], 1.0)
        self.assertEqual(invoices_by_id[refund.id]["quantityInvoiced"], -1.0)
        # invoice (+1) and refund (-1) net to zero invoiced quantity
        self.assertEqual(line["quantityInvoiced"], 0.0)

    def test_sale_line_cancelled_invoice_is_excluded(self):
        folio = self._create_folio_with_reservation()
        room_line = self._room_sale_line(folio)
        invoice = self._invoice_sale_line(room_line, "out_invoice", qty=1.0)
        invoice.button_cancel()
        self.assertEqual(invoice.state, "cancel")
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}/sale-lines")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        line = next(d for d in response.json() if d["id"] == room_line.id)
        self.assertEqual(line["invoices"], [])
        self.assertEqual(line["quantityInvoiced"], 0.0)

    # ------------------------------------------------------------------
    # GET /folios/{id} (detail)
    # ------------------------------------------------------------------
    def _create_extra_reservation(self, folio, partner=None, days_ahead=30):
        # A reservation in dates that do not overlap the folio's main one, to
        # avoid availability conflicts with the single test room.
        start = fields.date.today() + datetime.timedelta(days=days_ahead)
        return self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "room_type_id": self.room_type.id,
                "partner_id": (partner or self.partner).id,
                "adults": 1,
                "sale_channel_origin_id": self.sale_channel.id,
                "reservation_line_ids": [(0, False, {"date": start})],
            }
        )

    def test_detail_excludes_modified_reservations(self):
        folio = self._create_folio_with_reservation()
        kept = folio.reservation_ids
        modified = self._create_extra_reservation(folio)
        modified.with_context(action_cancel=True).write(
            {"state": "cancel", "cancelled_reason": "modified"}
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        reservation_ids = {r["id"] for r in response.json()["reservations"]}
        self.assertIn(kept.id, reservation_ids)
        self.assertNotIn(modified.id, reservation_ids)

    def test_detail_cancelled_state(self):
        folio = self._create_folio_with_reservation()
        reservation = folio.reservation_ids
        reservation.with_context(action_cancel=True).write(
            {"state": "cancel", "cancelled_reason": "late"}
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        res_data = next(
            r for r in response.json()["reservations"] if r["id"] == reservation.id
        )
        self.assertEqual(res_data["state"], "cancelled")

    def test_detail_payers_from_default_invoice_to(self):
        folio = self._create_folio_with_reservation()
        payer = self.env["res.partner"].create({"name": "Payer Partner"})
        self._room_sale_line(folio).write({"default_invoice_to": payer.id})
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        payer_ids = {p["id"] for p in response.json()["payers"]}
        self.assertIn(payer.id, payer_ids)

    # ------------------------------------------------------------------
    # GET /folios/{id}/contacts
    # ------------------------------------------------------------------
    def test_contacts_aggregate_and_deduplicate(self):
        folio = self._create_folio_with_reservation(agency=self.agency)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}/contacts")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        contacts = response.json()
        contact_ids = [c["id"] for c in contacts]
        self.assertIn(self.partner.id, contact_ids)
        self.assertIn(self.agency.id, contact_ids)
        # partner is both the folio customer and the reservation guest: deduped
        self.assertEqual(len(contact_ids), len(set(contact_ids)))

    def test_contacts_exclude_modified_reservation_partners(self):
        folio = self._create_folio_with_reservation()
        other_guest = self.env["res.partner"].create(
            {"firstname": "Modified", "lastname": "Guest"}
        )
        modified = self._create_extra_reservation(folio, partner=other_guest)
        modified.with_context(action_cancel=True).write(
            {"state": "cancel", "cancelled_reason": "modified"}
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios/{folio.id}/contacts")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        contact_ids = {c["id"] for c in response.json()}
        self.assertNotIn(other_guest.id, contact_ids)

    # ------------------------------------------------------------------
    # Reservation NO_SHOW state mapping (via /folios list summary)
    # ------------------------------------------------------------------
    def test_reservation_no_show_state(self):
        folio = self._create_folio_with_reservation()
        reservation = folio.reservation_ids
        reservation.with_context(action_cancel=True).write(
            {"state": "cancel", "cancelled_reason": "noshow"}
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/folios?pmsPropertyId={self.test_property.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        folio_data = next(
            item for item in response.json()["items"] if item["id"] == folio.id
        )
        res_data = next(
            r for r in folio_data["reservations"] if r["id"] == reservation.id
        )
        self.assertEqual(res_data["state"], "noShow")
