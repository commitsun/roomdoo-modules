# Copyright 2026 Roomdoo - Commit[Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models

STRFTIME_TO_EXCEL = {
    "%d": "DD",
    "%m": "MM",
    "%Y": "YYYY",
    "%y": "YY",
    "%B": "MMMM",
    "%b": "MMM",
}


class PaymentXlsx(models.AbstractModel):
    _name = "report.roomdoo_payments_exporter.payment_report"
    _inherit = [
        "report.report_xlsx.abstract",
        "report.roomdoo_payments_exporter.mixin",
    ]
    _description = "Payments XLSX Report"

    def _get_date_format(self):
        code = self.env.user.lang or "en_US"
        py_fmt = self.env["res.lang"]._lang_get(code).date_format
        for py, xl in STRFTIME_TO_EXCEL.items():
            py_fmt = py_fmt.replace(py, xl)
        return py_fmt

    def _get_columns(self):
        # (label, width, row-key, format-key)
        return [
            (_("Number"), 20, "name", "text"),
            (_("Date"), 15, "date", "date"),
            (_("Type"), 20, "type_label", "text"),
            (_("Contact"), 35, "partner_name", "text"),
            (_("Payment Method"), 20, "payment_method", "text"),
            (_("Journal"), 20, "journal_name", "text"),
            (_("Folio"), 20, "folio_name", "text"),
            (_("Reference"), 20, "ref", "text"),
            (_("Registered By"), 20, "created_by", "text"),
            (_("Amount"), 15, "amount", "money"),
            (_("Currency"), 10, "currency_name", "text"),
        ]

    def _get_formats(self, workbook):
        return {
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
                    "num_format": self._get_date_format(),
                    "border": 1,
                    "valign": "vcenter",
                }
            ),
            "money": workbook.add_format(
                {
                    "num_format": "#,##0.00",
                    "border": 1,
                    "valign": "vcenter",
                }
            ),
        }

    @staticmethod
    def _write_cell(sheet, row, col, value, cell_format):
        if value is None or value is False:
            sheet.write_blank(row, col, None, cell_format)
        else:
            sheet.write(row, col, value, cell_format)

    def generate_xlsx_report(self, workbook, data, payments):
        rows = self._payment_report_rows(payments)
        fmt = self._get_formats(workbook)
        columns = self._get_columns()

        sheet = workbook.add_worksheet(_("Payments"))
        for col_idx, (label, width, _key, _fmt_key) in enumerate(columns):
            sheet.write(0, col_idx, label, fmt["header"])
            sheet.set_column(col_idx, col_idx, width)
        sheet.freeze_panes(1, 0)

        for row_idx, row in enumerate(rows, start=1):
            for col_idx, (_label, _width, key, fmt_key) in enumerate(columns):
                self._write_cell(sheet, row_idx, col_idx, row.get(key), fmt[fmt_key])

        if rows:
            sheet.autofilter(0, 0, len(rows), len(columns) - 1)
