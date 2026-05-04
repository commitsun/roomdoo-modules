from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PmsProperty(models.Model):
    _inherit = "pms.property"

    default_invoicing_policy = fields.Selection(
        selection=[
            ("manual", "Manual"),
            ("checkout", "Checkout"),
            ("month_day", "Month Day Invoice"),
        ],
        default="manual",
    )
    invoicing_month_day = fields.Integer(
        help="The day of the month to invoice",
    )
    margin_days_autoinvoice = fields.Integer(
        string="Margin Days",
        help="Days from Checkout to generate the invoice",
    )

    @api.model
    def _get_folio_default_journal(self, partner_invoice_id, room_ids=False):
        self.ensure_one()
        partner = self.env["res.partner"].browse(partner_invoice_id)
        if not not partner._check_enought_invoice_data() and self._context.get(
            "autoinvoice"
        ):
            return self._get_journal(is_simplified_invoice=True, room_ids=room_ids)
        return super()._get_folio_default_journal(partner_invoice_id, room_ids=room_ids)

    @api.model
    def autoinvoicing(self, offset=0, with_delay=False, autocommit=False):
        """
        This method is used to invoicing automatically the folios
        and validate the draft invoices created by the folios
        """
        date_reference = fields.Date.today() - relativedelta(days=offset)
        # REVIEW: We clean the autoinvoice_date of the past draft invoices
        # to avoid blocking the autoinvoicing
        self.clean_date_on_past_draft_invoices(date_reference)
        # 1- Invoicing the folios
        folios = self.env["pms.folio"].search(
            [
                ("sale_line_ids.autoinvoice_date", "=", date_reference),
                ("invoice_status", "=", "to_invoice"),
                ("amount_total", ">", 0),
            ]
        )
        paid_folios = folios.filtered(lambda f: f.pending_amount <= 0)
        unpaid_folios = folios.filtered(lambda f: f.pending_amount > 0)
        folios_to_invoice = paid_folios
        # If the folio is unpaid we will auto invoice only the
        # not cancelled lines
        for folio in unpaid_folios:
            if any([res.state != "cancel" for res in folio.reservation_ids]):
                folios_to_invoice += folio
            else:
                folio.sudo().message_post(
                    body=_(
                        "Not invoiced due to pending amounts and cancelled reservations"
                    )
                )
        for folio in folios_to_invoice:
            if with_delay:
                self.with_delay().autoinvoice_folio(folio, delay_post=True)
            else:
                self.autoinvoice_folio(folio)
        if not with_delay:
            # 2- Validate the draft invoices created by the folios
            draft_invoices_to_post = self.env["account.move"].search(
                [
                    ("state", "=", "draft"),
                    (
                        "invoice_line_ids.folio_line_ids.autoinvoice_date",
                        "=",
                        date_reference,
                    ),
                    ("folio_ids", "!=", False),
                    ("amount_total", ">", 0),
                ]
            )
            # Skip invoices that still have lines with future autoinvoice dates
            draft_invoices_to_post = draft_invoices_to_post.filtered(
                lambda inv: not inv.invoice_line_ids.folio_line_ids.filtered(
                    lambda fl: fl.autoinvoice_date
                    and fl.autoinvoice_date > date_reference
                )
            )
            for invoice in draft_invoices_to_post:
                self.autovalidate_folio_invoice(invoice)

            # 3- Reverse the downpayment invoices not included in final invoice
            downpayments_invoices_to_reverse = self.env["account.move.line"].search(
                [
                    ("move_id.state", "=", "posted"),
                    ("folio_line_ids.is_downpayment", "=", True),
                    ("folio_line_ids.qty_invoiced", ">", 0),
                    ("folio_ids", "in", folios.ids),
                ]
            )
            downpayment_invoices = downpayments_invoices_to_reverse.mapped("move_id")
            if downpayment_invoices:
                for downpayment_invoice in downpayment_invoices:
                    default_values_list = [
                        {
                            "ref": _("Reversal of: " f'{move.name + " - " + move.ref}'),
                        }
                        for move in downpayment_invoice
                    ]
                    downpayment_invoice.with_context(
                        sii_refund_type="I"
                    )._reverse_moves(default_values_list, cancel=True)
                    downpayment_invoice.message_post(
                        body=_(
                            "The downpayment invoice has been reversed "
                            "because it was not included in the "
                            "final invoice"
                        )
                    )

        return True

    @api.model
    def clean_date_on_past_draft_invoices(self, date_reference):
        """
        This method is used to clean the date on past draft invoices
        """
        journal_ids = (
            self.env["account.journal"]
            .search(
                [
                    ("type", "=", "sale"),
                    ("pms_property_ids", "!=", False),
                ]
            )
            .ids
        )
        draft_invoices = self.env["account.move"].search(
            [
                ("state", "=", "draft"),
                ("invoice_date", "<", date_reference),
                ("journal_id", "in", journal_ids),
            ]
        )
        if draft_invoices:
            draft_invoices.write({"invoice_date": date_reference})
        return True

    def autovalidate_folio_invoice(self, invoice):
        try:
            with self.env.cr.savepoint():
                invoice.action_post()
                # Reverse simplified downpayment invoices
                for folio in invoice.folio_ids:
                    downpayments = folio.sale_line_ids.filtered(
                        lambda r: r.is_downpayment and r.qty_invoiced > 0
                    )
                    dp_invoices = (
                        downpayments.invoice_lines.mapped("move_id")
                    ).filtered(lambda i: i.is_simplified_invoice)
                    if dp_invoices:
                        default_values_list = [
                            {
                                "ref": _("Reversal of: " f'{m.name + " - " + m.ref}'),
                            }
                            for m in dp_invoices
                        ]
                        dp_invoices.with_context(sii_refund_type="I")._reverse_moves(
                            default_values_list, cancel=True
                        )
        except Exception as e:
            raise ValidationError(
                _("Error in autovalidate invoice: %s") % str(e)
            ) from e

    def _try_populate_fiscal_data_from_id_numbers(self, partners):
        for partner in partners:
            mappable_id_numbers = partner.id_numbers.filtered(
                lambda idn: idn.category_id.partner_map_field and idn.name
            )
            for id_number in mappable_id_numbers:
                id_number.set_partner_id_field()

    def autoinvoice_folio(self, folio, delay_post=False):
        try:
            with self.env.cr.savepoint():
                # REVIEW: folio sale line "_compute_auotinvoice_date" sometimes
                # dont work in services (probably cache issue¿?),
                # we ensure that the date is set or recompute this
                for line in folio.sale_line_ids.filtered(
                    lambda r: not r.autoinvoice_date
                ):
                    line._compute_autoinvoice_date()
                invoices = folio.with_context(autoinvoice=True)._create_invoices(
                    grouped=True,
                    final=False,
                )
                downpayments = folio.sale_line_ids.filtered(
                    lambda r: r.is_downpayment and r.qty_invoiced > 0
                )
                for invoice in invoices:
                    if (
                        invoice.amount_total
                        > invoice.pms_property_id.max_amount_simplified_invoice
                        and invoice.journal_id.is_simplified_invoice
                    ):
                        all_invoice_partners = invoice.folio_ids.partner_invoice_ids
                        hosts_to_invoice = all_invoice_partners.filtered(
                            lambda p: p._check_enought_invoice_data()
                        ).mapped("id")
                        if not hosts_to_invoice:
                            self._try_populate_fiscal_data_from_id_numbers(
                                all_invoice_partners
                            )
                            hosts_to_invoice = all_invoice_partners.filtered(
                                lambda p: p._check_enought_invoice_data()
                            ).mapped("id")
                        if hosts_to_invoice:
                            invoice.partner_id = hosts_to_invoice[0]
                            invoice.journal_id = (
                                invoice.pms_property_id.journal_normal_invoice_id
                            )
                        else:
                            mens = _(
                                "The total amount of the simplified invoice is "
                                "higher than the maximum amount allowed for "
                                "simplified invoices, and dont have enought data"
                                " in hosts to create a normal invoice."
                            )
                            folio.sudo().message_post(body=mens)
                            raise ValidationError(mens)
                    for downpayment in downpayments.filtered(
                        lambda d, i=invoice: d.default_invoice_to == i.partner_id
                    ):
                        # If the downpayment invoice partner is the same that the
                        # folio partner, we include the downpayment in the
                        #  normal invoice
                        invoice_down_payment_vals = downpayment._prepare_invoice_line(
                            sequence=max(invoice.invoice_line_ids.mapped("sequence"))
                            + 1,
                        )
                        invoice.write(
                            {"invoice_line_ids": [(0, 0, invoice_down_payment_vals)]}
                        )
                    if delay_post:
                        eta = datetime.now() + timedelta(minutes=15)
                        self.with_delay(eta=eta).autovalidate_folio_invoice(invoice)
                    else:
                        invoice.action_post()
                if not delay_post:
                    # The downpayment invoices that not was included in final
                    # invoice, are reversed
                    downpayment_invoices = (
                        downpayments.filtered(
                            lambda d: d.qty_invoiced > 0
                        ).invoice_lines.mapped("move_id")
                    ).filtered(lambda i: i.is_simplified_invoice)
                    if downpayment_invoices:
                        default_values_list = [
                            {
                                "ref": _(
                                    f'Reversal of: {move.name + " - " + move.ref}'
                                ),
                            }
                            for move in downpayment_invoices
                        ]
                        downpayment_invoices.with_context(
                            sii_refund_type="I"
                        )._reverse_moves(default_values_list, cancel=True)
        except Exception as e:
            raise ValidationError(_("Error in autoinvoicing folio: %s") % str(e)) from e
