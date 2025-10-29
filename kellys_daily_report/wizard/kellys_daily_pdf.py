##############################################################################
#
#    Odoo, Open Source Management Solution
#    Copyright (C) 2018-2021 Jose Luis Algara Toledo <osotranquilo@gmail.com>
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
from datetime import date, timedelta
from io import BytesIO

import xlsxwriter

from odoo import _, api, fields, models


class KellysWizard(models.TransientModel):
    _name = "kellysreport"
    _description = "Kellys report"

    @api.model
    def _get_default_date(self):
        return date.today()

    def _get_default_habitaciones(self):
        return self.calculalimpiar(date.today())

    date_start = fields.Date("Fecha del listado", default=_get_default_date)
    habitaciones = fields.Many2many(
        "kellysrooms", string="Limpieza:", default=_get_default_habitaciones
    )
    order = fields.Selection(
        [
            ("kelly ASC", "Calendario"),
            ("kelly ASC, tipo ASC", "Limpiar como... y orden en el Calendario"),
            ("kelly ASC, tipo ASC, checkin ASC", "Limpiar como... y Hora de entrada"),
        ],
        "Orden de impresión",
        default="kelly ASC, tipo ASC, checkin ASC",
        required=True,
        help="Establece el orden en el que se imprimira el listado",
    )

    xls_filename = fields.Char()
    xls_binary = fields.Binary("Export data")
    pms_property_id = fields.Many2one(
        "pms.property",
        string="Property",
        default=lambda self: self.env.user.pms_property_id.id or False,
    )

    def calculate_report(self):
        self.habitaciones = self.calculalimpiar(self.date_start)
        return

    def calculalimpiar(self, fechalimpieza=False):
        if not fechalimpieza:
            fechalimpieza = date.today()
        grids = self.env["pms.room"].search(
            [("pms_property_id", "=", self.pms_property_id.id)],
            order="sequence ASC",
        )
        grids2 = self.env["kellysrooms"]
        listid = []
        for x in grids:
            reservations = self.env["pms.reservation"].search(
                [
                    ("pms_property_id", "=", self.pms_property_id.id),
                    ("checkin", "<=", fechalimpieza),
                    ("checkout", ">=", fechalimpieza),
                    ("state", "!=", "cancel"),
                ],
                order="checkin ASC",
            )
            # Filter by room in date target
            reservations = reservations.filtered(
                lambda r: (
                    any(
                        line.date == fechalimpieza and line.room_id.id == x.id  # noqa: B023
                        for line in r.reservation_line_ids
                    )
                    or (
                        r.checkout == fechalimpieza
                        and r.reservation_line_ids[-1].room_id.id == x.id  # noqa: B023
                    )
                )
            )
            tipos = False
            if len(reservations) != 0:
                if len(reservations) == 2:
                    tipos = "1"
                    # Salida y etrada
                    checkinhour = reservations[1].checkin
                    checkouthour = reservations[1].checkout
                else:
                    room_id_today = (
                        reservations[0]
                        .reservation_line_ids.filtered(
                            lambda line: line.date == fechalimpieza
                        )
                        .room_id
                    )

                    room_id_yesterday = (
                        reservations[0]
                        .reservation_line_ids.filtered(
                            lambda line: line.date == fechalimpieza - timedelta(days=1)
                        )
                        .room_id
                    )
                    if reservations[0].checkin == fechalimpieza or (
                        room_id_today
                        and room_id_yesterday
                        and room_id_today != room_id_yesterday
                    ):
                        checkinhour = reservations[0].checkin
                        checkouthour = reservations[0].checkout
                        tipos = "3"
                        # Revisar
                    elif reservations[0].checkout == fechalimpieza:
                        checkinhour = "no prevista"
                        checkouthour = ""
                        tipos = "1"
                        # Salida
                    else:
                        checkinhour = reservations[0].checkin
                        checkouthour = reservations[0].checkout
                        tipos = "2"
                        # Cliente
                        if reservations[0].reservation_type == "staff":
                            checkinhour = reservations[0].checkin
                            checkouthour = reservations[0].checkout
                            tipos = "4"
                            # Staff
                if reservations[0].reservation_type == "out":
                    checkinhour = reservations[0].checkin
                    checkouthour = reservations[0].checkout
                    tipos = "5"
                    # Averiada
            if tipos is not False:
                reservation = reservations[0]
                if reservation.checkout == fechalimpieza:
                    room = reservation.reservation_line_ids.filtered(
                        lambda line: line.date == (fechalimpieza - timedelta(days=1))
                    ).room_id
                else:
                    room = reservation.reservation_line_ids.filtered(
                        lambda line: line.date == fechalimpieza
                    ).room_id
                listid.append(
                    grids2.create(
                        {
                            "habitacion": room.name,
                            "habitacionid": room.id,
                            "tipo": tipos,
                            "notas": " / ".join(
                                str(p) for p in reservations.mapped("partner_name") if p
                            ),
                            "checkin": checkinhour,
                            # 'checkin': rooms[0].checkin[:10],
                            # 'checkout': rooms[0].checkout[:10],
                            "checkout": checkouthour,
                            # 'kelly': 5,
                            "clean_date": fechalimpieza,
                        }
                    ).id
                )
        return self.env["kellysrooms"].search([("id", "in", listid)])

    def print_rooms_report(self):
        rooms = self.env["kellysrooms"].search(
            [("id", "in", self.habitaciones.ids)], order="habitacion ASC"
        )

        return self.env.ref("kellys_daily_report.report_kellysrooms").report_action(
            rooms
        )

    def _excel_export(self):
        tipo_limpieza = ["Salida", "Cliente", "Revisar", "Staff", "Averia"]
        file_data = BytesIO()
        workbook = xlsxwriter.Workbook(
            file_data, {"strings_to_numbers": True, "default_date_format": "dd/mm/yyyy"}
        )
        pms_property = self.pms_property_id
        workbook.set_properties(
            {
                "title": "Exported data from " + pms_property.name,
                "subject": "Export Kellys Report from Odoo of " + pms_property.name,
                "author": "Odoo",
                "manager": "User",
                "company": pms_property.name,
                "category": "Hoja de Calculo",
                "keywords": "kellys, odoo, data, " + pms_property.name,
                "comments": "Created with Python in Odoo and XlsxWriter",
            }
        )
        workbook.use_zip64()

        xls_cell_format_date = workbook.add_format({"num_format": "dd/mm/yyyy"})

        xls_cell_format_header = workbook.add_format(
            {"bg_color": "#CCCCCC", "bold": True, "align": "center"}
        )

        worksheet = workbook.add_worksheet(_("Kellys Report"))

        worksheet.write("A1", _("Habitacion"), xls_cell_format_header)
        worksheet.write("B1", _("Tipo L."), xls_cell_format_header)
        worksheet.write("C1", _("Notas"), xls_cell_format_header)
        worksheet.write("D1", _("Entrada"), xls_cell_format_header)
        worksheet.write("E1", _("Salida"), xls_cell_format_header)
        worksheet.write("F1", _("Asignado"), xls_cell_format_header)

        worksheet.set_column("A:A", 10)
        worksheet.set_column("B:B", 10)
        worksheet.set_column("C:C", 20)
        worksheet.set_column("D:D", 15)
        worksheet.set_column("E:E", 15)
        worksheet.set_column("F:F", 10)

        rooms = self.env["kellysrooms"].search(
            [("id", "in", self.habitaciones.ids)], order="habitacion ASC"
        )

        offset = 1
        for k_room, v_room in enumerate(rooms):
            worksheet.write(k_room + offset, 0, v_room.habitacion)
            worksheet.write(k_room + offset, 1, tipo_limpieza[int(v_room.tipo) - 1])
            worksheet.write(k_room + offset, 2, v_room.notas)
            worksheet.write(k_room + offset, 3, v_room.checkin, xls_cell_format_date)
            worksheet.write(k_room + offset, 4, v_room.checkout, xls_cell_format_date)
            worksheet.write(k_room + offset, 5, v_room.kelly.name)

        workbook.close()
        file_data.seek(0)

        return {
            "xls_filename": f"Kellys_{pms_property.name}_{self.date_start}.xlsx",
            "xls_binary": base64.encodebytes(file_data.read()),
        }

    def excel_rooms_report(self):
        self.write(self._excel_export())
        return {
            "name": _("Listado de limpieza"),
            "res_id": self.id,
            "res_model": "kellysreport",
            "type": "ir.actions.act_window",
            "view_id": self.env.ref("kellys_daily_report.kellys_report_view").id,
            "view_mode": "form",
        }
