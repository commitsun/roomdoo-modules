from datetime import date
from unittest.mock import patch

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED, get_guard

from .common import AdminToolsMixin

TOOL = "pms_folio_invoice"


@tagged("post_install", "-at_install")
class TestPmsFolioInvoice(AdminToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        Partner = cls.env["res.partner"]
        cls.customer = Partner.create(
            {
                "name": "AI Billing Company",
                "is_company": True,
                "country_id": cls.env.ref("base.es").id,
                "city": "AI City",
                "street": "AI Street 1",
            }
        )
        # A tax id as found in real data, without the VAT check.
        cls.env.cr.execute(
            "UPDATE res_partner SET vat = 'ESB12345674' WHERE id = %s",
            [cls.customer.id],
        )
        cls.person = Partner.create({"name": "AI Billing Person"})
        cls.anonymous = cls.env.ref("pms.various_pms_partner")
        cls.env.company.check_min_partner_data_invoice = True
        simplified = cls.env["account.journal"].create(
            {
                "name": "AI simplified invoices",
                "code": "AISI",
                "type": "sale",
                "company_id": cls.env.company.id,
                "is_simplified_invoice": True,
            }
        )
        cls.prop.journal_simplified_invoice_id = simplified
        with freeze_time("2026-09-01"):
            cls.folio = cls._folio(cls.prop, [100, 120])
            cls.other_folio = cls._folio(cls.other_prop, [90])
        cls.env.invalidate_all()
        cls.tool = cls.env.ref(f"pms_ai_tool_admin.ai_tool_{TOOL}")
        cls.tool.write(
            {
                "allowed_user_ids": [(6, 0, (cls.reader | cls.reader_both).ids)],
                "write_enabled": True,
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")

    @classmethod
    def _folio(cls, prop, prices):
        """Confirmed folio of the customer with a night per price."""
        reservation = cls.env["pms.reservation"].create(
            {
                "pms_property_id": prop.id,
                "room_type_id": cls.room_type.id,
                "partner_id": cls.customer.id,
                "sale_channel_origin_id": cls.direct.id,
                "reservation_line_ids": [
                    (0, 0, {"date": date(2026, 9, 20 + n), "price": price})
                    for n, price in enumerate(prices)
                ],
            }
        )
        reservation.action_confirm()
        return reservation.folio_id

    def _lines(self, folio=None):
        return (folio or self.folio).sale_line_ids.filtered(
            lambda line: not line.display_type
        )

    def _moves(self):
        return self.env["account.move"].search(
            [("folio_ids", "in", (self.folio | self.other_folio).ids)]
        )

    def _preview(self, **kwargs):
        kwargs.setdefault("folio", self.folio.name)
        return self._run(TOOL, **kwargs)

    def _confirm(self, preview, **kwargs):
        kwargs.setdefault("folio", self.folio.name)
        token = preview["confirmation"]["confirmation_token"]
        return self._run(TOOL, confirm=True, confirmation_token=token, **kwargs)

    def test_preview_changes_nothing_and_apply_does_what_it_listed(self):
        lines = self._lines()
        self.assertEqual(len(lines), 2)
        preview = self._preview()
        self.assertFalse(self._moves())
        self.assertTrue(preview["confirmation"]["required"])
        self.assertIn(self.folio.name, preview["summary"])
        (invoice,) = preview["result"]["invoices"]
        self.assertEqual(invoice["partner_id"], self.customer.id)
        self.assertEqual(invoice["invoice_type"], "full")
        self.assertEqual(invoice["document"], "invoice")
        self.assertEqual(
            sorted(line["sale_line_id"] for line in invoice["lines"]), sorted(lines.ids)
        )
        self.assertAlmostEqual(invoice["amount_untaxed"], 220, 2)
        self.assertAlmostEqual(invoice["amount_total"], self.folio.amount_total, 2)
        self.assertFalse(preview["result"]["post"])
        self.assertEqual(preview["warnings"], [])

        applied = self._confirm(preview)

        move = self._moves()
        self.assertEqual(len(move), 1)
        (created,) = applied["result"]["invoices"]
        self.assertEqual(created["id"], move.id)
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.move_type, "out_invoice")
        self.assertEqual(move.partner_id, self.customer)
        self.assertEqual(move.journal_id.id, invoice["journal_id"])
        self.assertFalse(move.journal_id.is_simplified_invoice)
        product_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertEqual(product_lines.folio_line_ids, lines)
        self.assertAlmostEqual(move.amount_untaxed, invoice["amount_untaxed"], 2)
        self.assertAlmostEqual(move.amount_total, invoice["amount_total"], 2)
        self.assertEqual(move.create_uid, self.reader)
        # Nothing is left to invoice: a new preview is refused.
        with self.assertRaisesRegex(UserError, "nothing to invoice"):
            self._preview()

    def test_some_lines_to_the_anonymous_customer_posted(self):
        line = self._lines()[:1]
        kwargs = {
            "sale_line_ids": line.ids,
            "partner_id": self.anonymous.id,
            "post": True,
        }
        preview = self._preview(**kwargs)
        self.assertFalse(self._moves())
        (invoice,) = preview["result"]["invoices"]
        self.assertEqual(invoice["invoice_type"], "simplified")
        self.assertEqual(invoice["partner_id"], self.anonymous.id)
        self.assertEqual([row["sale_line_id"] for row in invoice["lines"]], line.ids)
        self.assertTrue(preview["result"]["post"])

        self._confirm(preview, **kwargs)

        move = self._moves()
        self.assertEqual(len(move), 1)
        self.assertEqual(move.state, "posted")
        self.assertTrue(move.journal_id.is_simplified_invoice)
        self.assertEqual(move.partner_id, self.anonymous)
        self.assertEqual(
            move.invoice_line_ids.filtered(
                lambda line: line.display_type == "product"
            ).folio_line_ids,
            line,
        )
        self.assertAlmostEqual(move.amount_total, invoice["amount_total"], 2)
        # The other line is still pending.
        self.assertGreater((self._lines() - line).qty_to_invoice, 0)
        self.assertEqual(line.qty_to_invoice, 0)

    def test_customer_without_data_cannot_be_posted(self):
        # The data a full invoice needs depends on the localization: here,
        # a tax id.
        partner_class = type(self.env["res.partner"])
        with patch.object(
            partner_class,
            "_check_enought_invoice_data",
            lambda partner: bool(partner.vat),
        ):
            self._check_customer_without_data()

    def _check_customer_without_data(self):
        with self.assertRaisesRegex(UserError, "lacks the tax id"):
            self._preview(partner_id=self.person.id, post=True)
        preview = self._preview(partner_id=self.person.id)
        self.assertIn("lacks the tax id", " ".join(preview["warnings"]))
        self._confirm(preview, partner_id=self.person.id)
        move = self._moves()
        self.assertEqual((move.partner_id, move.state), (self.person, "draft"))

    def test_refusals(self):
        lines = self._lines()
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._preview(folio=str(self.other_folio.id))
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._preview(folio="000/00/999999")
        with self.assertRaisesRegex(UserError, "are not lines of the folio"):
            self._preview(sale_line_ids=self._lines(self.other_folio).ids)
        with self.assertRaisesRegex(UserError, "at most 50"):
            self._preview(sale_line_ids=list(range(1, 52)))
        with self.assertRaisesRegex(UserError, "No customer found"):
            self._preview(partner_id=10**9)
        for kwargs in ({"extra": 1}, {"folio": 12}, {"post": "yes"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._preview(**kwargs)
        # A line already invoiced has nothing left to invoice.
        first = self._preview(sale_line_ids=lines[:1].ids)
        self._confirm(first, sale_line_ids=lines[:1].ids)
        with self.assertRaisesRegex(UserError, "have nothing to invoice"):
            self._preview(sale_line_ids=lines.ids)
        self.assertEqual(len(self._moves()), 1)
        # The user who sees both properties can invoice the other one.
        preview = self._preview(user=self.reader_both, folio=str(self.other_folio.id))
        self.assertEqual(preview["result"]["folio"]["id"], self.other_folio.id)

    def test_confirmation_step(self):
        # By id: the other user sees both properties, which number folios
        # separately.
        folio = str(self.folio.id)
        preview = self._preview(folio=folio)
        token = preview["confirmation"]["confirmation_token"]
        with self.assertRaisesRegex(UserError, "Missing confirmation_token"):
            self._run(TOOL, folio=folio, confirm=True)
        with self.assertRaises(UserError):
            self._run(TOOL, folio=folio, confirm=True, confirmation_token="1.2")
        # A token only serves for what was previewed.
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(
                TOOL, folio=folio, post=True, confirm=True, confirmation_token=token
            )
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(
                TOOL,
                user=self.reader_both,
                folio=folio,
                confirm=True,
                confirmation_token=token,
            )
        self.assertFalse(self._moves())
        self._confirm(preview, folio=folio)
        self.assertEqual(len(self._moves()), 1)
        # A replay is refused: the folio has nothing left to invoice and the
        # token is spent.
        with self.assertRaisesRegex(UserError, "nothing to invoice|already used"):
            self._confirm(preview, folio=folio)
        self.assertEqual(len(self._moves()), 1)

    def test_only_allowed_users_and_enabled_changes(self):
        with self.assertRaises(AccessError):
            self._preview(user=self.pms_only)
        self.tool.write_enabled = False
        preview = self._preview()
        self.assertIn("Changes are disabled", " ".join(preview["warnings"]))
        with self.assertRaisesRegex(UserError, "Changes are disabled"):
            self._confirm(preview)
        self.assertFalse(self._moves())

    def test_it_is_a_write_tool(self):
        guard = get_guard(self.env["pms.ai.tools"]._ai_pms_folio_invoice)
        self.assertTrue(guard["write"])
        self.assertFalse(guard["log_result"])
        self.assertEqual(guard["apply"], "_apply_pms_folio_invoice")
        self.assertEqual(self.tool.guard_mode, "write")
        annotations = self.tool._get_tool_definition()["annotations"]
        self.assertTrue(annotations["destructiveHint"])
        self.assertTrue(self.tool.description.startswith("SENSITIVE"))
        self.assertIn("confirm=true with the token", self.tool.description)
