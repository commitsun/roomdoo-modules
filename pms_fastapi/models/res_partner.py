from odoo import fields, models


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["mail.thread.phone", "res.partner"]

    # new field to avoid interference with total_invoiced from account module
    fastapi_total_invoiced = fields.Monetary(compute="_compute_fastapi_total_invoiced")
    last_reservation_id = fields.Many2one(
        "pms.reservation", compute="_compute_reservation_data"
    )
    in_house = fields.Boolean(
        compute="_compute_reservation_data", search="_search_in_house"
    )
    identification_number = fields.Char(search="_search_identification_number")

    def _search_identification_number(self, operator, value):
        id_numbers = self.env["res.partner.id_number"].search(
            [("name", operator, value)]
        )
        return [("id_numbers.id", "in", id_numbers.ids)]

    def _compute_fastapi_total_invoiced(self):
        property_domain = []
        if self._context.get("pms_property_ids"):
            property_domain = [
                ("pms_property_id", "in", self._context["pms_property_ids"])
            ]
        invoice_type = self._context.get("invoice_type", "out_invoice")
        for partner in self:
            result = self.env["account.move"].read_group(
                domain=property_domain
                + [
                    ("partner_id", "child_of", partner.id),
                    ("move_type", "=", invoice_type),
                    ("state", "=", "posted"),
                ],
                fields=["amount_total_signed"],
                groupby=[],
            )
            partner.fastapi_total_invoiced = (
                result[0]["amount_total_signed"] if result else 0.0
            )

    def _compute_reservation_data(self):
        property_domain = []
        if self._context.get("pms_property_ids"):
            property_domain = [
                ("pms_property_id", "in", self._context["pms_property_ids"])
            ]
        for partner in self:
            checkin_partner = self.env["pms.checkin.partner"].search_read(
                property_domain
                + [("partner_id", "=", partner.id), ("reservation_id", "!=", None)],
                ["reservation_id"],
            )
            current_guest = (
                self.env["pms.checkin.partner"].search_count(
                    property_domain
                    + [("partner_id", "=", partner.id), ("state", "=", "onboard")]
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
                property_domain
                + [
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
        property_domain = []
        if self._context.get("pms_property_ids"):
            property_domain = [
                ("pms_property_id", "in", self._context["pms_property_ids"])
            ]
        checkins = self.env["pms.checkin.partner"].search(
            property_domain + [("state", "=", "onboard")]
        )
        partner_ids = checkins.mapped("partner_id.id")
        if (operator, value) in [("=", True), ("!=", False)]:
            return [("id", "in", partner_ids)]
        elif (operator, value) in [("=", False), ("!=", True)]:
            return [("id", "not in", partner_ids)]
        else:
            return [("id", "in", [])]

    def _phone_get_number_fields(self):
        """This method returns the fields to use to find the number to use to
        send an SMS on a record."""
        return ["mobile", "phone"]

    def set_fiscal_document_data(
        self, fiscal_id_number=False, fiscal_id_number_type=False
    ):
        """
        Set the fiscal document data for the partner if the type is vat,
        Otherwise will be set by other module.
        The function can receive only one of the two parameters, in that case
        the other parameter will be taken from the partner record.
        """
        if not fiscal_id_number and not fiscal_id_number_type:
            return
        if not fiscal_id_number:
            fiscal_id_number = self.vat
        if not fiscal_id_number_type:
            fiscal_id_number_type = "vat"
        self.write({"vat": fiscal_id_number})
