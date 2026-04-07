import json
import logging

from werkzeug.urls import url_decode

from odoo.http import (
    content_disposition,
    request,
    route,
    serialize_exception as _serialize_exception,
)
from odoo.tools import html_escape
from odoo.tools.safe_eval import safe_eval, time

from odoo.addons.report_xlsx.controllers.main import ReportController

_logger = logging.getLogger(__name__)


class XlsxPdfReportController(ReportController):
    @route()
    def report_routes(self, reportname, docids=None, converter=None, **data):
        if converter == "xlsx_pdf":
            report = request.env["ir.actions.report"]._get_report_from_name(reportname)
            context = dict(request.env.context)
            if docids:
                docids = [int(i) for i in docids.split(",")]
            if data.get("options"):
                data.update(json.loads(data.pop("options")))
            if data.get("context"):
                data["context"] = json.loads(data["context"])
                context.update(data["context"])
            pdf = report.with_context(**context)._render_xlsx_pdf(
                reportname, docids, data=data
            )[0]
            return request.make_response(
                pdf,
                headers=[
                    ("Content-Type", "application/pdf"),
                    ("Content-Length", len(pdf)),
                ],
            )
        return super().report_routes(reportname, docids, converter, **data)

    @route()
    def report_download(self, data, context=None, token=None):
        requestcontent = json.loads(data)
        url, report_type = requestcontent[0], requestcontent[1]
        if report_type != "xlsx_pdf":
            return super().report_download(data, context=context, token=token)
        try:
            reportname = url.split("/report/xlsx_pdf/")[1].split("?")[0]
            docids = None
            if "/" in reportname:
                reportname, docids = reportname.split("/")
            if docids:
                response = self.report_routes(
                    reportname, docids=docids, converter="xlsx_pdf", context=context
                )
            else:
                dl_data = dict(url_decode(url.split("?")[1]).items())
                if "context" in dl_data:
                    context, data_ctx = (
                        json.loads(context or "{}"),
                        json.loads(dl_data.pop("context")),
                    )
                    context = json.dumps({**context, **data_ctx})
                response = self.report_routes(
                    reportname, converter="xlsx_pdf", context=context, **dl_data
                )

            report = request.env["ir.actions.report"]._get_report_from_name(reportname)
            filename = "%s.pdf" % report.name
            if docids:
                ids = [int(x) for x in docids.split(",")]
                obj = request.env[report.model].browse(ids)
                if report.print_report_name and not len(obj) > 1:
                    filename = "%s.pdf" % safe_eval(
                        report.print_report_name, {"object": obj, "time": time}
                    )
            if not response.headers.get("Content-Disposition"):
                response.headers.add(
                    "Content-Disposition", content_disposition(filename)
                )
            return response
        except Exception as exc:
            _logger.exception("Error generating report %s", reportname)
            error = {
                "code": 200,
                "message": "Odoo Server Error",
                "data": _serialize_exception(exc),
            }
            return request.make_response(html_escape(json.dumps(error)))
