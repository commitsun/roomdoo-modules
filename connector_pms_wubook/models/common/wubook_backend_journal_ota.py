# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WubookBackendJournalOTA(models.Model):
    _name = "wubook.backend.journal.ota"
    _description = "Journals Backend OTA Map to payment"
    _check_pms_properties_auto = True

    backend_id = fields.Many2one(
        comodel_name="channel.wubook.backend",
        required=True,
        ondelete="cascade",
    )

    pms_property_id = fields.Many2one(
        string="Property",
        related="backend_id.pms_property_id",
        store=True,
        readonly=True,
    )

    agency_id = fields.Many2one(
        string="OTA",
        comodel_name="res.partner",
        domain="[('id', 'in', allowed_agency_ids)]",
    )

    journal_id = fields.Many2one(
        string="Payment Journal",
        comodel_name="account.journal",
        domain="[('type', 'in', ('bank', 'cash'))]",
        check_pms_properties=True,
    )
    payment_method_line_id = fields.Many2one(
        string="Payment Method",
        comodel_name="account.payment.method.line",
        domain="[('journal_id', '=', journal_id)]",
        check_pms_properties=True,
    )

    register_channel_payments = fields.Boolean(
        help="Register automatically, on this journal, the amount the channel "
        "reports as paid online.",
    )

    @api.onchange("journal_id")
    def _onchange_journal_id(self):
        if self.payment_method_line_id.journal_id != self.journal_id:
            self.payment_method_line_id = False

    @api.constrains("register_channel_payments", "agency_id", "payment_method_line_id")
    def _check_register_channel_payments(self):
        for record in self.filtered("register_channel_payments"):
            if (
                not record.agency_id
                or record.payment_method_line_id.payment_type != "inbound"
            ):
                raise ValidationError(
                    _(
                        "To register channel payments, the OTA payment mapping "
                        "needs an OTA and an incoming payment method."
                    )
                )
            if self.sudo().search_count(
                [
                    ("id", "!=", record.id),
                    ("backend_id", "=", record.backend_id.id),
                    ("agency_id", "=", record.agency_id.id),
                    ("register_channel_payments", "=", True),
                ]
            ):
                raise ValidationError(
                    _(
                        "Only one payment mapping per OTA can register the "
                        "channel payments: %(ota)s already has one.",
                        ota=record.agency_id.display_name,
                    )
                )

    allowed_agency_ids = fields.Many2many(
        string="Allowed Agencies",
        help="It contains all available otas for this backend",
        comodel_name="res.partner",
        compute="_compute_allowed_agency_ids",
    )

    @api.depends("backend_id")
    def _compute_allowed_agency_ids(self):
        for record in self:
            agency_ids = self.backend_id.backend_type_id.child_id.ota_ids.mapped(
                "agency_id.id"
            )
            if agency_ids:
                record.allowed_agency_ids = self.env["res.partner"].browse(agency_ids)
            else:
                record.allowed_agency_ids = False
