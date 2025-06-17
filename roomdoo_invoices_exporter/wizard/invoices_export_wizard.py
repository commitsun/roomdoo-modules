##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2018 Alexandre Díaz <dev@redneboa.es>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
import base64
import logging
from io import BytesIO
from itertools import cycle

import xlsxwriter

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class RoomdooInvoicesExporter(models.TransientModel):
    _name = "roomdoo.invoices.exporter"

    date_start = fields.Date("Start Date")
    date_end = fields.Date("End Date")
    export_journals = fields.Boolean("Export Account Movements?", default=True)
    export_invoices = fields.Boolean("Export Invoices?", default=True)
    property_ids = fields.Many2many(string="Properties", comodel_name="pms.property")
    company_id = fields.Many2one(
        string="Company",
        help="The company",
        required=True,
        comodel_name="res.company",
    )
    journal_ids = fields.Many2many(
        string="Journals",
        help="Journals to include in the report",
        comodel_name="account.journal",
        relation="roomdoo_invoices_exporter_journal_rel",
        column1="wizard_id",
        column2="journal_id",
    )
    seat_num = fields.Integer("Seat Number Start", default=1)
    xls_journals_filename = fields.Char()
    xls_journals_binary = fields.Binary()

    @api.onchange("company_id")
    def onchange_property_id(self):
        if (
            self.property_ids
            and self.company_id
            and any(x.company_id != self.company_id for x in self.property_ids)
        ):
            raise UserError(
                _(
                    """
                    Alguno de los hoteles seleccionados no es de esta compañía,
                    elimina o modifica los hoteles para seleccionar esta
                     compañía
                    """
                )
            )

    @api.model
    def _export_payments(self):
        file_data = BytesIO()
        workbook = xlsxwriter.Workbook(
            file_data, {"strings_to_numbers": True, "default_date_format": "dd/mm/yyyy"}
        )

        company_id = self.env.user.company_id
        workbook.set_properties(
            {
                "title": "Exported data from " + company_id.name,
                "subject": "PMS Data from Odoo of " + company_id.name,
                "author": "Roomdoo PMS",
                "manager": "Admin",
                "company": company_id.name,
                "category": "Hoja de Calculo",
                "keywords": "pms, odoo, data, " + company_id.name,
                "comments": "Created with Python in Odoo and XlsxWriter",
            }
        )
        workbook.use_zip64()

        xls_cell_format_header = workbook.add_format(
            {"bg_color": "#000000", "font_color": "#FFFFFF"}
        )

        worksheet = workbook.add_worksheet("Simples-1")

        worksheet.write("A1", _("Diario"), xls_cell_format_header)
        worksheet.write("B1", _("Estado"), xls_cell_format_header)
        worksheet.write("C1", _("Num Factura"), xls_cell_format_header)
        worksheet.write("D1", _("Cliente/Prov."), xls_cell_format_header)
        worksheet.write("E1", _("Origen"), xls_cell_format_header)
        worksheet.write("F1", _("Fecha de Factura"), xls_cell_format_header)
        worksheet.write("G1", _("NIF"), xls_cell_format_header)
        worksheet.write("H1", _("Base Imponible"), xls_cell_format_header)
        worksheet.write("I1", _("Impuestos"), xls_cell_format_header)
        worksheet.write("J1", _("Total"), xls_cell_format_header)
        worksheet.write("K1", _("Pendiente"), xls_cell_format_header)
        worksheet.write("L1", _("Tipo"), xls_cell_format_header)
        worksheet.write("M1", _("Pagos"), xls_cell_format_header)
        worksheet.write("N1", _("Importe"), xls_cell_format_header)
        worksheet.write("O1", _("Fecha"), xls_cell_format_header)
        worksheet.write("P1", _("Referencia"), xls_cell_format_header)

        worksheet.set_column("A:A", 25)
        worksheet.set_column("B:B", 10)
        worksheet.set_column("C:C", 25)
        worksheet.set_column("D:D", 50)
        worksheet.set_column("E:E", 15)
        worksheet.set_column("F:F", 15)
        worksheet.set_column("G:G", 15)
        worksheet.set_column("H:H", 9)
        worksheet.set_column("I:I", 9)
        worksheet.set_column("J:J", 9)
        worksheet.set_column("K:K", 9)
        worksheet.set_column("L:L", 18)
        worksheet.set_column("M:M", 25)
        worksheet.set_column("N:N", 9)
        worksheet.set_column("O:O", 15)
        worksheet.set_column("P:P", 20)

        account_inv_obj = self.env["account.move"]
        domain = [
            ("date", ">=", self.date_start),
            ("date", "<=", self.date_end),
            ("company_id", "=", self.company_id.id),
            (
                "pms_property_id",
                "in",
                self.property_ids.ids if self.property_ids else [],
            ),
            ("move_type", "!=", "entry"),
        ]
        if self.journal_ids:
            domain.append(("journal_id", "in", self.journal_ids.ids))

        account_invs = account_inv_obj.search(domain, order="name")
        nrow = 1
        xls_cell_format_date1 = workbook.add_format(
            {"num_format": "dd/mm/yyyy", "bg_color": "#FFFFFF"}
        )
        xls_cell_format_date2 = workbook.add_format(
            {"num_format": "dd/mm/yyyy", "bg_color": "#CCCCCC"}
        )
        xls_cell_format_money1 = workbook.add_format(
            {"num_format": "#,##0.00", "bg_color": "#FFFFFF"}
        )
        xls_cell_format_money2 = workbook.add_format(
            {"num_format": "#,##0.00", "bg_color": "#CCCCCC"}
        )
        data_format1 = workbook.add_format({"bg_color": "#FFFFFF"})
        data_format2 = workbook.add_format({"bg_color": "#CCCCCC"})
        data_formats = cycle([data_format1, data_format2])
        date_formats = cycle([xls_cell_format_date1, xls_cell_format_date2])
        money_formats = cycle([xls_cell_format_money1, xls_cell_format_money2])
        for inv in account_invs:
            data_format = next(data_formats)
            date_format = next(date_formats)
            money_format = next(money_formats)
            country_code = ""
            vat_partner = False
            if inv.partner_id.vat:
                vat_partner = inv.partner_id.vat
            elif inv.partner_id.aeat_identification:
                vat_partner = inv.partner_id.aeat_identification
            country_partner = inv.partner_id.country_id
            if country_partner:
                country_code = country_partner.code
                if inv.partner_id.vat:
                    vat_partner = (
                        inv.partner_id.vat[2:]
                        if inv.partner_id.vat[2:] == country_code
                        else inv.partner_id.vat
                    )

            if not vat_partner and inv.partner_id.vat:
                vat_partner = inv.partner_id.vat
            origin = ""
            signed = 1
            if inv.move_type == "out_refund":
                origin = inv.invoice_origin
                signed = -1
            elif inv.folio_ids:
                origin = ",".join([fol.name for fol in inv.folio_ids])

            state = inv._fields["state"].selection
            state_dict = dict(state)
            state = state_dict.get(inv.state)

            move_type = inv._fields["move_type"].selection
            move_type_dict = dict(move_type)
            move_type = move_type_dict.get(inv.move_type)
            payments_dict = inv.invoice_payments_widget

            if not payments_dict:
                worksheet.write(nrow, 0, inv.journal_id.name)
                worksheet.write(nrow, 1, state)
                worksheet.write(
                    nrow,
                    2,
                    (
                        inv.move_type in ("in_invoice", "in_refund")
                        and inv.ref
                        or inv.name
                    ),
                )
                worksheet.write(nrow, 3, inv.partner_id.name)
                worksheet.write(nrow, 4, origin)
                worksheet.write(nrow, 5, inv.invoice_date, date_format)
                worksheet.write(nrow, 6, vat_partner)
                worksheet.write(nrow, 7, inv.amount_untaxed * signed, money_format)
                worksheet.write(nrow, 8, inv.amount_tax * signed, money_format)
                worksheet.write(nrow, 9, inv.amount_total * signed, money_format)
                worksheet.write(nrow, 10, inv.amount_residual * signed, money_format)
                worksheet.write(nrow, 11, move_type)
                worksheet.set_row(nrow, cell_format=data_format)
                nrow += 1
            else:
                for payment in payments_dict.get("content"):
                    payment_date = fields.Date.from_string(payment.get("date"))
                    worksheet.write(nrow, 0, inv.journal_id.name)
                    worksheet.write(nrow, 1, state)
                    worksheet.write(
                        nrow,
                        2,
                        inv.move_type in ("in_invoice", "in_refund")
                        and inv.ref
                        or inv.name,
                    )
                    worksheet.write(nrow, 3, inv.partner_id.name)
                    worksheet.write(nrow, 4, origin)
                    worksheet.write(nrow, 5, inv.invoice_date, date_format)
                    worksheet.write(nrow, 6, vat_partner)
                    worksheet.write(nrow, 7, inv.amount_untaxed * signed, money_format)
                    worksheet.write(nrow, 8, inv.amount_tax * signed, money_format)
                    worksheet.write(nrow, 9, inv.amount_total * signed, money_format)
                    worksheet.write(
                        nrow, 10, inv.amount_residual * signed, money_format
                    )
                    worksheet.write(nrow, 11, move_type)
                    worksheet.write(nrow, 12, payment["journal_name"])
                    worksheet.write(
                        nrow, 13, float(payment["amount"]) * signed, money_format
                    )
                    worksheet.write(nrow, 14, payment_date, date_format)
                    worksheet.write(nrow, 15, payment["ref"])
                    worksheet.set_row(nrow, cell_format=data_format)
                    nrow += 1
        workbook.close()
        file_data.seek(0)
        tnow = str(fields.Datetime.now()).replace(" ", "_")
        _logger.info("▶ Facturas encontradas: %s", len(account_invs))
        _logger.info("▶ Primera factura: %s", account_invs[:1].mapped("name"))
        _logger.info(
            "▶ Primer widget: %s", account_invs[:1].mapped("invoice_payments_widget")
        )
        return {
            "xls_journals_filename": f"pagos_facturas_{tnow}.xlsx",
            "xls_journals_binary": base64.encodebytes(file_data.read()),
        }

    def export(self):
        """
        Exports invoice data based on the selected options
        and updates the record.

        This method performs the following actions:
        - If `export_journals` is enabled, it calls `_export_payments()`
        to retrieve
          payment data and updates the `towrite` dictionary with the results.
        - If there is any data to write, it updates the current record using
        the `write()` method.
        - Returns an action dictionary to open the "Invoices export" form view.

        Returns:
            dict: An action dictionary containing the following keys:
                - "name": The name of the action ("Invoices export").
                - "res_id": The ID of the current record.
                - "res_model": The model name ("roomdoo.invoices.exporter").
                - "type": The type of action ("ir.actions.act_window").
                - "view_id": The ID of the form view to display.
                - "view_mode": The mode of the view ("form").
        """
        self.ensure_one()
        towrite = {}
        if self.export_journals:
            towrite.update(self._export_payments())
        if any(towrite):
            self.write(towrite)
        return {
            "name": _("Invoices export"),
            "res_id": self.id,
            "res_model": "roomdoo.invoices.exporter",
            "type": "ir.actions.act_window",
            "view_id": self.env.ref(
                "roomdoo_invoices_exporter.view_roomdoo_invoices_exporter"
            ).id,
            "view_mode": "form",
            "target": "new",
        }
