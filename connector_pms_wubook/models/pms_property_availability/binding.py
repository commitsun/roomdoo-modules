# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from ..common.wubook_window import accepted_window


class ChannelWubookPmsPropertyAvailabilityBinding(models.Model):
    _name = "channel.wubook.pms.property.availability"
    _inherit = "channel.wubook.binding"
    _inherits = {"pms.property": "odoo_id"}

    external_id = fields.Char(string="External ID")

    odoo_id = fields.Many2one(
        comodel_name="pms.property",
        string="Odoo ID",
        required=True,
        ondelete="cascade",
    )

    # What the channel is told is one number per room type and night, but
    # that number is not stored anywhere: it is resolved from the physical
    # availability and the declared inventory when the export runs. So what
    # the binding has to remember is not a value, it is which nights still
    # have to be published. An empty window means a full push, which is what
    # a property connected for the first time or a manual resync wants.
    wubook_pending_date_from = fields.Date(
        string="Pending From",
        readonly=True,
        help="First night waiting to be pushed to Wubook.",
    )
    wubook_pending_date_to = fields.Date(
        string="Pending To",
        readonly=True,
        help="Last night waiting to be pushed to Wubook.",
    )

    def _wubook_stage_pending_window(self, date_from, date_to):
        """Widen the window waiting to be exported so it also covers
        ``date_from``..``date_to``.
        """
        for record in self:
            pending_from = record.wubook_pending_date_from
            pending_to = record.wubook_pending_date_to
            record.write(
                {
                    "wubook_pending_date_from": min(
                        pending_from or date_from, date_from
                    ),
                    "wubook_pending_date_to": max(pending_to or date_to, date_to),
                    # Availability moved, so the property is no longer in
                    # sync. This is what the scheduler looks at to pick it up.
                    "actual_write_date": fields.Datetime.now(),
                }
            )

    def _wubook_export_window(self):
        """:return: the ``(first, last)`` nights this export has to push,
        clipped to what Wubook accepts, or ``None`` when there is nothing
        left inside the window.
        """
        self.ensure_one()
        accepted_from, accepted_to = accepted_window()
        date_from = max(self.wubook_pending_date_from or accepted_from, accepted_from)
        date_to = min(self.wubook_pending_date_to or accepted_to, accepted_to)
        if date_from > date_to:
            return None
        return (date_from, date_to)

    def _wubook_export_room_types(self):
        """:return: the room type BINDINGS whose availability this backend
        publishes.

        The room types that actually have rooms in the property, bound on
        the backend and not in a room type class marked as not to be
        synchronized. The binding is what is returned because the export
        needs its external id and its default availability, which is the
        ceiling to apply when no inventory rule declares one.
        """
        self.ensure_one()
        backend = self.backend_id
        nosync = (
            backend.backend_type_id.child_id.room_type_class_ids.get_nosync_shortnames()
        )
        room_type_ids = self.odoo_id.room_ids.filtered("active").mapped("room_type_id")
        if not room_type_ids:
            return self.env["channel.wubook.pms.room.type"].browse()
        bindings = self.env["channel.wubook.pms.room.type"].search(
            [
                ("backend_id", "=", backend.id),
                ("odoo_id", "in", room_type_ids.ids),
                ("external_id", "!=", False),
            ]
        )
        return bindings.filtered(
            lambda binding: binding.odoo_id.class_id.default_code not in nosync
        )

    @api.model
    def export_data(self, backend_record=None):
        """Prepare the export of Availability to Channel"""
        return (
            self.env["channel.wubook.pms.property.availability"]
            .with_delay()
            .export_record(backend_record, backend_record.pms_property_id)
        )
