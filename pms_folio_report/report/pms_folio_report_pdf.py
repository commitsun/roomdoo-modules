import io
import logging
import os
import subprocess
import tempfile

import xlsxwriter

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import find_in_path

_logger = logging.getLogger(__name__)


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    report_type = fields.Selection(
        selection_add=[("xlsx_pdf", "XLSX → PDF (LibreOffice)")],
        ondelete={"xlsx_pdf": "set default"},
    )

    @api.model
    def _get_report_from_name(self, report_name):
        res = super()._get_report_from_name(report_name)
        if res:
            return res
        context = self.env["res.users"].context_get()
        return (
            self.env["ir.actions.report"]
            .with_context(**context)
            .search(
                [("report_type", "=", "xlsx_pdf"), ("report_name", "=", report_name)],
                limit=1,
            )
        )

    @api.model
    def _render_xlsx_pdf(self, report_ref, docids, data):
        report_sudo = self._get_report(report_ref)
        folios = self.env[report_sudo.model].browse(docids)

        xlsx_model = self.env["report.pms_folio_report.folio_report_xlsx"]
        buf = io.BytesIO()
        workbook = xlsxwriter.Workbook(buf, {"in_memory": True})
        fmt = xlsx_model._add_formats(workbook)
        xlsx_model._sheet_summary(workbook, fmt, folios)
        workbook.close()
        xlsx_bytes = buf.getvalue()
        buf.close()

        try:
            lo_bin = find_in_path("libreoffice")
        except OSError:
            try:
                lo_bin = find_in_path("soffice")
            except OSError as exc:
                raise UserError(_("LibreOffice is not installed.")) from exc

        with tempfile.TemporaryDirectory() as tmpdir:
            xlsx_path = os.path.join(tmpdir, "report.xlsx")
            with open(xlsx_path, "wb") as fh:
                fh.write(xlsx_bytes)
            try:
                subprocess.check_output(
                    [
                        lo_bin,
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        tmpdir,
                        xlsx_path,
                        "-env:UserInstallation=file:%s"
                        % os.path.join(tmpdir, "lo_profile"),
                    ],
                    stderr=subprocess.STDOUT,
                    timeout=120,
                )
            except subprocess.CalledProcessError as exc:
                raise UserError(
                    _("LibreOffice conversion failed:\n%s")
                    % exc.output.decode(errors="replace")
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise UserError(_("LibreOffice conversion timed out.")) from exc

            with open(os.path.join(tmpdir, "report.pdf"), "rb") as fh:
                return fh.read(), "pdf"
