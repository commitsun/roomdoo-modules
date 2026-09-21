# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Create the Ertzaintza reservation communications from the reservation flow.

The triggers mirror the SES ones of ``pms_l10n_es`` so that a receptionist
sees the same behaviour, with one difference forced by the service: the A19
web service has no cancellation and no modification operation, so a
reservation that changes after being reported can only leave a note in the
chatter.
"""
from odoo import _, api, fields, models

from .ertzaintza_codes import ENTITY_PV, ENTITY_RH, INSTITUTION_CODE

COMMUNICATION_STATES = [
    ("not_applicable", "Not applicable"),
    ("incomplete", "Incomplete guest data"),
    ("to_send", "Pending notification"),
    ("error_sending", "Error sending"),
    ("rejected", "Rejected by the Ertzaintza"),
    ("processed", "Processed"),
    ("cancelled", "Cancelled"),
    ("error_create", "Error creating"),
]
WATCHED_FIELDS = ("adults", "checkin", "checkout")


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    ertzaintza_communication_ids = fields.One2many(
        comodel_name="pms.ertzaintza.communication",
        inverse_name="reservation_id",
        string="Ertzaintza Communications",
    )
    is_ertzaintza = fields.Boolean(
        string="Reports to the Ertzaintza",
        compute="_compute_is_ertzaintza",
        store=True,
    )
    ertzaintza_status_reservation = fields.Selection(
        selection=COMMUNICATION_STATES,
        string="Ertzaintza Status",
        compute="_compute_ertzaintza_status",
    )
    ertzaintza_status_traveller_report = fields.Selection(
        selection=COMMUNICATION_STATES,
        string="Ertzaintza Status traveller",
        compute="_compute_ertzaintza_status",
    )

    @api.depends("pms_property_id", "pms_property_id.institution")
    def _compute_is_ertzaintza(self):
        for record in self:
            record.is_ertzaintza = (
                record.pms_property_id.institution == INSTITUTION_CODE
            )

    @api.depends("ertzaintza_communication_ids.state", "pms_property_id.institution")
    def _compute_ertzaintza_status(self):
        for record in self:
            for entity, field_name in (
                (ENTITY_RH, "ertzaintza_status_reservation"),
                (ENTITY_PV, "ertzaintza_status_traveller_report"),
            ):
                if record.pms_property_id.institution != INSTITUTION_CODE:
                    record[field_name] = "not_applicable"
                    continue
                communications = record.ertzaintza_communication_ids.filtered(
                    lambda communication, entity=entity: communication.entity == entity
                ).sorted(key=lambda communication: communication.create_date or "")
                record[field_name] = (
                    communications[-1].state if communications else "error_create"
                )

    # ------------------------------------------------------------------
    def _ertzaintza_create_reservation_communication(self):
        """Queue an RH communication for this reservation."""
        self.ensure_one()
        return self.env["pms.ertzaintza.communication"].create(
            {
                "reservation_id": self.id,
                "entity": ENTITY_RH,
                "state": "to_send",
                "contract_reference": (self.name or "")[:50],
            }
        )

    @api.model_create_multi
    def create(self, vals_list):
        reservations = super().create(vals_list)
        for reservation in reservations:
            if (
                reservation.pms_property_id.institution == INSTITUTION_CODE
                and reservation.reservation_type != "out"
            ):
                reservation._ertzaintza_create_reservation_communication()
        return reservations

    def write(self, vals):
        # pms.reservation.write sits on every hot path of the PMS (the channel
        # manager imports included), so nothing else is read when no
        # reservation of the batch reports to the Ertzaintza. The previous
        # state is captured before the write to tell a cancellation from a
        # re-activation.
        watched = self.filtered(
            lambda record: record.is_ertzaintza and record.reservation_type != "out"
        )
        previous_states = {record.id: record.state for record in watched}
        result = super().write(vals)
        for record in watched:
            record._ertzaintza_handle_write(vals, previous_states.get(record.id))
        return result

    def _ertzaintza_handle_write(self, vals, previous_state):
        self.ensure_one()
        communications = self.ertzaintza_communication_ids
        new_state = vals.get("state")
        was_cancelled = previous_state == "cancel"

        if new_state == "cancel" and not was_cancelled:
            open_communications = communications.filtered(
                lambda communication: communication.state
                in ("incomplete", "to_send", "error_sending", "rejected")
            )
            open_communications.write({"state": "cancelled"})
            if communications.filtered(
                lambda communication: communication.state == "processed"
            ):
                self.sudo().message_post(
                    body=_(
                        "The reservation was cancelled after being reported to "
                        "the Ertzaintza. The A19 service has no cancellation "
                        "operation: notify the Ertzaintza manually if needed."
                    )
                )
            return

        if new_state and new_state != "cancel" and was_cancelled:
            pending = communications.filtered(
                lambda communication: communication.entity == ENTITY_RH
                and communication.state in ("to_send", "processed")
            )
            if not pending:
                self._ertzaintza_create_reservation_communication()
            return

        if self.state == "cancel":
            return
        if any(field_name in vals for field_name in WATCHED_FIELDS) and (
            communications.filtered(
                lambda communication: communication.entity == ENTITY_RH
                and communication.state == "processed"
            )
        ):
            # A pending communication needs no action: its XML is built when
            # it is sent, so it already carries the new values.
            self.sudo().message_post(
                body=_(
                    "The reservation changed after being reported to the "
                    "Ertzaintza. The A19 service has no modification "
                    "operation: the change was not notified."
                )
            )
