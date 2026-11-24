"""Files exported by the AI tools: XLSX or CSV attachments of the user.

A listing tool that cannot return every row in the chat builds its rows as
for the chat and passes them to ``_ai_export_file`` with the description of
the columns. It must be a write tool: read tools run in a read-only
transaction, which cannot create the attachment.
"""

import csv
import io
from datetime import datetime, time

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import xlsxwriter

EXPORT_MIMETYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}
EXPORT_FORMATS = list(EXPORT_MIMETYPES)
# A text cell starting with one of these is a formula for a spreadsheet: the
# CSV writes it with a leading apostrophe, so that it opens as text.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
XLSX_FORMATS = {
    "integer": "0",
    "number": "#,##0.00",
    "date": "dd/mm/yyyy",
}
CSV_DATE_FORMAT = "%d/%m/%Y"
MAX_COLUMN_WIDTH = 50


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_export_file(self, rows, columns, filename, file_format, description=None):
        """Save ``rows`` as an XLSX or CSV file in an attachment of the user.

        :param rows: list of dicts.
        :param columns: list of dicts with ``key`` (of the rows), ``label``
            (header), ``kind`` (``text`` by default, ``integer``, ``number``,
            ``date`` or ``boolean``) and ``total`` (sum the column in the
            totals row of the XLSX). The first column holds the totals label.
        :param filename: name of the file, with its extension.
        :param file_format: ``xlsx`` or ``csv``.
        :param description: what was exported, kept in the attachment as the
            record of the export.
        :return: the ``ir.attachment``.

        The attachment is created with the rights of the user and without a
        linked record. Odoo 16 then lets only its creator, and the
        administrators, read it. Linking it to the user (``res.users``) would
        open it to every internal user allowed to read that user.
        """
        builders = {"xlsx": self._ai_export_xlsx, "csv": self._ai_export_csv}
        if file_format not in builders:
            raise UserError(_("Unknown file format %s.") % file_format)
        content = builders[file_format](rows, columns)
        return self.env["ir.attachment"].create(
            {
                "name": filename,
                "raw": content,
                "mimetype": EXPORT_MIMETYPES[file_format],
                "description": description,
            }
        )

    @api.model
    def _ai_export_url(self, attachment):
        """Absolute download link; it opens with the session of its owner."""
        return f"{attachment.get_base_url()}/web/content/{attachment.id}?download=true"

    @api.model
    def _ai_export_xlsx(self, rows, columns):
        """One sheet: header, a typed row per item and a totals row."""
        if not xlsxwriter:
            raise UserError(_("XLSX files cannot be created here: use csv."))
        from xlsxwriter.utility import xl_range

        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
        sheet = workbook.add_worksheet()
        bold = workbook.add_format({"bold": True})
        formats = {
            kind: workbook.add_format({"num_format": num_format})
            for kind, num_format in XLSX_FORMATS.items()
        }
        total_format = workbook.add_format(
            {"bold": True, "num_format": XLSX_FORMATS["number"]}
        )
        widths = [len(column["label"]) for column in columns]
        for col, column in enumerate(columns):
            sheet.write_string(0, col, column["label"], bold)
        for line, row in enumerate(rows, start=1):
            for col, column in enumerate(columns):
                text = self._ai_export_xlsx_cell(
                    sheet, line, col, row.get(column["key"]), column, formats
                )
                widths[col] = max(widths[col], len(text))
        totals_line = len(rows) + 1
        sheet.write_string(totals_line, 0, _("Total"), bold)
        for col, column in enumerate(columns):
            if not column.get("total"):
                continue
            value = round(sum(row.get(column["key"]) or 0 for row in rows), 2)
            if rows:
                # SUBTOTAL leaves out the rows hidden by a filter; the value
                # is stored too, for readers that do not compute formulas.
                cells = xl_range(1, col, len(rows), col)
                sheet.write_formula(
                    totals_line, col, f"=SUBTOTAL(9,{cells})", total_format, value
                )
            else:
                sheet.write_number(totals_line, col, value, total_format)
        for col, width in enumerate(widths):
            sheet.set_column(col, col, min(width + 2, MAX_COLUMN_WIDTH))
        sheet.freeze_panes(1, 0)
        sheet.autofilter(0, 0, max(len(rows), 1), len(columns) - 1)
        workbook.close()
        return buffer.getvalue()

    @api.model
    def _ai_export_xlsx_cell(self, sheet, line, col, value, column, formats):
        """Write one typed cell; return its text, to size the column."""
        kind = column.get("kind")
        if kind == "boolean":
            text = _("Yes") if value else _("No")
            sheet.write_string(line, col, text)
            return text
        if value is None or value is False or value == "":
            return ""
        if kind in ("integer", "number"):
            sheet.write_number(line, col, value, formats[kind])
            return f"{value:,.2f}" if kind == "number" else str(value)
        if kind == "date":
            day = fields.Date.to_date(value)
            sheet.write_datetime(
                line, col, datetime.combine(day, time.min), formats["date"]
            )
            return "00/00/0000"
        # Always as text: write() would turn a text starting with = into a
        # formula.
        text = str(value)
        sheet.write_string(line, col, text)
        return text

    @api.model
    def _ai_export_csv(self, rows, columns):
        """CSV as a Spanish Excel opens it: semicolons, decimal comma and
        UTF-8 with BOM. No totals row, so that every line is an item."""
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
        writer.writerow([column["label"] for column in columns])
        for row in rows:
            writer.writerow(
                [
                    self._ai_export_csv_cell(row.get(column["key"]), column)
                    for column in columns
                ]
            )
        return buffer.getvalue().encode("utf-8-sig")

    @api.model
    def _ai_export_csv_cell(self, value, column):
        kind = column.get("kind")
        if kind == "boolean":
            return _("Yes") if value else _("No")
        if value is None or value is False:
            return ""
        if kind == "number":
            return f"{value:.2f}".replace(".", ",")
        if kind == "integer":
            return str(int(value))
        if kind == "date":
            return fields.Date.to_date(value).strftime(CSV_DATE_FORMAT)
        text = str(value)
        return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text
