# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime
import logging

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create

from ...models.pms_reservation.mapper_import import get_room_type
from .channel_booking import MIRAI_CHANNEL_ID
from .channel_notes import MIRAI_NOTE_PREFIX, merge_tagged_lines
from .retryable import RETRYABLE_ERRORS

_logger = logging.getLogger(__name__)


class ChannelWubookPmsFolioMapperImport(Component):
    _name = "channel.wubook.pms.folio.mapper.import"
    _inherit = "channel.wubook.mapper.import"

    _apply_on = "channel.wubook.pms.folio"

    direct = [
        ("customer_phone", "mobile"),
        ("customer_mail", "email"),
    ]

    children = [
        ("reservations", "reservation_ids", "channel.wubook.pms.reservation"),
    ]

    @only_create
    @mapping
    def backend_id(self, record):
        return {"backend_id": self.backend_record.id}

    @only_create
    @mapping
    def property_id(self, record):
        return {"pms_property_id": self.backend_record.pms_property_id.id}

    @mapping
    def wubook_status(self, record):
        return {
            "wubook_status": record["was_modified"] and "7" or str(record["status"])
        }

    @mapping
    def payment_gateway_fee(self, record):
        # Wubook payments gateway
        if record["id_channel"] == 0 and record["payment_gateway_fee"]:
            return {"payment_gateway_fee": record["payment_gateway_fee"]}

        # Expedia payments gateway
        if (
            record["id_channel"] == 1
            and record["channel_data"].get("pay_model") == "merchant"
        ):
            return {"payment_gateway_fee": record["amount"]}

        # Booking payments gateway
        if record["id_channel"] == 2 and record["ancillary"].get("Payment"):
            if record["ancillary"]["Payment"].get("Payout") == "BankTransfer":
                return {"payment_gateway_fee": record["amount"]}
        # Not online pre payment
        return {"payment_gateway_fee": 0}

    @only_create
    @mapping
    def sale_channel_origin_id(self, record):
        if record["id_channel"] == 0:
            type_id = (
                self.backend_record.backend_type_id.child_id.direct_channel_type_id.id
            )
        else:
            agency = self.backend_record.backend_type_id.child_id.ota_ids.filtered(
                lambda x: x.wubook_ota == record["id_channel"]
            ).agency_id
            type_id = agency.sale_channel_id.id
        return {"sale_channel_origin_id": type_id}

    @only_create
    @mapping
    def agency_id(self, record):
        if record["id_channel"] != 0:
            # TODO refactor and create agency bindings and use connector tools as to_internal
            agency = self.backend_record.backend_type_id.child_id.ota_ids.filtered(
                lambda x: x.wubook_ota == record["id_channel"]
            ).agency_id
            if not agency:
                raise ValidationError(
                    _(
                        """Id Channel '%s' not found on mapping.
                        Please check it on the Backend Type configuration"""
                    )
                    % (record["id_channel"],)
                )
            return {"agency_id": agency.id}

    @mapping
    def reservation_origin_code(self, record):
        modified_reservations = record["modified_reservations"]
        if len(modified_reservations) > 1:
            raise NotImplementedError(
                _("Multiple modified reservations is not supported yet %s")
                % (modified_reservations,)
            )
        if not modified_reservations or modified_reservations[0] == [
            record["reservation_code"]
        ]:
            return {"reservation_origin_code": record["reservation_code"]}
        else:
            return {"reservation_origin_code": modified_reservations[0]}

    @mapping
    def partner_name(self, record):
        return {
            "partner_name": f"{record['customer_surname']}, {record['customer_name']}"
        }

    @mapping
    def lang(self, record):
        lang = self.env["res.lang"].search(
            [("iso_code", "=", record["customer_language_iso"])], limit=1
        )
        if lang:
            return {"lang": lang.code}
        return {}

    @mapping
    def internal_comment(self, record):
        """Keep on top of the folio internal notes the lines for reception
        that Mirai passes (loyalty club, upselling to collect), when its OTA
        mapping enables them.

        They are rebuilt on every import of the booking (creation,
        modification, cancellation, re-import) and merged with the current
        text, read from the binding when the folio already exists: the staff
        text is kept and nothing is written when the lines did not change.
        """
        binding = self.options.binding
        if binding is None:
            # The binder maps the booking without options, only to look for
            # the folio of a modified booking: the importer maps it again
            # with the binding (an empty recordset on creation).
            return {}
        if not self._channel_notes_enabled(record.get("id_channel")):
            return {}
        pms_property = self.backend_record.pms_property_id
        lang = (
            pms_property.lang
            or pms_property.company_id.partner_id.lang
            or self.env.lang
        )
        folios = self.env["channel.wubook.pms.folio"].with_context(lang=lang)
        try:
            # Read only work: a flushing savepoint would run the precommit
            # hooks of the import here (e.g. the availability export buffer).
            with self.env.cr.savepoint(flush=False):
                lines = folios._get_channel_note_lines(record)
                current = (binding.internal_comment if binding else False) or ""
                merged = merge_tagged_lines(current, MIRAI_NOTE_PREFIX, lines)
        except RETRYABLE_ERRORS:
            raise
        except Exception:
            # Notes for reception must never make a booking import fail
            _logger.exception(
                "Could not compute the [Mirai] lines of the internal notes of"
                " the Wubook booking %s",
                record.get("reservation_code"),
            )
            if binding:
                folios._notify_channel_notes_failure(binding.odoo_id)
            return {}
        if merged == current:
            return {}
        return {"internal_comment": merged or False}

    def _channel_notes_enabled(self, id_channel):
        """Whether the folio internal notes get the lines of ``id_channel``:
        only Mirai passes any, and its row of the OTA mappings of the backend
        type must enable them. It runs on every import, so it only reads that
        mapping, and a mapping that cannot be read leaves the notes alone."""
        if id_channel != MIRAI_CHANNEL_ID:
            return False
        try:
            otas = self.backend_record.backend_type_id.child_id.ota_ids
            return any(
                ota.import_channel_notes for ota in otas if ota.wubook_ota == id_channel
            )
        except RETRYABLE_ERRORS:
            raise
        except Exception:
            _logger.exception(
                "Could not read whether the Wubook channel %s writes folio notes",
                id_channel,
            )
            return False


