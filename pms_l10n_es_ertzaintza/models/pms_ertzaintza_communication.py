# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Queue and audit log of the communications sent to the Ertzaintza.

One record is one ``comunicacion`` block: a reservation (``RH``) or a
traveller report (``PV``). The A19 service is synchronous and has neither a
cancellation nor a modification operation, so the life cycle is shorter than
the SES one: there is no "pending processing" state and re-sending the same
``contrato/referencia`` simply answers ``CTO01``, which we treat as success.
"""
import base64
import logging
from datetime import datetime

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ertzaintza_client import (
    ErtzaintzaClient,
    ErtzaintzaTransportError,
    parse_response,
)
from .ertzaintza_codes import (
    ENTITY_PV,
    ENTITY_RH,
    INSTITUTION_CODE,
    MAX_COMMUNICATIONS_PER_REQUEST,
)
from .ertzaintza_xml_builder import build_solicitud, validate_xsd

_logger = logging.getLogger(__name__)

GUEST_REPORTABLE_STATES = ("onboard", "done")
MAX_SEND_ATTEMPTS = 5
# States a communication can still leave on its own.
OPEN_STATES = ("incomplete", "to_send", "error_sending", "rejected")
FINAL_STATES = ("processed", "cancelled")
FILENAME_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
# The A19 service stamps its answers in Spanish local time.
SERVICE_TIMEZONE = "Europe/Madrid"


class PmsErtzaintzaCommunication(models.Model):
    _name = "pms.ertzaintza.communication"
    _description = "Ertzaintza A19 Communication"
    _order = "create_date desc"

    reservation_id = fields.Many2one(
        comodel_name="pms.reservation",
        string="Reservation",
        help="Reservation reported in this communication",
        required=True,
        index=True,
        ondelete="cascade",
    )
    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        string="Property",
        related="reservation_id.pms_property_id",
        store=True,
        index=True,
    )
    entity = fields.Selection(
        selection=[(ENTITY_RH, "Reservation"), (ENTITY_PV, "Traveller report")],
        required=True,
        index=True,
    )
    state = fields.Selection(
        selection=[
            ("incomplete", "Incomplete guest data"),
            ("to_send", "Pending notification"),
            ("error_sending", "Error sending"),
            ("rejected", "Rejected by the Ertzaintza"),
            ("processed", "Processed"),
            ("cancelled", "Cancelled"),
        ],
        default="to_send",
        required=True,
        index=True,
    )
    contract_reference = fields.Char(
        size=50,
        help="Value sent as contrato/referencia. The Ertzaintza rejects a "
        "second communication with the same reference (CTO01), so it is the "
        "key that makes a re-send idempotent.",
    )
    checkin_partner_ids = fields.Many2many(
        comodel_name="pms.checkin.partner",
        relation="pms_ertzaintza_communication_checkin_partner_rel",
        column1="communication_id",
        column2="checkin_partner_id",
        string="Reported guests",
    )
    communication_xml = fields.Text(string="XML Com.")
    communication_soap = fields.Text(string="SOAP Com.")
    response_soap = fields.Text(string="SOAP Resp.")
    request_uuid = fields.Char(string="Request UUID")
    result_code = fields.Char()
    result_description = fields.Char()
    errors = fields.Text()
    sending_result = fields.Text()
    communication_time = fields.Datetime()
    processed_time = fields.Datetime()
    send_attempt_count = fields.Integer(default=0)
    manual_export_date = fields.Datetime()
    manually_sent = fields.Boolean()
    xml_filename = fields.Char(compute="_compute_xml_filename")

    @api.depends(
        "entity",
        "contract_reference",
        "reservation_id.name",
        "pms_property_id.institution_property_id",
    )
    def _compute_xml_filename(self):
        for record in self:
            reference = record.contract_reference or record.reservation_id.name or ""
            reference = "".join(
                char if char in FILENAME_SAFE else "_" for char in reference
            )
            day = record.create_date or fields.Datetime.now()
            establishment = record.pms_property_id.institution_property_id or "NA"
            record.xml_filename = (
                f"A19_{record.entity or 'NA'}_{establishment}"
                f"_{reference or 'NA'}_{day.strftime('%Y%m%d')}.xml"
            )

    # ------------------------------------------------------------------
    # Creation helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_or_create_pv(self, reservation):
        """Traveller report of ``reservation`` that is still open, or a new one.

        Guests can arrive after the report was sent; in that case a
        supplementary communication is created with its own reference
        (``<name>-2``) so that the Ertzaintza does not answer CTO01.
        """
        existing = self.search(
            [
                ("reservation_id", "=", reservation.id),
                ("entity", "=", ENTITY_PV),
                ("state", "in", ("incomplete", "to_send")),
            ],
            order="create_date desc",
            limit=1,
        )
        if existing:
            return existing
        previous = self.search_count(
            [("reservation_id", "=", reservation.id), ("entity", "=", ENTITY_PV)]
        )
        reference = reservation.name or ""
        if previous:
            reference = f"{reference}-{previous + 1}"
        return self.create(
            {
                "reservation_id": reservation.id,
                "entity": ENTITY_PV,
                "state": "incomplete",
                "contract_reference": reference[:50],
            }
        )

    def _pv_pending_checkin_partners(self):
        """Guests of the reservation not reported by a processed PV yet."""
        self.ensure_one()
        reported = self.search(
            [
                ("reservation_id", "=", self.reservation_id.id),
                ("entity", "=", ENTITY_PV),
                ("state", "=", "processed"),
                ("id", "!=", self.id),
            ]
        ).mapped("checkin_partner_ids")
        return self.reservation_id.checkin_partner_ids.filtered(
            lambda guest: guest.state in GUEST_REPORTABLE_STATES
            and guest not in reported
        )

    # ------------------------------------------------------------------
    # XML
    # ------------------------------------------------------------------
    def _xml_item(self, ignore_not_onboard=False):
        """Return ``(item, problems)`` for the XML builder."""
        self.ensure_one()
        reservation = self.reservation_id
        item = {
            "reservation": reservation,
            "reference": self.contract_reference or reservation.name,
        }
        problems = []
        if self.entity == ENTITY_PV:
            guests = self._pv_pending_checkin_partners()
            if not ignore_not_onboard:
                missing = reservation.checkin_partner_ids.filtered(
                    lambda guest: guest.state not in GUEST_REPORTABLE_STATES
                )
                if missing or len(reservation.checkin_partner_ids) < reservation.adults:
                    problems.append(_("Not every guest has checked in yet."))
            item["checkin_partners"] = guests
        return item, problems

    def _build_xml(self, ignore_not_onboard=False):
        """Build the document of this communication.

        Returns ``(xml_string, problems)``; on success the XML and the
        reported guests are stored on the record.
        """
        self.ensure_one()
        item, problems = self._xml_item(ignore_not_onboard=ignore_not_onboard)
        if problems:
            return "", problems
        xml, problems = build_solicitud(self.entity, [item])
        if not problems:
            problems = validate_xsd(xml, self.entity)
        if problems:
            return "", problems
        values = {"communication_xml": xml}
        if self.entity == ENTITY_PV:
            values["checkin_partner_ids"] = [(6, 0, item["checkin_partners"].ids)]
        self.write(values)
        return xml, []

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def _send(self):
        """Build, sign and post this communication. Returns True on success."""
        self.ensure_one()
        pms_property = self.pms_property_id
        if pms_property.institution != INSTITUTION_CODE:
            raise UserError(
                _(
                    "%s does not report to the Ertzaintza.",
                    pms_property.display_name,
                )
            )
        configuration_problems = pms_property._ertzaintza_config_problems()
        if configuration_problems:
            self.write(
                {
                    "state": "error_sending",
                    "sending_result": "\n".join(configuration_problems),
                }
            )
            return False
        ignore_not_onboard = bool(self.env.context.get("ertzaintza_ignore_not_onboard"))
        xml, problems = self._build_xml(ignore_not_onboard=ignore_not_onboard)
        if problems:
            self.write({"state": "incomplete", "errors": "\n".join(problems)})
            return False

        client = ErtzaintzaClient.from_property(pms_property)
        signed = client.build_signed_request(
            self.entity, pms_property.institution_lessor_id, xml
        )
        self.write(
            {
                "communication_soap": signed.decode("utf-8"),
                "communication_time": fields.Datetime.now(),
                "send_attempt_count": self.send_attempt_count + 1,
                "sending_result": False,
            }
        )
        try:
            status, body = client.post(signed)
        except ErtzaintzaTransportError as error:
            # The service may still have processed it: a later retry answers
            # CTO01, which is treated as success.
            self.write({"state": "error_sending", "sending_result": str(error)})
            return False
        return self._apply_response(parse_response(status, body))

    def _apply_response(self, response):
        """Store an A19 answer and move the record to its new state."""
        self.ensure_one()
        values = {
            "response_soap": response.raw,
            "request_uuid": response.request_uuid or self.request_uuid,
            "result_code": response.state_code,
            "result_description": response.state_description,
            "errors": response.errors_text(),
        }
        classification = response.classification
        if classification in ("ok", "duplicate"):
            values["state"] = "processed"
            values["processed_time"] = (
                self._parse_service_datetime(response.processing_date)
                or fields.Datetime.now()
            )
        elif classification == "auth":
            values["state"] = "error_sending"
            values["sending_result"] = _(
                "The Ertzaintza did not accept the certificate or the lessor "
                "code of the establishment. This has to be sorted out with "
                "them, retrying will not help:\n%s",
                response.errors_text(),
            )
        elif classification == "transient":
            values["state"] = "error_sending"
            values["sending_result"] = response.errors_text() or _(
                "The Ertzaintza service is not available."
            )
        else:
            values["state"] = "rejected"
        self.write(values)
        if values["state"] == "rejected":
            self.reservation_id.sudo().message_post(
                body=_(
                    "The Ertzaintza rejected the %(entity)s communication:"
                    "<br/>%(errors)s",
                    entity=self.entity,
                    errors=(response.errors_text() or "").replace("\n", "<br/>"),
                )
            )
        return values["state"] == "processed"

    @staticmethod
    def _parse_service_datetime(value):
        """``2026-09-15T12:45:05.003`` (Spanish local time) -> naive UTC.

        Odoo stores datetimes in UTC, so the stamp of the service has to be
        converted or the communication would look two hours older than it is.
        """
        if not value:
            return False
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            return False
        if stamp.tzinfo is None:
            stamp = pytz.timezone(SERVICE_TIMEZONE).localize(stamp)
        return stamp.astimezone(pytz.UTC).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------
    def action_force_send(self):
        """Send now, whatever the scheduled actions were going to do."""
        pending = self.filtered(lambda record: record.state not in FINAL_STATES)
        pending.filtered(lambda record: record.state == "error_sending").write(
            {"send_attempt_count": 0}
        )
        return pending._send_batch()

    def action_cancel(self):
        for record in self:
            if record.state not in OPEN_STATES:
                raise UserError(
                    _("A %s communication cannot be cancelled.", record.state)
                )
        self.write({"state": "cancelled"})
        return True

    def action_mark_manually_sent(self):
        for record in self:
            if record.state in FINAL_STATES:
                raise UserError(
                    _("A %s communication cannot be marked as sent.", record.state)
                )
            record.write(
                {
                    "state": "processed",
                    "manually_sent": True,
                    "processed_time": fields.Datetime.now(),
                }
            )
            record.reservation_id.sudo().message_post(
                body=_(
                    "Ertzaintza %s communication marked as sent manually.",
                    record.entity,
                )
            )
        return True

    def action_download_xml(self):
        """Build the document of these communications and offer it as a file.

        This is the fallback while a production certificate is not authorised
        yet: the establishment uploads the file in the "Envío fichero" tab of
        the Ertzaintza portal.
        """
        if not self:
            raise UserError(_("There is nothing to export."))
        entities = set(self.mapped("entity"))
        properties = self.mapped("pms_property_id")
        if len(entities) > 1 or len(properties) > 1:
            raise UserError(
                _(
                    "Select communications of a single property and a single "
                    "communication type."
                )
            )
        if len(self) > MAX_COMMUNICATIONS_PER_REQUEST:
            raise UserError(
                _(
                    "The Ertzaintza accepts at most %s communications per file.",
                    MAX_COMMUNICATIONS_PER_REQUEST,
                )
            )
        entity = entities.pop()
        items, problems = [], []
        for record in self:
            item, item_problems = record._xml_item(
                ignore_not_onboard=bool(
                    self.env.context.get("ertzaintza_ignore_not_onboard")
                )
            )
            problems += item_problems
            items.append(item)
        xml, build_problems = build_solicitud(entity, items)
        problems += build_problems
        if problems:
            raise UserError("\n".join(problems))
        stamp = fields.Datetime.now().strftime("%Y%m%d%H%M%S")
        batch_name = (
            f"A19_{entity}_{properties.institution_property_id or 'NA'}_{stamp}.xml"
        )
        attachment = self.env["ir.attachment"].create(
            {
                "name": self[0].xml_filename if len(self) == 1 else batch_name,
                "type": "binary",
                "datas": base64.b64encode(xml.encode("utf-8")),
                "res_model": self._name,
                "res_id": self[0].id,
                "mimetype": "application/xml",
            }
        )
        self.write({"manual_export_date": fields.Datetime.now()})
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    # ------------------------------------------------------------------
    # Batch sending
    # ------------------------------------------------------------------
    def _send_batch(self):
        """Send every record, each one in its own savepoint.

        A record that blows up must neither stop the batch nor be retried
        forever, so the failure is stored on it and counted as an attempt.
        """
        for communication in self:
            try:
                with self.env.cr.savepoint():
                    communication._send()
            except Exception as error:  # noqa: BLE001 - the batch must go on
                _logger.exception(
                    "Ertzaintza communication %s could not be sent", communication.id
                )
                communication.write(
                    {
                        "state": "error_sending",
                        "send_attempt_count": communication.send_attempt_count + 1,
                        "sending_result": str(error),
                    }
                )
        return True

    @api.model
    def _pending_domain(self, entity):
        """Communications the cron has to pick up."""
        return [
            ("entity", "=", entity),
            ("pms_property_id.institution", "=", INSTITUTION_CODE),
            "|",
            ("state", "=", "to_send"),
            "&",
            ("state", "=", "error_sending"),
            ("send_attempt_count", "<", MAX_SEND_ATTEMPTS),
        ]

    @api.model
    def cron_send_communications(self, entity, limit=50):
        """Send the pending communications of one entity."""
        return self.search(
            self._pending_domain(entity), order="create_date", limit=limit
        )._send_batch()

    @api.model
    def cron_send_incomplete_traveller_reports(self, hours=20, limit=50):
        """Unblock the traveller reports that are waiting for guests.

        A report becomes sendable as soon as its data is complete. When it is
        still incomplete ``hours`` after it was opened, the guests already on
        board are reported, so that a reservation whose remaining guests never
        check in does not stay unreported (same rule as the SES integration).
        """
        deadline = fields.Datetime.now() - relativedelta(hours=hours)
        reports = self.search(
            [
                ("entity", "=", ENTITY_PV),
                ("state", "=", "incomplete"),
                ("pms_property_id.institution", "=", INSTITUTION_CODE),
            ],
            order="create_date",
            limit=limit,
        )
        for report in reports:
            try:
                with self.env.cr.savepoint():
                    report._unblock_incomplete(deadline)
            except Exception:  # noqa: BLE001 - the batch must go on
                _logger.exception(
                    "Ertzaintza traveller report %s could not be unblocked", report.id
                )
        return True

    def _unblock_incomplete(self, deadline):
        """Queue this report if it is complete, or send what we have."""
        self.ensure_one()
        __, problems = self._build_xml()
        if not problems:
            self.state = "to_send"
            return True
        if self.create_date > deadline or not self._pv_pending_checkin_partners():
            return False
        self.reservation_id.sudo().message_post(
            body=_(
                "Not every guest had checked in on time. Reported to the "
                "Ertzaintza with the guests on board:<br/>%s",
                "<br/>".join(problems),
            )
        )
        return self.with_context(ertzaintza_ignore_not_onboard=True)._send()
