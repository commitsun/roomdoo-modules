import base64
from datetime import datetime

import werkzeug.exceptions

from odoo import SUPERUSER_ID, _, fields
from odoo.exceptions import MissingError, UserError
from odoo.osv import expression

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

from ..pms_api_rest_utils import invoice_is_reversed, pms_api_check_access

# Reported as the invoice date while the reservation invoice lock blocks a
# draft, so the legacy front-end does not offer the validation button.
LOCKED_INVOICE_YEARS = 1


class PmsInvoiceService(Component):
    _inherit = "base.rest.service"
    _name = "pms.invoice.service"
    _usage = "invoices"
    _collection = "pms.services"

    @restapi.method(
        [
            (
                [
                    "/",
                ],
                "GET",
            )
        ],
        input_param=Datamodel("pms.invoice.search.param"),
        output_param=Datamodel("pms.invoice.info"),
        auth="jwt_api_pms",
    )
    def get_invoices(self, pms_invoice_search_param):
        order_by_param = False
        result_invoices = []

        domain = []
        domain_fields = [
            ("state", "in", ("draft", "posted")),
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("folio_ids", "!=", False),
        ]
        domain_filter = list()

        if pms_invoice_search_param.originAgencyId:
            domain_fields.append(
                ("origin_agency_id", "=", pms_invoice_search_param.originAgencyId),
            )
        if pms_invoice_search_param.pmsPropertyId:
            domain_fields.append(
                ("pms_property_id", "=", pms_invoice_search_param.pmsPropertyId)
            )
        if pms_invoice_search_param.paymentState == "paid":
            domain_fields.append(
                ("payment_state", "in", ("paid", "reversed", "invoicing_legacy"))
            )
        elif pms_invoice_search_param.paymentState == "not_paid":
            domain_fields.append(("payment_state", "in", ("not_paid", "in_payment")))
        elif pms_invoice_search_param.paymentState == "partial":
            domain_fields.append(
                ("payment_state", "=", pms_invoice_search_param.paymentState)
            )
        if pms_invoice_search_param.dateStart and pms_invoice_search_param.dateEnd:
            date_from = fields.Date.from_string(pms_invoice_search_param.dateStart)
            date_to = fields.Date.from_string(pms_invoice_search_param.dateEnd)
            domain_fields.extend(
                [
                    ("invoice_date", ">=", date_from),
                    ("invoice_date", "<=", date_to),
                ]
            )
        if pms_invoice_search_param.filter:
            for search in pms_invoice_search_param.filter.split(" "):
                subdomains = [
                    [("name", "ilike", search)],
                    [("partner_id.display_name", "ilike", search)],
                    [("partner_id.vat", "ilike", search)],
                    [
                        (
                            "partner_id.aeat_identification",
                            "ilike",
                            search,
                        )
                    ],
                    [("folio_ids.name", "ilike", search)],
                ]
                domain_filter.append(expression.OR(subdomains))

        if domain_filter:
            domain = expression.AND([domain_fields, domain_filter[0]])
        else:
            domain = domain_fields
        PmsInvoiceResults = self.env.datamodels["pms.invoice.results"]
        PmsInvoiceInfo = self.env.datamodels["pms.invoice.info"]
        PmsInvoiceLineInfo = self.env.datamodels["pms.invoice.line.info"]
        total_invoices = self.env["account.move"].sudo().search_count(domain)
        if pms_invoice_search_param.orderBy:
            order_by_param = self._get_mapped_order_by_field(
                pms_invoice_search_param.orderBy
            ) + (" desc" if pms_invoice_search_param.orderDesc else " asc")
        invoices = (
            self.env["account.move"]
            .sudo()
            .search(
                domain,
                order=order_by_param if order_by_param else False,
                limit=pms_invoice_search_param.limit,
                offset=pms_invoice_search_param.offset,
            )
        )
        pms_api_check_access(user=self.env.user, records=invoices)
        amount_total = sum(invoices.mapped("amount_total"))
        for invoice in invoices:
            move_lines = []
            for move_line in invoice.invoice_line_ids:
                move_lines.append(
                    PmsInvoiceLineInfo(
                        id=move_line.id,
                        name=move_line.name if move_line.name else None,
                        quantity=move_line.quantity if move_line.quantity else None,
                        priceUnit=move_line.price_unit
                        if move_line.price_unit
                        else None,
                        total=move_line.price_total if move_line.price_total else None,
                        discount=move_line.discount if move_line.discount else None,
                        displayType=move_line.display_type
                        if move_line.display_type
                        else None,
                        saleLineId=move_line.folio_line_ids[0]
                        if move_line.folio_line_ids
                        else None,
                        isDownPayment=move_line.move_id._is_downpayment(),
                    )
                )
            # TEMP: for draft invoices, surface the autoinvoice_date computed
            # by pms_autoinvoice for the folio sale lines bound to this move
            # (or today() if all of them resolve to manual policy and have no
            # date), so the front-end can preview when the move will be
            # auto-validated and stop interpreting a null `date` as a hard
            # block on the manual invoice-validation flow. Posted moves keep
            # returning their real invoice_date with the invoice_date_due
            # fallback. Drop this branch once a dedicated field (e.g. an
            # explicit can-validate flag or a display-only invoice-date
            # field) is exposed to the front-end.
            if invoice.state == "draft":
                autoinvoice_dates = [
                    d
                    for d in invoice.invoice_line_ids.mapped(
                        "folio_line_ids.autoinvoice_date"
                    )
                    if d
                ]
                display_date = (
                    max(autoinvoice_dates) if autoinvoice_dates else fields.Date.today()
                )
                # While the reservation invoice lock blocks this move, report a
                # future date so the front-end does not offer the manual
                # validation button for something _post() is going to refuse
                # anyway. It takes precedence over the autoinvoice preview: it
                # is the harder constraint of the two.
                if self._is_reservation_invoice_locked(invoice):
                    display_date = fields.Date.add(
                        fields.Date.today(), years=LOCKED_INVOICE_YEARS
                    )
                invoice_date = datetime.combine(
                    display_date, datetime.min.time()
                ).isoformat()
            else:
                invoice_date = (
                    datetime.combine(
                        invoice.invoice_date, datetime.min.time()
                    ).isoformat()
                    if invoice.invoice_date
                    else datetime.combine(
                        invoice.invoice_date_due, datetime.min.time()
                    ).isoformat()
                    if invoice.invoice_date_due
                    else None
                )
            invoice_url = (
                invoice.get_proforma_portal_url()
                if invoice.state == "draft"
                else invoice.get_portal_url()
            )
            portal_url = (
                self.env["ir.config_parameter"].sudo().get_param("web.base.url")
                + invoice_url
            )
            result_invoices.append(
                PmsInvoiceInfo(
                    id=invoice.id if invoice.id else None,
                    name=invoice.name if invoice.name else None,
                    amount=round(invoice.amount_total, 2)
                    if invoice.amount_total
                    else None,
                    date=invoice_date,
                    state=invoice.state if invoice.state else None,
                    paymentState=invoice.payment_state
                    if invoice.payment_state
                    else None,
                    partnerName=invoice.partner_id.name
                    if invoice.partner_id.name
                    else None,
                    partnerId=invoice.partner_id.id if invoice.partner_id.id else None,
                    moveLines=move_lines if len(move_lines) > 0 else None,
                    folioId=invoice.folio_ids[0] if invoice.folio_ids else None,
                    portalUrl=portal_url,
                    moveType=invoice.move_type,
                    isReversed=invoice_is_reversed(invoice),
                    isDownPaymentInvoice=invoice._is_downpayment(),
                    isSimplifiedInvoice=invoice.journal_id.is_simplified_invoice,
                    originAgencyId=invoice.origin_agency_id.id
                    if invoice.origin_agency_id
                    else None,
                    ref=invoice.ref if invoice.ref else None,
                    narration=invoice.narration if invoice.narration else None,
                    pmsPropertyId=invoice.pms_property_id
                    if invoice.pms_property_id
                    else None,
                )
            )
        return PmsInvoiceResults(
            invoices=result_invoices,
            total=round(amount_total, 2),
            totalInvoices=total_invoices,
        )

    @restapi.method(
        [
            (
                [
                    "/p/<int:invoice_id>",
                ],
                "PATCH",
            )
        ],
        input_param=Datamodel("pms.invoice.info"),
        auth="jwt_api_pms",
    )
    # flake8: noqa: C901
    def update_invoice(self, invoice_id, pms_invoice_info):
        invoice = self.env["account.move"].sudo().browse(invoice_id)
        pms_api_check_access(user=self.env.user, records=invoice)
        if invoice.move_type in ["in_refund", "out_refund"]:
            raise UserError(_("You can't update a refund invoice"))
        # Not only payment_state == 'reversed': rectified keeping its payment,
        # the original stays 'paid' and would be rectified a second time.
        if invoice_is_reversed(invoice):
            raise UserError(_("You can't update a reversed invoice"))
        # TODO: _check_fiscalyear_lock_date with sudo is unsafe, we need to check the access
        # invoice._check_fiscalyear_lock_date()
        new_vals = {}
        if (
            pms_invoice_info.partnerId
            and pms_invoice_info.partnerId != invoice.partner_id.id
        ):
            new_vals["partner_id"] = pms_invoice_info.partnerId

        if pms_invoice_info.date:
            invoice_date_info = fields.Date.from_string(pms_invoice_info.date)
            if invoice_date_info != invoice.invoice_date:
                # If the invoice is in draft state and the date is less than today,
                if invoice_date_info < fields.Date.today() and not invoice.invoice_date:
                    new_vals["invoice_date"] = fields.Date.today()
                else:
                    new_vals["invoice_date"] = invoice_date_info

        # If invoice lines are updated, we expect that all lines will be
        # send to service, the lines that are not sent we assume that
        # they have been eliminated
        cmd_invoice_lines = []
        if pms_invoice_info.moveLines is not None:
            cmd_invoice_lines = self._get_invoice_lines_commands(
                invoice, pms_invoice_info
            )
            if cmd_invoice_lines:
                new_vals["invoice_line_ids"] = cmd_invoice_lines
        # Update Journal by partner if necessary (simplified invoice -> normal invoice)
        if pms_invoice_info.isSimplifiedInvoice is not None:
            self._set_invoice_journal_vals(invoice, pms_invoice_info, new_vals)
        new_invoice = False
        if new_vals:
            # Update Invoice
            # When modifying an invoice, depending on the company's configuration,
            # and the invoice state it will be modified directly or a reverse
            # of the current invoice will be created to later create a new one
            # with the updated data.
            # TODO: to create core pms correct_invoice_policy field
            # if invoice.state != "draft" and company.corrective_invoice_policy == "strict":
            if invoice.state == "posted" and self.check_blocked_fields(
                invoice, new_vals
            ):
                # invoice create refund
                new_invoice = invoice.copy()
                cmd_new_invoice_lines = []
                for item in cmd_invoice_lines:
                    # susbstituted in new_vals reversed invoice line id by new invoice line id
                    if item[0] == 0:
                        cmd_new_invoice_lines.append(item)
                    else:
                        folio_line_ids = (
                            self.env["folio.sale.line"]
                            .sudo()
                            .browse(
                                self.env["account.move.line"]
                                .browse(item[1])
                                .folio_line_ids.ids
                            )
                        )
                        new_id = new_invoice.invoice_line_ids.filtered(
                            lambda l: l.folio_line_ids == folio_line_ids
                        ).id
                        if item[0] == 2:
                            # delete
                            cmd_new_invoice_lines.append((2, new_id))
                        else:
                            # update
                            cmd_new_invoice_lines.append((1, new_id, item[2]))
                if cmd_new_invoice_lines:
                    new_vals["invoice_line_ids"] = cmd_new_invoice_lines
                default_values_list = [
                    {
                        "ref": _(
                            f'Reversal of: {move.name + (" - " + move.ref if move.ref else "")}'
                        ),
                    }
                    for move in invoice
                ]
                new_invoice.write(new_vals)
                # Lines of a posted invoice cannot be deleted: the sections left
                # without lines go before the new invoice is posted.
                self._unlink_empty_sections(new_invoice)
                self._replace_posted_invoice(invoice, new_invoice, default_values_list)
            else:
                new_invoice = self._direct_move_update(invoice, new_vals)
        invoice_to_update = new_invoice or invoice
        if invoice_to_update.state == "draft":
            self._unlink_empty_sections(invoice_to_update)

        if pms_invoice_info.narration is not None:
            invoice_to_update.write({"narration": pms_invoice_info.narration})
        if invoice_to_update.state == "draft" and pms_invoice_info.state == "confirm":
            invoice_to_update.action_post()
        if (
            invoice_to_update.state == "draft"
            and not invoice_to_update.invoice_line_ids
        ):
            invoice_to_update.unlink()
        return invoice_to_update.id or None

    @restapi.method(
        [
            (
                [
                    "/<int:invoice_id>",
                ],
                "DELETE",
            )
        ],
        auth="jwt_api_pms",
    )
    def delete_invoice(self, invoice_id):
        invoice = self.env["account.move"].sudo().browse(invoice_id)
        pms_api_check_access(user=self.env.user, records=invoice)
        if invoice:
            if invoice.state != "draft" and invoice.name not in ["/", False]:
                raise UserError(_("Only draft invoices can be deleted"))
            invoice.unlink()
        else:
            raise MissingError(_("Invoice not found"))

    def _unlink_empty_sections(self, invoice):
        """Remove the section lines of ``invoice`` left with no line under
        them."""
        folio_lines = invoice.invoice_line_ids.folio_line_ids
        used_sections = folio_lines.filtered(
            lambda line: line.display_type != "line_section"
        ).section_id
        for section in folio_lines.filtered(
            lambda line: line.display_type == "line_section"
            and line not in used_sections
        ):
            section.invoice_lines.filtered(
                lambda line: line.move_id == invoice
            ).unlink()

    def _set_invoice_journal_vals(self, invoice, pms_invoice_info, new_vals):
        """Put in ``new_vals`` the journal the invoice must end up in, chosen
        the way the folio invoicing chooses it: by the customer it ends up with.
        The anonymous customer gets a simplified invoice, a named one a normal
        invoice, whatever the flag says.

        The legacy edit modal never loads isSimplifiedInvoice and sends False
        on every save, so False alone is not a request: with the customer
        unchanged the invoice stays in its journal. Taken literally, a plain
        save of a simplified invoice moved it to the normal journal -- on a
        posted invoice, a rectification and a normal invoice for the anonymous
        customer. Ticking it with no customer, or with the one already loaded
        (the modal hides the customer while the option shows), asks for a
        simplified invoice, so the invoice goes to the anonymous customer.
        """
        anonymous = self.env.ref("pms.various_pms_partner", raise_if_not_found=False)
        to_simplified = pms_invoice_info.isSimplifiedInvoice
        keeps_customer = (
            not pms_invoice_info.partnerId
            or pms_invoice_info.partnerId == invoice.partner_id.id
        )
        if (
            to_simplified
            and keeps_customer
            and anonymous
            and invoice.partner_id != anonymous
        ):
            new_vals["partner_id"] = anonymous.id
        new_partner_id = new_vals.get("partner_id")
        if anonymous and new_partner_id == anonymous.id:
            to_simplified = True
        elif new_partner_id:
            to_simplified = False
        elif not to_simplified:
            to_simplified = invoice.journal_id.is_simplified_invoice
        if invoice.journal_id.is_simplified_invoice != to_simplified:
            new_vals["journal_id"] = (
                invoice.pms_property_id.journal_simplified_invoice_id.id
                if to_simplified
                else invoice.pms_property_id.journal_normal_invoice_id.id
            )

    def _replace_posted_invoice(self, invoice, new_invoice, default_values_list):
        """Rectify ``invoice`` and post ``new_invoice``, the draft copy that
        replaces it.

        The core way: a credit note with ``cancel=True``, which starts by
        unreconciling the original, so that its payment is released and the
        folio autoreconciliation can take it to the new invoice when it is
        posted. pms_api_rest_reconcile_lock_date replaces it with a
        rectification that keeps the original's reconciliations.
        """
        invoice.with_context(
            sii_refund_type="I", supplier_invoice_number=invoice.name
        )._reverse_moves(default_values_list, cancel=True)
        new_invoice.sudo().action_post()

    def _direct_move_update(self, invoice, new_vals):
        previus_state = invoice.state
        if previus_state == "posted":
            return self._rename_posted_invoice_lines(invoice, new_vals)
        if new_vals:
            updated_invoice_lines_name = False
            # REVIEW: If invoice lines are updated (lines that already existed),
            # the _move_autocomplete_invoice_lines_write called accout.move write
            # method overwrite the move_lines dict and we lost the new name values,
            # so, we need to save and rewrite it. (core odoo methods)

            # 1- save send invoice line name values:
            if new_vals.get("invoice_line_ids"):
                updated_invoice_lines_name = {
                    line[1]: line[2]["name"]
                    for line in new_vals["invoice_line_ids"]
                    if line[0] == 1 and "name" in line[2]
                }
            # _move_autocomplete_invoice_lines_write overwrite invoice line name values
            # so, we need to save and rewrite it. in all line that are not updated or deleted
            for line in invoice.invoice_line_ids.filtered(
                lambda l: l.id not in updated_invoice_lines_name
                if updated_invoice_lines_name
                else []
                and l.id
                not in [
                    line[1] for line in new_vals["invoice_line_ids"] if line[0] == 2
                ]
            ):
                updated_invoice_lines_name[line.id] = line.name
            # 2- update invoice
            invoice.write(new_vals)
            # 3- rewrite invoice line name values:
            if updated_invoice_lines_name:
                for item in updated_invoice_lines_name:
                    invoice.invoice_line_ids.filtered(lambda l: l.id == item).write(
                        {"name": updated_invoice_lines_name[item]}
                    )
            # 4- Avoid set number invoice in draft invoices
            if previus_state == "draft":
                invoice.write({"name": "/"})
        if previus_state == "posted":
            invoice.action_post()
        return invoice

    def _rename_posted_invoice_lines(self, invoice, new_vals):
        """A posted invoice is never reset to draft here.

        button_draft() unreconciles it, and the payments, transfer entries and
        credit notes it was settled with are lost: re-posting only brings back
        the payments the folio autoreconciliation can find. check_blocked_fields
        sends every real change to the rectification, so what is left here is
        renaming lines, which is written in place: line names are not protected
        by the lock dates, and the SII uses a fixed description.
        """
        names = {}
        for command in new_vals.get("invoice_line_ids", []):
            if command[0] != 1 or set(command[2]) - {"name"}:
                raise UserError(
                    _("This change on a posted invoice needs a rectification.")
                )
            names[command[1]] = command[2]["name"]
        if set(new_vals) - {"invoice_line_ids"}:
            raise UserError(_("This change on a posted invoice needs a rectification."))
        for line in invoice.invoice_line_ids.filtered(
            lambda move_line: move_line.id in names
            and move_line.name != names[move_line.id]
        ):
            line.name = names[line.id]
        return invoice

    def check_blocked_fields(self, invoice, new_vals):
        # A posted invoice cannot change its journal (core refuses it once it
        # has been posted) nor, once sent to the SII, its date: both are a
        # rectification and a new invoice.
        if invoice.state == "posted" and (
            "journal_id" in new_vals or "invoice_date" in new_vals
        ):
            return True
        # Check partner and amounts
        if (
            new_vals.get("partner_id")
            and new_vals.get("partner_id") != invoice.partner_id.id
        ):
            return True
        if new_vals.get("invoice_line_ids"):
            for line in new_vals["invoice_line_ids"]:
                if line[0] == 2:
                    move_line = self.env["account.move.line"].sudo().browse(line[1])
                    if move_line.display_type == "product":
                        return True
                if line[0] == 1:
                    move_line = self.env["account.move.line"].sudo().browse(line[1])
                    if "quantity" in line[2] and move_line.quantity != line[2].get(
                        "quantity"
                    ):
                        return True
                if (
                    line[0] == 0
                    and not line[2].get("display_type")
                    or line[2].get("display_type") == "product"
                ):
                    return True
        return False

    @restapi.method(
        [
            (
                [
                    "/",
                ],
                "POST",
            )
        ],
        input_param=Datamodel("pms.invoice.info"),
        auth="jwt_api_pms",
    )
    def create_invoice(self, pms_invoice_info):
        if pms_invoice_info.originDownPaymentId:
            payment = (
                self.env["account.payment"]
                .sudo()
                .browse(pms_invoice_info.originDownPaymentId)
            )
            pms_api_check_access(user=self.env.user, records=payment)
            self.env["account.payment"].sudo()._create_downpayment_invoice(
                payment=payment,
                partner_id=pms_invoice_info.partnerId,
            )

    @restapi.method(
        [
            (
                [
                    "/<int:invoice_id>/mail",
                ],
                "GET",
            )
        ],
        output_param=Datamodel("pms.mail.info", is_list=False),
        auth="jwt_api_pms",
    )
    def get_invoice_mail(self, invoice_id):
        invoice = self.env["account.move"].sudo().browse(invoice_id)
        pms_api_check_access(user=self.env.user, records=invoice)
        compose_vals = {
            "template_id": self.env.ref("account.email_template_edi_invoice").id,
            "model": "account.move",
            "res_ids": invoice.id,
        }
        values = (
            self.env["mail.compose.message"]
            .with_user(SUPERUSER_ID)
            .generate_email_for_composer(
                template_id=compose_vals["template_id"],
                res_ids=compose_vals["res_ids"],
                fields=["subject", "body_html"],
            )
        )
        PmsMailInfo = self.env.datamodels["pms.mail.info"]
        return PmsMailInfo(
            bodyMail=values["body"],
            subject=values["subject"],
        )

    @restapi.method(
        [
            (
                [
                    "/send-mail-print-invoices",
                ],
                "POST",
            )
        ],
        input_param=Datamodel("pms.account.send.search.param", is_list=False),
        output_param=Datamodel("pms.report", is_list=False),
        auth="jwt_api_pms",
    )
    def send_mail_print_invoices(self, account_send_search_param):
        invoices = (
            self.env["account.move"].sudo().browse(account_send_search_param.invoiceIds)
        )
        pms_api_check_access(user=self.env.user, records=invoices)
        template = self.env.ref(
            "account.email_template_edi_invoice", raise_if_not_found=False
        ).sudo()
        PmsResponse = self.env.datamodels["pms.report"]
        base64_encoded_str = None

        for invoice in invoices:
            if account_send_search_param.isEmail:
                email_values = {
                    "email_from": invoice.pms_property_id.email
                    if invoice.pms_property_id.email
                    else False,
                    "auto_delete": False,
                    "partner_ids": account_send_search_param.partnerIds
                    if account_send_search_param.partnerIds
                    else False,
                    "recipient_ids": account_send_search_param.partnerIds
                    if account_send_search_param.partnerIds
                    else False,
                    "email_to": account_send_search_param.emailAddresses,
                }
                template.with_user(SUPERUSER_ID).send_mail(
                    invoice.id, force_send=True, email_values=email_values
                )
        if account_send_search_param.isPrint:
            invoice_report = self.env.ref("account.account_invoices").sudo()
            pdf_content, _ = invoice_report.with_user(SUPERUSER_ID)._render_qweb_pdf(
                "account.account_invoices",
                invoices.ids,
            )
            base64_encoded_str = base64.b64encode(pdf_content)
        return PmsResponse(binary=base64_encoded_str)

    @restapi.method(
        [
            (
                [
                    "/<int:invoice_id>/send-mail",
                ],
                "POST",
            )
        ],
        input_param=Datamodel("pms.mail.info"),
        auth="jwt_api_pms",
    )
    def send_invoice_mail(self, invoice_id, pms_mail_info):
        invoice = self.env["account.move"].sudo().browse(invoice_id)
        pms_api_check_access(user=self.env.user, records=invoice)
        recipients = pms_mail_info.emailAddresses
        template = self.env.ref(
            "account.email_template_edi_invoice", raise_if_not_found=False
        ).sudo()
        email_values = {
            "email_to": ",".join(recipients) if recipients else False,
            "email_from": invoice.pms_property_id.email
            if invoice.pms_property_id.email
            else False,
            "subject": pms_mail_info.subject,
            "body_html": pms_mail_info.bodyMail,
            "partner_ids": pms_mail_info.partnerIds
            if pms_mail_info.partnerIds
            else False,
            "recipient_ids": pms_mail_info.partnerIds
            if pms_mail_info.partnerIds
            else False,
            "auto_delete": False,
        }
        template.with_user(SUPERUSER_ID).send_mail(
            invoice.id, force_send=True, email_values=email_values
        )
        invoice.write(
            {
                "is_move_sent": True,
            }
        )
        return True

    def _get_invoice_lines_commands(self, invoice, pms_invoice_info):
        cmd_invoice_lines = []
        for line in invoice.invoice_line_ids:
            line_info = [
                item for item in pms_invoice_info.moveLines if item.id == line.id
            ]
            if line_info:
                line_info = line_info[0]
                line_values = {}
                if line_info.name:
                    line_values["name"] = line_info.name
                if line_info.quantity and line_info.quantity != line.quantity:
                    line_values["quantity"] = line_info.quantity
                if line_values:
                    cmd_invoice_lines.append((1, line.id, line_values))
            elif line.display_type == "product":
                cmd_invoice_lines.append((2, line.id))
        # Get the new lines to add in invoice
        newInvoiceLinesInfo = list(
            filter(lambda item: not item.id, pms_invoice_info.moveLines)
        )
        if newInvoiceLinesInfo:
            partner = (
                self.env["res.partner"].sudo().browse(pms_invoice_info.partnerId)
                if pms_invoice_info.partnerId
                else invoice.partner_id
            )
            folios = (
                self.env["pms.folio"]
                .sudo()
                .browse(
                    list(
                        {
                            self.env["folio.sale.line"]
                            .sudo()
                            .browse(line.saleLineId)
                            .folio_id.id
                            for line in list(
                                filter(
                                    lambda item: item.name,
                                    pms_invoice_info.moveLines,
                                )
                            )
                        }
                    )
                )
            )
            pms_api_check_access(user=self.env.user, records=folios)
            lines_to_invoice = {
                newInvoiceLinesInfo[i].saleLineId: newInvoiceLinesInfo[i].quantity
                for i in range(0, len(newInvoiceLinesInfo))
            }
            # Add sections to invoice lines
            new_section_ids = (
                self.env["folio.sale.line"]
                .sudo()
                .browse([line.saleLineId for line in newInvoiceLinesInfo])
                .filtered(
                    lambda l: l.section_id.id
                    not in invoice.invoice_line_ids.mapped("folio_line_ids.id")
                )
                .mapped("section_id.id")
            )
            if new_section_ids:
                lines_to_invoice.update(
                    {section_id: 0 for section_id in new_section_ids}
                )
            new_invoice_lines = [
                item["invoice_line_ids"]
                for item in folios.get_invoice_vals_list(
                    lines_to_invoice=lines_to_invoice,
                    partner_invoice_id=partner.id,
                )
            ][0]
            # Update name of new invoice lines
            for item in filter(
                lambda l: not l[2].get("display_type"), new_invoice_lines
            ):
                item[2]["name"] = [
                    line.name
                    for line in newInvoiceLinesInfo
                    if [line.saleLineId] == item[2]["folio_line_ids"][0][2]
                ][0]
            cmd_invoice_lines.extend(new_invoice_lines)
        return cmd_invoice_lines

    def _is_reservation_invoice_locked(self, invoice):
        """True when pms_reservation_invoice_lock would refuse to post it.

        Asked by catching the UserError that the check raises, because the
        lock module exposes no predicate for it. Doing it here, rather than
        adding one to that module, keeps the posting path it guards
        untouched -- and this module is the one being discontinued, so it is
        the right place for the workaround to die with.
        """
        try:
            invoice._check_reservation_invoice_lock()
        except UserError:
            return True
        return False

    def _get_mapped_order_by_field(self, field):
        if field == "name":
            result = "name"
        elif field == "ref":
            result = "ref"
        elif field == "date":
            result = "invoice_date"
        elif field == "amount":
            result = "amount_total"
        else:
            raise werkzeug.exceptions.MethodNotAllowed(description="Field not allowed")
        return result