class ChannelWubookPmsFolioChildMapperImport(Component):
    _name = "channel.wubook.pms.folio.child.mapper.import"
    _inherit = "channel.wubook.child.mapper.import"
    _apply_on = "channel.wubook.pms.reservation"

    def get_all_items(self, mapper, items, parent, to_attr, options):
        binding = options.get("binding")
        if not binding:
            return super().get_all_items(mapper, items, parent, to_attr, options)
        import_mapper = self.component(usage="import.mapper")
        reservations = binding.reservation_ids
        mapped = []
        for item in items:
            map_record = mapper.map_record(item, parent=parent)
            if self.skip_item(map_record):
                continue
            item_values = self.get_item_values(map_record, to_attr, options)
            if item_values:
                room_type = get_room_type(import_mapper, map_record.source["room_id"])
                days = [x["day"] for x in map_record.source["lines"]]
                checkin, checkout = min(days), max(days) + datetime.timedelta(days=1)
                reservation = reservations.filtered(
                    lambda x: all(
                        [
                            x.room_type_id == room_type,
                            x.checkin == checkin,
                            x.checkout == checkout,
                            # Skip reservations cancelled because of a previous
                            # modification: they are historical artefacts of
                            # earlier versions of the same booking and must not
                            # be revived. A brand-new reservation is created
                            # for the incoming payload instead.
                            not (
                                x.state == "cancel" and x.cancelled_reason == "modified"
                            ),
                        ]
                    )
                )
                if reservation:
                    item_values["id"] = reservation[0].id
                    del item_values["reservation_line_ids"]
                    reservations -= reservation[0]
                mapped.append(item_values)

        # TODO: move this code to importer and try to use _get_item_values
        # outside the main loop in another one and create wubook_status on
        # reservation
        if reservations and map_record.parent.source["was_modified"] == 0:
            draft_invoices = binding.move_ids.filtered(lambda x: x.state == "draft")
            if draft_invoices:
                draft_invoices.button_cancel()
                draft_invoices.unlink()
            reservations.filtered(lambda x: x.allowed_cancel).with_context(
                modified=True, force_write_blocked=True
            ).action_cancel()

        return mapped

    def format_items(self, items_values):
        ops = []
        for values in items_values:
            _id = values.pop("id", None)
            if _id:
                ops.append((1, _id, values))
            else:
                ops.append((0, 0, values))

        return ops
