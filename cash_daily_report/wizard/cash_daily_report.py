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
import datetime
from io import BytesIO

import pytz
import xlsxwriter

from odoo import _, api, fields, models
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT


class CashDailyReportWizard(models.TransientModel):
    FILENAME = "cash_daily_report.xls"
    _name = "cash.daily.report.wizard"

    @api.model
    def _get_default_date_start(self):
        return datetime.datetime.now().strftime(DEFAULT_SERVER_DATE_FORMAT)

    @api.model
    def _get_default_date_end(self):
        return datetime.datetime.now().strftime(DEFAULT_SERVER_DATE_FORMAT)

    date_start = fields.Date("Start Date", default=_get_default_date_start)
    date_end = fields.Date("End Date", default=_get_default_date_end)
    xls_filename = fields.Char()
    xls_binary = fields.Binary()
    pms_property_id = fields.Many2one(
        "pms.property",
        string="Property",
        default=lambda self: self.env.user.pms_property_ids.ids,
    )

    @api.model
    def _export(self, pms_property_id):  # noqa: C901
        self.env["res.users"].browse(self.env.uid)
        file_data = BytesIO()
        workbook = xlsxwriter.Workbook(
            file_data, {"strings_to_numbers": True, "default_date_format": "dd/mm/yyyy"}
        )
        cell_format = workbook.add_format({"bold": True, "font_color": "red"})
        company_id = self.env.user.company_id
        workbook.set_properties(
            {
                "title": "Exported data from " + company_id.name,
                "subject": "Payments Data from Odoo of " + company_id.name,
                "author": "Odoo",
                "manager": "Alexandre Díaz Cuadrado",
                "company": company_id.name,
                "category": "Hoja de Calculo",
                "keywords": "payments, odoo, data, " + company_id.name,
                "comments": "Created with Python in Odoo and XlsxWriter",
            }
        )
        workbook.use_zip64()

        xls_cell_format_date = workbook.add_format({"num_format": "dd/mm/yyyy"})
        xls_cell_format_money = workbook.add_format({"num_format": "#,##0.00"})
        xls_cell_format_header = workbook.add_format({"bg_color": "#CCCCCC"})

        worksheet = workbook.add_worksheet(_("Cash Daily Report"))

        worksheet.write("A1", _("Usuario"), xls_cell_format_header)
        worksheet.write("B1", _("Referencia"), xls_cell_format_header)
        worksheet.write("C1", _("Cliente/Prov."), xls_cell_format_header)
        worksheet.write("D1", _("Fecha"), xls_cell_format_header)
        worksheet.write("E1", _("Diario"), xls_cell_format_header)
        worksheet.write("F1", _("Cantidad"), xls_cell_format_header)
        worksheet.write("G1", _("Tipo"), xls_cell_format_header)
        # worksheet.write('G1', _('Tipo'), xls_cell_format_header)

        worksheet.set_column("A:A", 15)
        worksheet.set_column("B:B", 15)
        worksheet.set_column("C:C", 15)
        worksheet.set_column("D:D", 11)
        worksheet.set_column("E:E", 10)
        worksheet.set_column("F:F", 12)
        worksheet.set_column("G:G", 10)

        account_payments_obj = self.env["account.payment"]
        account_payments = account_payments_obj.search(
            [
                ("pms_property_id", "=", self.pms_property_id.id),
                ("date", ">=", self.date_start),
                ("date", "<=", self.date_end),
                ("state", "=", "posted"),
                ("journal_id.allowed_pms_payments", "=", True),
            ]
        )
        offset = 1
        total_account_payment_amount = 0.0
        total_account_payment = 0.0
        total_account_expenses = 0.0
        payment_journals = {}
        expense_journals = {}
        count_payment_journals = {}
        count_expense_journals = {}
        total_dates = {}
        for k_payment, v_payment in enumerate(account_payments):
            folio = v_payment.folio_ids[0] if v_payment.folio_ids else False
            partner_name = v_payment.partner_id.name
            if not partner_name and folio:
                partner_name = folio.partner_name
            where = partner_name or ""
            amount = (
                v_payment.amount
                if v_payment.payment_type in ("inbound")
                else -v_payment.amount
            )
            if v_payment.payment_type == "transfer":
                ingresos = "Ingresos " + v_payment.destination_journal_id.name
                gastos = "Gastos " + v_payment.destination_journal_id.name
                where = v_payment.destination_journal_id.name
                total_account_payment += -amount
                if v_payment.destination_journal_id.name not in payment_journals:
                    payment_journals.update(
                        {v_payment.destination_journal_id.name: -amount}
                    )
                    count_payment_journals.update(
                        {v_payment.destination_journal_id.name: 1}
                    )
                else:
                    payment_journals[v_payment.destination_journal_id.name] += -amount
                    count_payment_journals[v_payment.destination_journal_id.name] += 1
                if v_payment.date not in total_dates:
                    total_dates.update(
                        {
                            v_payment.date: {
                                v_payment.destination_journal_id.name: -amount
                            }
                        }
                    )
                    total_dates[v_payment.date].update({ingresos: -amount})
                    total_dates[v_payment.date].update({gastos: 0})
                else:
                    if (
                        v_payment.destination_journal_id.name
                        not in total_dates[v_payment.date]
                    ):
                        total_dates[v_payment.date].update({ingresos: -amount})
                        total_dates[v_payment.date].update({gastos: 0})
                        total_dates[v_payment.date].update(
                            {v_payment.destination_journal_id.name: -amount}
                        )
                    else:
                        total_dates[v_payment.date][ingresos] += -amount
                        total_dates[v_payment.date][
                            v_payment.destination_journal_id.name
                        ] += -amount
            if amount < 0:
                ingresos = "Ingresos " + v_payment.journal_id.name
                gastos = "Gastos " + v_payment.journal_id.name
                total_account_expenses += -amount
                if v_payment.journal_id.name not in expense_journals:
                    expense_journals.update({v_payment.journal_id.name: amount})
                    count_expense_journals.update({v_payment.journal_id.name: 1})
                else:
                    expense_journals[v_payment.journal_id.name] += amount
                    count_expense_journals[v_payment.journal_id.name] += 1
                if v_payment.date not in total_dates:
                    total_dates.update(
                        {v_payment.date: {v_payment.journal_id.name: amount}}
                    )
                    total_dates[v_payment.date].update({gastos: -amount})
                    total_dates[v_payment.date].update({ingresos: 0})
                else:
                    if v_payment.journal_id.name not in total_dates[v_payment.date]:
                        total_dates[v_payment.date].update(
                            {v_payment.journal_id.name: amount}
                        )
                        total_dates[v_payment.date].update({gastos: -amount})
                        total_dates[v_payment.date].update({ingresos: 0})
                    else:
                        total_dates[v_payment.date][gastos] += -amount
                        total_dates[v_payment.date][v_payment.journal_id.name] += amount
            else:
                ingresos = "Ingresos " + v_payment.journal_id.name
                gastos = "Gastos " + v_payment.journal_id.name
                total_account_payment += amount
                if v_payment.journal_id.name not in payment_journals:
                    payment_journals.update({v_payment.journal_id.name: amount})
                    count_payment_journals.update({v_payment.journal_id.name: 1})
                else:
                    payment_journals[v_payment.journal_id.name] += amount
                    count_payment_journals[v_payment.journal_id.name] += 1
                if v_payment.date not in total_dates:
                    total_dates.update(
                        {v_payment.date: {v_payment.journal_id.name: amount}}
                    )
                    total_dates[v_payment.date].update({ingresos: amount})
                    total_dates[v_payment.date].update({gastos: 0})
                else:
                    if v_payment.journal_id.name not in total_dates[v_payment.date]:
                        total_dates[v_payment.date].update(
                            {v_payment.journal_id.name: amount}
                        )
                        total_dates[v_payment.date].update({ingresos: amount})
                        total_dates[v_payment.date].update({gastos: 0})
                    else:
                        total_dates[v_payment.date][v_payment.journal_id.name] += amount
                        total_dates[v_payment.date][ingresos] += amount

            worksheet.write(k_payment + offset, 0, v_payment.create_uid.login)
            worksheet.write(k_payment + offset, 1, v_payment.ref or "")
            worksheet.write(k_payment + offset, 2, where)
            worksheet.write(k_payment + offset, 3, v_payment.date, xls_cell_format_date)
            worksheet.write(k_payment + offset, 4, v_payment.journal_id.name)
            worksheet.write(k_payment + offset, 5, amount, xls_cell_format_money)
            if v_payment.is_internal_transfer:
                tipo_operacion = "Interna"
            elif v_payment.partner_type == "customer":
                tipo_operacion = "Cliente"
            elif v_payment.partner_type == "supplier":
                tipo_operacion = "Proveedor"

            worksheet.write(k_payment + offset, 6, tipo_operacion)
            total_account_payment_amount += amount
        offset += len(account_payments)
        line = offset

        worksheet.write(line + 1, 1, "Fecha/Hora:", cell_format)
        timezone = pytz.timezone(self._context.get("tz") or self.env.user.tz or "UTC")
        event_date = datetime.datetime.now()
        event_date = pytz.UTC.localize(event_date)

        event_date = event_date.astimezone(timezone)
        event_date = event_date.strftime("%d/%m/%Y %H:%M:%S")

        worksheet.write(line + 2, 1, event_date, cell_format)
        # CJACT - ignored journal restaurant Toro
        journal_cash_ids = self.env["account.journal"].search(
            [
                ("pms_property_ids", "in", self.pms_property_id.id),
                ("type", "=", "cash"),
                ("code", "!=", "CJACT"),
            ]
        )
        for journal in journal_cash_ids:
            statement = (
                self.env["account.bank.statement"]
                .sudo()
                .search(
                    [("journal_id", "=", journal.id), ("balance_end", "!=", 0)],
                    limit=1,
                )
            )
            if not statement:
                result_cash = 0
            else:
                result_cash = statement.balance_end_real
            worksheet.write(line + 3, 1, journal.name, cell_format)
            worksheet.write(line + 4, 1, result_cash, cell_format)

        result_journals = {}
        # NORMAL PAYMENTS
        # if total_account_payment != 0:
        #     line += 1
        #     worksheet.write(line, 3, _('COBROS'), xls_cell_format_header)
        #     worksheet.write(line, 4, _('UDS'), xls_cell_format_header)
        #     worksheet.write(line, 5, total_account_payment,
        #                     xls_cell_format_header)
        for journal in payment_journals:
            # line += 1
            # worksheet.write(line, 3, _(journal))
            # worksheet.write(line, 4, count_payment_journals[journal],
            #                 xls_cell_format_money)
            # worksheet.write(line, 5, payment_journals[journal],
            #                 xls_cell_format_money)
            if journal not in result_journals:
                result_journals.update({journal: payment_journals[journal]})
            else:
                result_journals[journal] += payment_journals[journal]

        for journal in expense_journals:
            # line += 1
            # worksheet.write(line, 3, _(journal))
            # worksheet.write(line, 4, count_expense_journals[journal],
            #                 xls_cell_format_money)
            # worksheet.write(line, 5, -expense_journals[journal],
            #                 xls_cell_format_money)
            if journal not in result_journals:
                result_journals.update({journal: expense_journals[journal]})
            else:
                result_journals[journal] += expense_journals[journal]

        # # TOTALS
        line += 1
        worksheet.write(line, 3, _("TOTAL"), xls_cell_format_header)
        worksheet.write(
            line,
            5,
            total_account_payment - total_account_expenses,
            xls_cell_format_header,
        )
        for journal in result_journals:
            line += 1
            worksheet.write(line, 3, _(journal))
            worksheet.write(line, 5, result_journals[journal], xls_cell_format_money)

        # line += 1
        # worksheet.write(line, 1, _('FECHA:'))
        # line += 1
        # worksheet.write(line, 1, _('NOMBRE Y FIRMA TURNO SALIENTE:'))
        # worksheet.write(line, 3, _('NOMBRE Y FIRMA TURNO ENTRANTE:'))
        # worksheet.set_landscape()
        # if not user.has_group('hotel.group_hotel_manager'):
        #     worksheet.protect()
        worksheet_day = workbook.add_worksheet(_("Por dia"))
        worksheet_day.write("A2", _("Date"), xls_cell_format_header)
        # worksheet_day.write('B2', _('Validar'), xls_cell_format_header)
        # columns_balance = {4: 'E:E', 7: 'H:H', 10: 'K:K',
        #                    13: 'N:N', 16: 'Q:Q', 19: 'T:T'}
        # i = 1
        # column_journal = {}
        # for journal in result_journals:
        #     ingresos = 'Ingresos ' + journal
        #     gastos = 'Gastos ' + journal
        #     i += 1
        #     worksheet_day.write(0, i + 2, _(journal), xls_cell_format_header)
        #     worksheet_day.write(1, i, _('Ingresos'), xls_cell_format_header)
        #     column_journal.update({ingresos: i})
        #     i += 1
        #     worksheet_day.write(1, i, _('Gastos'), xls_cell_format_header)
        #     column_journal.update({gastos: i})
        #     i += 1
        #     worksheet_day.write(1, i, _('Resultado'))
        #     column_journal.update({journal: i})
        #     if columns_balance.get(i):
        #         worksheet_day.set_column(columns_balance.get(i), 8, cell_format)

        worksheet_day.set_column("A:A", 11)

        offset = 2
        total_dates = sorted(total_dates.items(), key=lambda x: x[0])
        # for k_day, v_day in enumerate(total_dates):
        #     worksheet_day.write(k_day + offset, 0, v_day[0])
        #     for journal in v_day[1]:
        #         worksheet_day.write(
        #             k_day + offset, column_journal[journal], v_day[1][journal])
        worksheet_day.set_landscape()
        # if not user.has_group('hotel.group_hotel_manager'):
        #     worksheet_day.protect()
        workbook.close()
        file_data.seek(0)
        user = self.env.user
        user.tz or "UTC"  # noqa: B018
        now_utc = fields.Datetime.now()
        now_user = fields.Datetime.context_timestamp(self, now_utc)
        tnow = now_user.strftime("%Y-%m-%d_%H:%M:%S")
        return {
            "xls_filename": "cash_daily_report_%s.xlsx" % tnow,
            "xls_binary": base64.encodebytes(file_data.read()),
        }

    def export(self):
        self.write(self._export(self.pms_property_id.id))
        return {
            "name": _("Informe de caja diaria"),
            "res_id": self.id,
            "res_model": "cash.daily.report.wizard",
            "type": "ir.actions.act_window",
            "view_id": self.env.ref(
                "cash_daily_report.view_cash_daily_report_wizard"
            ).id,
            "view_mode": "form",
        }
