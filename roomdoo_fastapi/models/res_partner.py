from dateutil.relativedelta import relativedelta

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    total_invoiced_last_year = fields.Monetary(
        compute="_compute_total_invoiced_last_year"
    )
    last_reservation_id = fields.Many2one(
        "pms.reservation", compute="_compute_reservation_data"
    )
    in_house = fields.Boolean(
        compute="_compute_reservation_data", search="_search_in_house"
    )

    def _compute_total_invoiced_last_year(self):
        invoice_type = self._context.get("invoice_type", "out_invoice")
        for partner in self:
            today = fields.Date.context_today(self)
            a_year_ago = today - relativedelta(years=1)
            result = self.env["account.move"].read_group(
                domain=[
                    ("partner_id", "child_of", partner.id),
                    ("move_type", "=", invoice_type),
                    ("state", "=", "posted"),
                    ("invoice_date", ">", a_year_ago),
                ],
                fields=["amount_total_signed"],
                groupby=[],
            )
            partner.total_invoiced_last_year = (
                result[0]["amount_total_signed"] if result else 0.0
            )

    def _compute_reservation_data(self):
        for partner in self:
            checkin_partner = self.env["pms.checkin.partner"].search_read(
                [("partner_id", "=", partner.id)], ["reservation_id"]
            )
            current_guest = (
                self.env["pms.checkin.partner"].search_count(
                    [("partner_id", "=", partner.id), ("state", "=", "onboard")]
                )
                > 0
            )

            checkin_reservation_ids = [
                rec["reservation_id"][0] for rec in checkin_partner
            ]
            # current_guest = any(
            #     checkin_partner.filtered(lambda r: r.state == "onboard")
            # )
            # checkin_reservation_ids = checkin_partner.mapped("reservation_id.id")
            reservation = self.env["pms.reservation"].search_read(
                [
                    "|",
                    (
                        "partner_id",
                        "=",
                        partner.id if isinstance(partner.id, int) else False,
                    ),
                    ("id", "in", checkin_reservation_ids),
                ],
                fields=["id"],
                order="checkout desc",
                limit=1,
            )
            partner.last_reservation_id = reservation[0]["id"] if reservation else False
            partner.in_house = current_guest

    def _search_in_house(self, operator, value):
        checkins = self.env["pms.checkin.partner"].search([("state", "=", "onboard")])
        partner_ids = checkins.mapped("partner_id.id")
        if (operator, value) in [("=", True), ("!=", False)]:
            return [("id", "in", partner_ids)]
        elif (operator, value) in [("=", False), ("!=", True)]:
            return [("id", "not in", partner_ids)]
        else:
            return [("id", "in", [])]
