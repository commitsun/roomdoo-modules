# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from psycopg2.extensions import AsIs

from odoo import _, fields

from odoo.addons.component.core import Component
from odoo.addons.connector.exception import IDMissingInBackend
from odoo.addons.connector_pms.components.adapter import ChannelAdapterError

_logger = logging.getLogger(__name__)


class ChannelWubookPmsFolioDelayedBatchImporter(Component):
    _name = "channel.wubook.pms.folio.delayed.batch.importer"
    _inherit = "channel.wubook.delayed.batch.importer"

    _apply_on = "channel.wubook.pms.folio"


class ChannelWubookPmsFolioDirectBatchImporter(Component):
    _name = "channel.wubook.pms.folio.direct.batch.importer"
    _inherit = "channel.wubook.direct.batch.importer"

    _apply_on = "channel.wubook.pms.folio"


class ChannelWubookPmsFolioImporter(Component):
    _name = "channel.wubook.pms.folio.importer"
    _inherit = "channel.wubook.importer"

    _apply_on = "channel.wubook.pms.folio"

    def run(self, external_id, external_data=None, external_fields=None):
        if not external_data:
            external_data = self.backend_adapter.read(external_id)
            if not external_data:
                raise IDMissingInBackend(
                    _("Record with external_id '%s' does not exist in Backend")
                    % (external_id,)
                )
        binder = self.binder_for("channel.wubook.pms.folio")
        binding = binder.to_internal(external_id)
        is_cancel = not external_data.get("was_modified") and str(
            external_data.get("status", "")
        ) in ("3", "5", "6")
        if binding and is_cancel:
            try:
                return super().run(
                    external_id,
                    external_data=external_data,
                    external_fields=external_fields,
                )
            except (IDMissingInBackend, ChannelAdapterError) as e:
                _logger.warning(
                    "Folio %s cancellation: full import failed (%s). "
                    "Falling back to local-only cancellation because the "
                    "folio already exists and Wubook reports a cancelled "
                    "status. Some Wubook dependencies referenced by the "
                    "reservation no longer exist on Wubook.",
                    external_id,
                    e,
                )
                binding.with_context(
                    connector_no_export=True,
                    force_write_blocked=True,
                    mail_create_nosubscribe=True,
                    force_overbooking=True,
                ).write({"wubook_status": str(external_data["status"])})
                self._after_import(binding)
                return True
        return super().run(
            external_id,
            external_data=external_data,
            external_fields=external_fields,
        )

    def _import_dependencies(self, external_data, external_fields):
        self._import_dependency(
            {x["room_id"] for x in external_data.get("reservations", [])},
            "channel.wubook.pms.room.type",
        )
        self._import_dependency(
            {x["board"] for x in external_data.get("reservations", []) if x["board"]},
            "channel.wubook.pms.board.service",
        )
        self._import_dependency(
            {x["rate_id"] for x in external_data.get("reservations", [])},
            "channel.wubook.product.pricelist",
        )
        self._import_dependency(
            [
                r
                for r in external_data["modified_reservations"]
                if r != external_data["reservation_code"]
            ],
            "channel.wubook.pms.folio",
        )

    def _after_import(self, binding):
        folio = binding.odoo_id
        # If Wubook status is 7 (Wubook modification) the folio state is not changed
        if binding.wubook_status in ("3", "5", "6") and binding.state != "cancel":
            # If folio has draft invoice, cancel invoices:
            draft_invoices = folio.move_ids.filtered(lambda x: x.state == "draft")
            if draft_invoices:
                draft_invoices.button_cancel()
                draft_invoices.unlink()
            folio.action_cancel()
        elif binding.wubook_status in ("1", "2", "4") and binding.state == "cancel":
            folio.with_context(confirm_all_reservations=True).action_confirm()

        # TODO: move get_all_items action_cancel here
        # binding.reservation_ids.filtered(
        #     lambda x: x["wubook_status"] == "5"
        # ).action_cancel()

        # Pre payment Folio
        if binding.payment_gateway_fee > 0:
            # REVIEW: If the agency has configured invoice the agency manually,
            # and a payment from the agency enters, we preset in the folio
            # invoice the agency to true (p.e. Expedia Collect)
            if folio.agency_id and folio.agency_id.invoice_to_agency == "manual":
                folio.invoice_to_agency = True
            # Wubook Pre payment
            if (
                folio.sale_channel_origin_id
                == binding.backend_id.backend_type_id.child_id.direct_channel_type_id
            ):
                payment_method_line = binding.backend_id.wubook_payment_method_line_id
            # Other OTAs Pre payment
            else:
                payment_method_line = (
                    binding.backend_id.backend_journal_ota_ids.filtered(
                        lambda x: x.agency_id.id == folio.agency_id.id
                    ).payment_method_line_id
                )
                journal = payment_method_line.journal_id
                # auto update OTAs payment on modified/cancelled reservations
                ota_payments = folio.payment_ids.filtered(
                    lambda x: x.journal_id.id == journal.id
                )
                if ota_payments:
                    if folio.state == "cancel" and folio.amount_total == 0:
                        ota_payments.action_draft()
                        ota_payments.action_cancel()
                        folio.sudo().message_post(
                            body=_(
                                "The folio and the OTA payment have been cancelled."
                            ),
                            subtype_id=self.env.ref("mail.mt_note").id,
                            email_from=self.env.user.partner_id.email_formatted
                            or folio.pms_property_id.email_formatted,
                        )
                    elif binding.payment_gateway_fee != sum(
                        ota_payments.mapped("amount")
                    ):
                        ota_payments.action_draft()
                        ota_payments[0].amount = binding.payment_gateway_fee
                        ota_payments[0].action_post()
                        if len(ota_payments) > 1:
                            ota_payments[1:].action_cancel()
                        folio.sudo().message_post(
                            body=_(
                                "The amount of the payment has been updated"
                                " to %s by OTA modification"
                                % binding.payment_gateway_fee
                            ),
                            subtype_id=self.env.ref("mail.mt_note").id,
                            email_from=self.env.user.partner_id.email_formatted
                            or folio.pms_property_id.email_formatted,
                        )
            # We omit those payments from agencies that that have already
            # been registered in previous imports, that the total of the
            # folio is zero, or that do not have a journal configured
            if (
                not folio.payment_ids.filtered(lambda p: p.state == "posted")
                and folio.amount_total > 0
                and payment_method_line
            ):
                payment_amount = (
                    binding.payment_gateway_fee
                    if binding.payment_gateway_fee <= folio.amount_total
                    else folio.amount_total
                )
                folio.do_payment(
                    payment_method_line,
                    self.env.user,
                    payment_amount,
                    folio,
                    reservations=False,
                    services=False,
                    partner=folio.partner_id,
                )

        # REVIEW: mark actual_write_date to now
        # in availability and force to update Wubook avail changes
        # (Wubook add/delete avail by itself)
        dates = folio.mapped("reservation_ids.reservation_line_ids.date")
        avails = self.env["channel.wubook.pms.availability"].search(
            [
                ("backend_id", "=", binding.backend_id.id),
                ("date", ">=", min(dates)),
                ("date", "<=", max(dates)),
                ("room_type_id", "in", folio.mapped("reservation_ids.room_type_id.id")),
            ]
        )
        # %s/%%s here is psycopg2 placeholder syntax, the `%` operator below
        # only substitutes AsIs into actual_write_date.
        query = (
            'UPDATE "channel_wubook_pms_availability" '  # noqa: UP031
            'SET "actual_write_date"=%s WHERE id IN %%s'
            % (AsIs("(now() at time zone 'UTC')"),)
        )
        cr = self.env.cr
        for sub_ids in cr.split_for_in_conditions(
            set(avails.filtered(lambda i: i.date >= fields.Date.today()).ids)
        ):
            cr.execute(query, [sub_ids])

    def _create(self, model, values):
        """Create the Internal record"""
        return super()._create(
            model.with_context(mail_create_nosubscribe=True, force_overbooking=True),
            values,
        )

    def _update(self, binding, values):
        """Update an Internal record"""
        return super()._update(
            binding.with_context(mail_create_nosubscribe=True, force_overbooking=True),
            values,
        )
