import math
from datetime import date

from odoo import _, models

COLOR_TITLE = "#1F3864"
COLOR_FOLIO_HEADER = "#BFBFBF"
COLOR_COL_HEADER = "#808080"
COLOR_COMMENT = "#F2F2F2"
COLOR_ALT_A = "#DEEAF1"
COLOR_ALT_B = "#FFFFFF"
COLOR_CANCEL = "#FFCCCC"
COLOR_PENDING = "#C00000"


class PmsFolioReportXlsx(models.AbstractModel):
    _name = "report.pms_folio_report.folio_report_xlsx"
    _description = "PMS Folio Excel Report"
    _inherit = "report.report_xlsx.abstract"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _selection_label(self, model_name, field_name, value):
        """Return the human-readable label for a Selection field value.

        Uses fields_get() to introspect the selection list at runtime so it
        picks up any additions made by other modules. Falls back to the raw
        technical key if the value is not found (e.g. stale data).
        """
        if not value:
            return ""
        field_defs = self.env[model_name].fields_get([field_name])
        return dict(field_defs[field_name].get("selection", [])).get(value, value)

    def _format_money(self, value, currency):
        """Format a monetary value with its currency symbol.

        `value or 0.0` coerces Odoo's False (returned for empty Float fields)
        to zero. Symbol placement follows res.currency.position ("before"/"after").
        """
        formatted = f"{value or 0.0:,.2f}"
        if currency.position == "before":
            return f"{currency.symbol} {formatted}"
        return f"{formatted} {currency.symbol}"

    def _channel_agency(self, record):
        """Return the booking source label for a folio or a reservation.

        pms.folio always has sale_channel_origin_id; pms.reservation may also
        have agency_id. getattr is used because agency_id is not present on
        all record types this method is called with.
        """
        if record.sale_channel_origin_id:
            return record.sale_channel_origin_id.name
        if getattr(record, "agency_id", False):
            return record.agency_id.name
        return ""

    def _add_formats(self, workbook):
        fmt = {}

        def _f(key, props):
            fmt[key] = workbook.add_format(props)

        _f(
            "title",
            {
                "bg_color": COLOR_TITLE,
                "font_color": "#FFFFFF",
                "bold": True,
                "font_size": 14,
                "align": "center",
                "valign": "vcenter",
                "border": 1,
            },
        )
        _f(
            "subtitle",
            {
                "italic": True,
                "align": "center",
                "valign": "vcenter",
            },
        )
        _f(
            "folio_lbl",
            {
                "bg_color": COLOR_FOLIO_HEADER,
                "italic": True,
                "font_size": 8,
                "border": 1,
                "text_wrap": True,
                "valign": "vcenter",
            },
        )
        _f("folio_data", {"bg_color": COLOR_FOLIO_HEADER, "bold": True, "border": 1})
        _f(
            "folio_data_wrap",
            {
                "bg_color": COLOR_FOLIO_HEADER,
                "bold": True,
                "border": 1,
                "text_wrap": True,
                "valign": "vcenter",
            },
        )
        _f(
            "folio_money",
            {
                "bg_color": COLOR_FOLIO_HEADER,
                "bold": True,
                "align": "right",
                "border": 1,
            },
        )
        _f(
            "folio_money_pending",
            {
                "bg_color": COLOR_FOLIO_HEADER,
                "bold": True,
                "align": "right",
                "font_color": COLOR_PENDING,
                "border": 1,
            },
        )
        _f(
            "col_hdr",
            {
                "bg_color": COLOR_COL_HEADER,
                "font_color": "#FFFFFF",
                "bold": True,
                "font_size": 8,
                "border": 1,
                "text_wrap": True,
                "valign": "vcenter",
            },
        )
        _f(
            "comment",
            {
                "bg_color": COLOR_COMMENT,
                "italic": True,
                "border": 1,
                "text_wrap": True,
                "valign": "top",
            },
        )
        _f("cell", {"border": 1, "font_size": 8})
        _f("cell_date", {"border": 1, "num_format": "dd/mm/yyyy", "font_size": 8})
        _f("cell_money", {"border": 1, "align": "right", "font_size": 8})
        _f("cell_center", {"border": 1, "align": "center", "font_size": 8})
        _f("cell_cancel", {"bg_color": COLOR_CANCEL, "border": 1, "font_size": 8})
        _f(
            "cell_cancel_date",
            {
                "bg_color": COLOR_CANCEL,
                "border": 1,
                "num_format": "dd/mm/yyyy",
                "font_size": 8,
            },
        )
        _f(
            "cell_cancel_center",
            {"bg_color": COLOR_CANCEL, "border": 1, "align": "center", "font_size": 8},
        )
        _f(
            "cell_cancel_money",
            {
                "bg_color": COLOR_CANCEL,
                "border": 1,
                "align": "right",
                "font_size": 8,
            },
        )
        for key, color in (("a", COLOR_ALT_A), ("b", COLOR_ALT_B)):
            _f(f"alt_{key}", {"bg_color": color, "border": 1, "font_size": 8})
            _f(
                f"alt_{key}_date",
                {
                    "bg_color": color,
                    "border": 1,
                    "num_format": "dd/mm/yyyy",
                    "font_size": 8,
                },
            )
            _f(
                f"alt_{key}_money",
                {"bg_color": color, "border": 1, "align": "right", "font_size": 8},
            )
            _f(
                f"alt_{key}_center",
                {"bg_color": color, "border": 1, "align": "center", "font_size": 8},
            )
        return fmt

    # ------------------------------------------------------------------
    # Sheet 1 – Summary
    # ------------------------------------------------------------------

    def _sheet_summary(self, workbook, fmt, folios):
        """Sheet 1: one block per folio with its reservations listed below."""
        ws = workbook.add_worksheet(_("Summary"))
        ws.set_landscape()
        ws.set_paper(9)  # A4
        ws.set_margins(left=0.3, right=0.3, top=0.5, bottom=0.5)
        # set_column(first_col, last_col, width) sets the column width in characters.
        # col: 0:sequence  1:guest  2:phone  3:channel_agency
        #      4:total  5:paid  6:pending  7:invoice_status  8-9:spare
        for col, width in enumerate([13, 18, 12, 14, 11, 11, 11, 14, 7, 9]):
            ws.set_column(col, col, width)

        # Title row
        ws.set_row(0, 30)
        ws.merge_range(0, 0, 0, 9, _("BOOKING REPORT – SUMMARY"), fmt["title"])

        # Subtitle: property name + generation date
        props = folios.mapped("pms_property_id")
        prop_name = props[0].name if len(props) == 1 else self.env.company.name
        today_str = date.today().strftime("%d/%m/%Y")
        subtitle = f"{prop_name}  ·  {_('Generated')}: {today_str}"
        ws.set_row(1, 18)
        ws.merge_range(1, 0, 1, 9, subtitle, fmt["subtitle"])

        row = 2
        for folio in folios:
            # Folio label row
            ws.set_row(row, 30)
            for col, lbl in enumerate(
                [
                    _("Sequence"),
                    _("Guest"),
                    _("Phone"),
                    _("Channel / Agency"),
                    _("Total"),
                    _("Paid"),
                    _("Pending"),
                    _("Invoice Status"),
                    "",
                    "",
                ]
            ):
                ws.write(row, col, lbl, fmt["folio_lbl"])
            row += 1

            # Folio data row
            invoice_status = self._selection_label(
                "pms.folio", "invoice_status", folio.invoice_status
            )
            pending = folio.pending_amount or 0.0
            pending_fmt = (
                fmt["folio_money_pending"] if pending > 0 else fmt["folio_money"]
            )
            name = folio.partner_name or ""
            mobile = folio.mobile or ""
            # Estimate wrapped line count using the column width as divisor:
            # guest col = 18 chars wide, phone col = 12 chars wide.
            n_lines = max(
                math.ceil(len(name) / 18) if name else 1,
                math.ceil(len(mobile) / 12) if mobile else 1,
            )
            if n_lines > 1:
                ws.set_row(row, n_lines * 15)  # 15 pt ≈ one text line at 8 pt font
            ws.write(row, 0, folio.name or "", fmt["folio_data"])
            ws.write(row, 1, name, fmt["folio_data_wrap"])
            ws.write(row, 2, mobile, fmt["folio_data_wrap"])
            ws.write(row, 3, self._channel_agency(folio), fmt["folio_data"])
            cur = folio.currency_id
            ws.write(
                row, 4, self._format_money(folio.amount_total, cur), fmt["folio_money"]
            )
            ws.write(
                row, 5, self._format_money(folio.invoices_paid, cur), fmt["folio_money"]
            )
            ws.write(row, 6, self._format_money(pending, cur), pending_fmt)
            ws.write(row, 7, invoice_status, fmt["folio_data"])
            ws.write(row, 8, "", fmt["folio_data"])
            ws.write(row, 9, "", fmt["folio_data"])
            row += 1

            # Comment row (merged across all columns)
            if folio.internal_comment:
                comment_text = f"{_('Comments')}: {folio.internal_comment}"
                n_lines = max(1, comment_text.count("\n") + 1)
                ws.set_row(row, 15 * n_lines)  # 15 pt ≈ one text line at 8 pt font
                ws.merge_range(row, 0, row, 9, comment_text, fmt["comment"])
                row += 1

            # Reservation column headers
            ws.set_row(row, 30)
            for col, lbl in enumerate(
                [
                    _("Res. Code"),
                    _("Status"),
                    _("Room"),
                    _("Check-in"),
                    _("Check-out"),
                    _("Nights"),
                    _("Sold Category"),
                    _("Adults"),
                    _("Children"),
                    _("Res. Total"),
                ]
            ):
                ws.write(row, col, lbl, fmt["col_hdr"])
            row += 1

            # Reservation rows
            for res in folio.reservation_ids:
                res_state = self._selection_label("pms.reservation", "state", res.state)
                is_cancel = res.state == "cancel"
                # `c` is the format key prefix. Appending "_date", "_center" or
                # "_money" selects the matching variant from the formats dict.
                c = "cell_cancel" if is_cancel else "cell"
                ws.write(row, 0, res.name or "", fmt[c])
                ws.write(row, 1, res_state, fmt[c])
                ws.write(row, 2, res.rooms or "", fmt[c])
                ws.write(row, 3, res.checkin or "", fmt[f"{c}_date"])
                ws.write(row, 4, res.checkout or "", fmt[f"{c}_date"])
                ws.write(row, 5, res.nights or 0, fmt[f"{c}_center"])
                ws.write(
                    row,
                    6,
                    res.room_type_id.name if res.room_type_id else "",
                    fmt[c],
                )
                ws.write(row, 7, res.adults or 0, fmt[f"{c}_center"])
                ws.write(row, 8, res.children or 0, fmt[f"{c}_center"])
                ws.write(
                    row,
                    9,
                    self._format_money(res.price_room_services_set, cur),
                    fmt[f"{c}_money"],
                )
                row += 1

            row += 1  # blank separator between folios

        # Limit the print area to actual content so LibreOffice does not add
        # blank pages at the end. After the loop, `row` sits two positions past
        # the last written row: one for the blank separator increment above, one
        # for the final post-increment inside the reservation loop.
        last_content_row = row - 2
        if last_content_row > 1:  # guard: at least one folio was rendered
            ws.print_area(0, 0, last_content_row, 9)

    # ------------------------------------------------------------------
    # Sheet 2 – Reservations (one row per folio)
    # ------------------------------------------------------------------

    def _sheet_reservations(self, workbook, fmt, folios):
        """Sheet 2: flat list with one row per folio (summary view for exports)."""
        ws = workbook.add_worksheet(_("Reservations"))
        headers = [
            _("Reference Code"),
            _("Status"),
            _("First Arrival"),
            _("Last Departure"),
            _("# Reservations"),
            _("Room(s)"),
            _("Guest"),
            _("Phone"),
            _("Adults"),
            _("Children"),
            _("Channel / Agency"),
            _("Internal Notes"),
            _("Total"),
            _("Paid"),
            _("Pending"),
            _("Payment Status"),
            _("Invoice Status"),
        ]
        # Column widths in characters, same order as headers above
        widths = [14, 12, 12, 12, 6, 20, 22, 14, 6, 6, 18, 30, 12, 12, 12, 14, 16]
        for col, (hdr, width) in enumerate(zip(headers, widths, strict=True)):
            ws.set_column(col, col, width)
            ws.write(0, col, hdr, fmt["col_hdr"])

        row = 1
        for folio in folios:
            state = self._selection_label("pms.folio", "state", folio.state)
            payment_state = self._selection_label(
                "pms.folio", "payment_state", folio.payment_state
            )
            invoice_status = self._selection_label(
                "pms.folio", "invoice_status", folio.invoice_status
            )
            rooms = ", ".join(
                r for r in folio.reservation_ids.mapped("preferred_room_id.name") if r
            )
            nb_res = len(folio.reservation_ids.filtered(lambda r: r.state != "cancel"))

            ws.write(row, 0, folio.name or "", fmt["cell"])
            ws.write(row, 1, state, fmt["cell"])
            ws.write(row, 2, folio.first_checkin or "", fmt["cell_date"])
            ws.write(row, 3, folio.last_checkout or "", fmt["cell_date"])
            ws.write(row, 4, nb_res, fmt["cell_center"])
            ws.write(row, 5, rooms, fmt["cell"])
            ws.write(row, 6, folio.partner_name or "", fmt["cell"])
            ws.write(row, 7, folio.mobile or "", fmt["cell"])
            ws.write(
                row, 8, sum(folio.reservation_ids.mapped("adults")), fmt["cell_center"]
            )
            ws.write(
                row,
                9,
                sum(folio.reservation_ids.mapped("children")),
                fmt["cell_center"],
            )
            ws.write(row, 10, self._channel_agency(folio), fmt["cell"])
            ws.write(row, 11, folio.internal_comment or "", fmt["cell"])
            cur = folio.currency_id
            ws.write(
                row, 12, self._format_money(folio.amount_total, cur), fmt["cell_money"]
            )
            ws.write(
                row, 13, self._format_money(folio.invoices_paid, cur), fmt["cell_money"]
            )
            ws.write(
                row,
                14,
                self._format_money(folio.pending_amount, cur),
                fmt["cell_money"],
            )
            ws.write(row, 15, payment_state, fmt["cell"])
            ws.write(row, 16, invoice_status, fmt["cell"])
            row += 1

    # ------------------------------------------------------------------
    # Sheet 3 – Room Detail (one row per reservation)
    # ------------------------------------------------------------------

    def _sheet_rooms_detail(self, workbook, fmt, folios):
        """Sheet 3: one row per reservation; folios alternate background color."""
        ws = workbook.add_worksheet(_("Room Detail"))
        headers = [
            _("Res. Code"),
            _("Res. Status"),
            _("Check-in"),
            _("Check-out"),
            _("Nights"),
            _("Room(s)"),
            _("Sold Category"),
            _("Guest"),
            _("Phone"),
            _("Adults"),
            _("Children"),
            _("Channel / Agency"),
            _("Internal Notes"),
            _("Res. Total"),
            _("Payment Status"),
        ]
        # Column widths in characters, same order as headers above
        widths = [14, 14, 12, 12, 6, 14, 18, 22, 14, 6, 6, 18, 30, 12, 14]
        for col, (hdr, width) in enumerate(zip(headers, widths, strict=True)):
            ws.set_column(col, col, width)
            ws.write(0, col, hdr, fmt["col_hdr"])

        row = 1
        for folio_idx, folio in enumerate(folios):
            # `alt` selects the format family (alt_a_* / alt_b_*) so that all
            # reservations belonging to the same folio share the same background.
            alt = "a" if folio_idx % 2 == 0 else "b"
            cur = folio.currency_id
            payment_state = self._selection_label(
                "pms.folio", "payment_state", folio.payment_state
            )
            for res in folio.reservation_ids:
                res_state = self._selection_label("pms.reservation", "state", res.state)
                ws.write(row, 0, res.name or "", fmt[f"alt_{alt}"])
                ws.write(row, 1, res_state, fmt[f"alt_{alt}"])
                ws.write(row, 2, res.checkin or "", fmt[f"alt_{alt}_date"])
                ws.write(row, 3, res.checkout or "", fmt[f"alt_{alt}_date"])
                ws.write(row, 4, res.nights or 0, fmt[f"alt_{alt}_center"])
                ws.write(row, 5, res.rooms or "", fmt[f"alt_{alt}"])
                ws.write(
                    row,
                    6,
                    res.room_type_id.name if res.room_type_id else "",
                    fmt[f"alt_{alt}"],
                )
                ws.write(row, 7, res.partner_name or "", fmt[f"alt_{alt}"])
                ws.write(row, 8, res.mobile or "", fmt[f"alt_{alt}"])
                ws.write(row, 9, res.adults or 0, fmt[f"alt_{alt}_center"])
                ws.write(row, 10, res.children or 0, fmt[f"alt_{alt}_center"])
                ws.write(row, 11, self._channel_agency(res), fmt[f"alt_{alt}"])
                ws.write(row, 12, res.folio_internal_comment or "", fmt[f"alt_{alt}"])
                ws.write(
                    row,
                    13,
                    self._format_money(res.price_room_services_set, cur),
                    fmt[f"alt_{alt}_money"],
                )
                ws.write(row, 14, payment_state, fmt[f"alt_{alt}"])
                row += 1

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def generate_xlsx_report(self, workbook, data, folios):
        fmt = self._add_formats(workbook)
        self._sheet_summary(workbook, fmt, folios)
        self._sheet_reservations(workbook, fmt, folios)
        self._sheet_rooms_detail(workbook, fmt, folios)
