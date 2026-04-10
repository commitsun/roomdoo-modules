# Copyright 2026 Roomdoo - Commit[Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from collections import defaultdict

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

STRFTIME_TO_EXCEL = {
    "%d": "DD",
    "%m": "MM",
    "%Y": "YYYY",
    "%y": "YY",
    "%B": "MMMM",
    "%b": "MMM",
}


class InvoicePaymentXlsx(models.AbstractModel):
    _name = "report.roomdoo_invoices_exporter.invoice_payment_report"
    _inherit = "report.report_xlsx.abstract"
    _description = "Invoice & Payment XLSX Report"

    # ------------------------------------------------------------------
    # Locale helpers
    # ------------------------------------------------------------------

    def _get_lang(self):
        code = self.env.user.lang or "en_US"
        return self.env["res.lang"]._lang_get(code)

    @staticmethod
    def _python_date_to_excel(py_fmt):
        """Convert a Python strftime date format to an Excel date format.

        E.g. ``%d/%m/%Y`` → ``DD/MM/YYYY``.
        """
        result = py_fmt
        for py, xl in STRFTIME_TO_EXCEL.items():
            result = result.replace(py, xl)
        return result

    @staticmethod
    def _build_money_num_format(currency):
        """Build an Excel ``num_format`` string from *res.currency*.

        Uses the ``[$<symbol>]`` notation so the symbol is shown regardless of
        the viewer's locale, while thousands / decimal separators remain
        locale-aware (Excel's native behaviour).
        """
        decimals = "0" * currency.decimal_places if currency.decimal_places else "00"
        number_part = f"#,##0.{decimals}"
        symbol_token = f"[${currency.symbol}]"
        if currency.position == "before":
            return f"{symbol_token} {number_part}"
        return f"{number_part} {symbol_token}"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def generate_xlsx_report(self, workbook, data, invoices):
        invoices = invoices.sudo().filtered(lambda m: m.move_type != "entry")
        if not invoices:
            raise UserError(_("No invoices selected for export."))
        companies = invoices.mapped("company_id")
        if len(companies) > 1:
            raise UserError(
                _("Cannot export invoices from multiple companies " "in a single file.")
            )

        today = fields.Date.context_today(self)
        currency = companies.currency_id
        fmt = self._get_formats(workbook, currency)

        invoice_totals = self._write_invoices_sheet(workbook, invoices, fmt, today)
        payment_data = self._collect_payment_data(invoices)
        self._write_payments_sheet(workbook, payment_data, fmt)
        self._write_summary_sheet(workbook, invoice_totals, payment_data, fmt)

    # ------------------------------------------------------------------
    # Formats
    # ------------------------------------------------------------------

    def _get_formats(self, workbook, currency):
        money_fmt = self._build_money_num_format(currency)
        date_fmt = self._python_date_to_excel(self._get_lang().date_format)
        return {
            "_money_num_format": money_fmt,
            "header": workbook.add_format(
                {
                    "bold": True,
                    "bg_color": "#4472C4",
                    "font_color": "#FFFFFF",
                    "border": 1,
                    "text_wrap": True,
                    "valign": "vcenter",
                }
            ),
            "text": workbook.add_format({"border": 1, "valign": "vcenter"}),
            "date": workbook.add_format(
                {
                    "num_format": date_fmt,
                    "border": 1,
                    "valign": "vcenter",
                }
            ),
            "money": workbook.add_format(
                {
                    "num_format": money_fmt,
                    "border": 1,
                    "valign": "vcenter",
                }
            ),
            "bold": workbook.add_format({"bold": True, "border": 1}),
            "section": workbook.add_format(
                {
                    "bold": True,
                    "font_size": 13,
                    "bottom": 2,
                }
            ),
            "label": workbook.add_format(
                {
                    "bold": True,
                    "bg_color": "#D9E2F3",
                    "border": 1,
                }
            ),
            "value": workbook.add_format(
                {
                    "num_format": money_fmt,
                    "border": 1,
                }
            ),
            "int_value": workbook.add_format({"border": 1}),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_cell(sheet, row, col, value, fmt):
        if value is None or value is False:
            sheet.write_blank(row, col, None, fmt)
        else:
            sheet.write(row, col, value, fmt)

    @staticmethod
    def _move_sign(move):
        return -1 if move.move_type in ("out_refund", "in_refund") else 1

    @staticmethod
    def _get_maturity_lines(invoice):
        """Return receivable/payable move lines (one per payment term)."""
        return invoice.line_ids.filtered(
            lambda ln: ln.account_id.account_type
            in ("asset_receivable", "liability_payable")
        )

    def _get_payment_status_label(self, invoice, today):
        if invoice.payment_state == "paid":
            return _("Paid")
        lines = self._get_maturity_lines(invoice)
        is_overdue = any(
            line.date_maturity
            and line.date_maturity < today
            and abs(line.amount_residual) > 0.01
            for line in lines
        )
        if is_overdue:
            return _("Overdue")
        mapping = {
            "partial": _("Partial"),
            "in_payment": _("In Payment"),
            "not_paid": _("Not Paid"),
            "reversed": _("Reversed"),
        }
        return mapping.get(
            invoice.payment_state,
            dict(
                invoice._fields["payment_state"]._description_selection(invoice.env)
            ).get(invoice.payment_state, ""),
        )

    def _get_overdue_amount(self, invoice, today):
        lines = self._get_maturity_lines(invoice)
        sign = self._move_sign(invoice)
        return (
            sum(
                abs(line.amount_residual)
                for line in lines
                if line.date_maturity
                and line.date_maturity < today
                and abs(line.amount_residual) > 0.01
            )
            * sign
        )

    # ------------------------------------------------------------------
    # Sheet 1 — Invoices
    # ------------------------------------------------------------------

    def _get_invoice_columns(self):
        return [
            (_("Invoice Number"), 20, "text"),
            (_("Invoice State"), 15, "text"),
            (_("Invoice Date"), 15, "date"),
            (_("Due Date"), 15, "date"),
            (_("Customer"), 35, "text"),
            (_("VAT"), 15, "text"),
            (_("Fiscal Address"), 35, "text"),
            (_("Zip Code"), 12, "text"),
            (_("City"), 15, "text"),
            (_("Country"), 15, "text"),
            (_("Journal"), 20, "text"),
            (_("Untaxed Amount"), 15, "money"),
            (_("Taxes"), 15, "money"),
            (_("Invoice Total"), 15, "money"),
            (_("Total Paid"), 15, "money"),
            (_("Amount Due"), 15, "money"),
            (_("Overdue Amount"), 15, "money"),
            (_("Payment Status"), 15, "text"),
            (_("Currency"), 10, "text"),
            (_("Internal Reference"), 20, "text"),
            (_("Notes"), 30, "text"),
        ]

    def _get_invoice_row(self, inv, today):
        partner = inv.commercial_partner_id or inv.partner_id
        sign = self._move_sign(inv)

        amount_untaxed = inv.amount_untaxed * sign
        amount_tax = inv.amount_tax * sign
        amount_total = inv.amount_total * sign
        amount_residual = inv.amount_residual
        amount_paid = amount_total - amount_residual
        overdue = self._get_overdue_amount(inv, today)
        payment_status = self._get_payment_status_label(inv, today)

        ref = ""
        if hasattr(inv, "folio_ids") and inv.folio_ids:
            ref = ", ".join(inv.folio_ids.mapped("name"))
        elif inv.ref:
            ref = inv.ref
        elif inv.invoice_origin:
            ref = inv.invoice_origin

        notes = html2plaintext(inv.narration) if inv.narration else ""
        address_parts = [p for p in [partner.street, partner.street2] if p]
        state_label = dict(inv._fields["state"]._description_selection(inv.env)).get(
            inv.state, inv.state
        )

        return [
            (inv.name or "", "text"),
            (state_label, "text"),
            (inv.invoice_date, "date"),
            (inv.invoice_date_due, "date"),
            (partner.name or "", "text"),
            (partner.vat or "", "text"),
            (", ".join(address_parts), "text"),
            (partner.zip or "", "text"),
            (partner.city or "", "text"),
            (partner.country_id.name if partner.country_id else "", "text"),
            (inv.journal_id.name or "", "text"),
            (amount_untaxed, "money"),
            (amount_tax, "money"),
            (amount_total, "money"),
            (amount_paid, "money"),
            (amount_residual, "money"),
            (overdue, "money"),
            (payment_status, "text"),
            (inv.currency_id.name or "", "text"),
            (ref, "text"),
            (notes, "text"),
        ]

    def _write_invoices_sheet(self, workbook, invoices, fmt, today):
        sheet = workbook.add_worksheet(_("Invoices"))
        columns = self._get_invoice_columns()

        for col_idx, (label, width, _fmt_key) in enumerate(columns):
            sheet.write(0, col_idx, label, fmt["header"])
            sheet.set_column(col_idx, col_idx, width)
        sheet.freeze_panes(1, 0)

        totals = {
            "count": 0,
            "total_untaxed": 0.0,
            "total_tax": 0.0,
            "total_amount": 0.0,
            "total_paid": 0.0,
            "total_residual": 0.0,
            "total_overdue": 0.0,
            "count_cancelled": 0,
            "count_rectified": 0,
        }

        for row_idx, inv in enumerate(invoices, start=1):
            row_data = self._get_invoice_row(inv, today)
            for col_idx, (value, fmt_key) in enumerate(row_data):
                self._write_cell(sheet, row_idx, col_idx, value, fmt[fmt_key])

            sign = self._move_sign(inv)
            amount_untaxed = inv.amount_untaxed * sign
            amount_tax = inv.amount_tax * sign
            amount_total = inv.amount_total * sign

            totals["count"] += 1
            totals["total_untaxed"] += amount_untaxed
            totals["total_tax"] += amount_tax
            totals["total_amount"] += amount_total
            totals["total_paid"] += amount_total - inv.amount_residual
            totals["total_residual"] += inv.amount_residual
            totals["total_overdue"] += self._get_overdue_amount(inv, today)
            if inv.state == "cancel":
                totals["count_cancelled"] += 1
            if inv.move_type in ("out_refund", "in_refund"):
                totals["count_rectified"] += 1

        if invoices:
            sheet.autofilter(0, 0, len(invoices), len(columns) - 1)

        return totals

    # ------------------------------------------------------------------
    # Payment data collection
    # ------------------------------------------------------------------

    def _collect_payment_data(self, invoices):
        # 1) Maturity lines for all invoices (receivable / payable)
        maturity_lines = self.env["account.move.line"].search(
            [
                ("move_id", "in", invoices.ids),
                (
                    "account_id.account_type",
                    "in",
                    ("asset_receivable", "liability_payable"),
                ),
            ]
        )
        if not maturity_lines:
            return []

        # Map maturity line → invoice
        line_to_invoice = {}
        for ln in maturity_lines:
            line_to_invoice[ln.id] = ln.move_id

        # 2) All partial reconciles in a single SQL (batch version of
        #    _get_all_reconciled_invoice_partials)
        self.env["account.partial.reconcile"].flush_model(
            [
                "credit_amount_currency",
                "credit_move_id",
                "debit_amount_currency",
                "debit_move_id",
            ]
        )
        query = """
            SELECT
                part.debit_amount_currency AS amount,
                part.credit_move_id AS counterpart_line_id,
                part.debit_move_id AS invoice_line_id
            FROM account_partial_reconcile part
            WHERE part.debit_move_id IN %s

            UNION ALL

            SELECT
                part.credit_amount_currency AS amount,
                part.debit_move_id AS counterpart_line_id,
                part.credit_move_id AS invoice_line_id
            FROM account_partial_reconcile part
            WHERE part.credit_move_id IN %s
        """
        line_ids_tuple = tuple(maturity_lines.ids)
        self.env.cr.execute(query, [line_ids_tuple, line_ids_tuple])
        partials = self.env.cr.dictfetchall()

        if not partials:
            return []

        # 3) Counterpart lines → payments (batch browse)
        counterpart_ids = {r["counterpart_line_id"] for r in partials}
        counterpart_lines = {
            ln.id: ln
            for ln in self.env["account.move.line"].browse(list(counterpart_ids))
        }

        # 4) All related account.payment records (batch)
        payment_ids = {
            ln.payment_id.id for ln in counterpart_lines.values() if ln.payment_id
        }
        payment_map = (
            {p.id: p for p in self.env["account.payment"].browse(list(payment_ids))}
            if payment_ids
            else {}
        )

        # 5) Build result
        lines = []
        for row in partials:
            inv = line_to_invoice.get(row["invoice_line_id"])
            if not inv:
                continue
            cp_line = counterpart_lines.get(row["counterpart_line_id"])
            if not cp_line:
                continue
            payment_rec = (
                payment_map.get(cp_line.payment_id.id) if cp_line.payment_id else None
            )

            if not payment_rec:
                continue

            sign = self._move_sign(inv)
            ref = ""
            if payment_rec:
                ref = (
                    f"{payment_rec.name} ({payment_rec.ref})"
                    if payment_rec.ref
                    else payment_rec.name or ""
                )

            lines.append(
                {
                    "invoice_name": inv.name,
                    "invoice_date": inv.invoice_date,
                    "partner_name": (inv.commercial_partner_id or inv.partner_id).name
                    or "",
                    "payment_date": (payment_rec.date if payment_rec else cp_line.date),
                    "payment_method": (
                        payment_rec.payment_method_line_id.name
                        if payment_rec and payment_rec.payment_method_line_id
                        else ""
                    ),
                    "amount": row["amount"] * sign,
                    "currency": inv.currency_id.name or "",
                    "ref": ref,
                    "journal_name": (
                        payment_rec.journal_id.name
                        if payment_rec
                        else cp_line.journal_id.name or ""
                    ),
                    "user": (payment_rec.create_uid.name if payment_rec else ""),
                    "notes": "",
                }
            )
        return lines

    # ------------------------------------------------------------------
    # Sheet 2 — Payments
    # ------------------------------------------------------------------

    def _get_payment_columns(self):
        return [
            (_("Invoice Number"), 20, "text"),
            (_("Invoice Date"), 15, "date"),
            (_("Customer"), 35, "text"),
            (_("Payment Date"), 15, "date"),
            (_("Payment Method"), 20, "text"),
            (_("Payment Amount"), 15, "money"),
            (_("Currency"), 10, "text"),
            (_("Payment Reference"), 20, "text"),
            (_("Payment Journal"), 20, "text"),
            (_("User"), 20, "text"),
            (_("Notes"), 30, "text"),
        ]

    _PAYMENT_KEYS = [
        "invoice_name",
        "invoice_date",
        "partner_name",
        "payment_date",
        "payment_method",
        "amount",
        "currency",
        "ref",
        "journal_name",
        "user",
        "notes",
    ]

    def _write_payments_sheet(self, workbook, payment_data, fmt):
        sheet = workbook.add_worksheet(_("Payments"))
        columns = self._get_payment_columns()

        for col_idx, (label, width, _fmt_key) in enumerate(columns):
            sheet.write(0, col_idx, label, fmt["header"])
            sheet.set_column(col_idx, col_idx, width)
        sheet.freeze_panes(1, 0)

        for row_idx, pmt in enumerate(payment_data, start=1):
            for col_idx, key in enumerate(self._PAYMENT_KEYS):
                value = pmt.get(key, "")
                fmt_key = columns[col_idx][2]
                self._write_cell(sheet, row_idx, col_idx, value, fmt[fmt_key])

        if payment_data:
            sheet.autofilter(0, 0, len(payment_data), len(columns) - 1)

    # ------------------------------------------------------------------
    # Sheet 3 — Summary
    # ------------------------------------------------------------------

    def _write_summary_sheet(self, workbook, invoice_totals, payment_data, fmt):
        sheet = workbook.add_worksheet(_("Summary"))
        sheet.set_column(0, 0, 35)
        sheet.set_column(1, 1, 20)
        row = 0

        # — Invoicing —
        sheet.write(row, 0, _("INVOICING"), fmt["section"])
        row += 1
        for label, value, is_int in [
            (_("Total invoices"), invoice_totals["count"], True),
            (_("Total untaxed amount"), invoice_totals["total_untaxed"], False),
            (_("Total taxes"), invoice_totals["total_tax"], False),
            (_("Total invoiced"), invoice_totals["total_amount"], False),
            (_("Cancelled invoices"), invoice_totals["count_cancelled"], True),
            (_("Credit notes"), invoice_totals["count_rectified"], True),
        ]:
            sheet.write(row, 0, label, fmt["label"])
            sheet.write(row, 1, value, fmt["int_value"] if is_int else fmt["value"])
            row += 1
        row += 1

        # — Collections —
        sheet.write(row, 0, _("COLLECTIONS"), fmt["section"])
        row += 1
        for label, value in [
            (_("Total collected"), invoice_totals["total_paid"]),
            (_("Total pending"), invoice_totals["total_residual"]),
            (_("Total overdue"), invoice_totals["total_overdue"]),
        ]:
            sheet.write(row, 0, label, fmt["label"])
            sheet.write(row, 1, value, fmt["value"])
            row += 1
        row += 1

        # — Collections by payment method —
        sheet.write(row, 0, _("COLLECTIONS BY PAYMENT METHOD"), fmt["section"])
        row += 1
        sheet.write(row, 0, _("Payment Method"), fmt["label"])
        sheet.write(row, 1, _("Total Collected"), fmt["label"])
        row += 1

        method_totals = defaultdict(float)
        for pmt in payment_data:
            method = pmt.get("journal_name") or _("Other")
            method_totals[method] += pmt.get("amount", 0.0)

        for method, total in sorted(method_totals.items()):
            sheet.write(row, 0, method, fmt["text"])
            sheet.write(row, 1, total, fmt["value"])
            row += 1
        row += 1

        # — Control —
        sheet.write(row, 0, _("CONTROL"), fmt["section"])
        row += 1
        total_inv = invoice_totals["total_amount"]
        total_paid = invoice_totals["total_paid"]
        total_pend = invoice_totals["total_residual"]
        diff = total_inv - total_paid - total_pend

        for label, value in [
            (_("Total invoiced"), total_inv),
            (_("Total collected"), total_paid),
            (_("Total pending"), total_pend),
        ]:
            sheet.write(row, 0, label, fmt["label"])
            sheet.write(row, 1, value, fmt["value"])
            row += 1

        sheet.write(row, 0, _("Difference (should be 0)"), fmt["label"])
        diff_fmt = workbook.add_format(
            {
                "num_format": fmt["_money_num_format"],
                "border": 1,
                "bold": True,
                "font_color": "#FF0000" if abs(diff) > 0.01 else "#008000",
            }
        )
        sheet.write(row, 1, diff, diff_fmt)
