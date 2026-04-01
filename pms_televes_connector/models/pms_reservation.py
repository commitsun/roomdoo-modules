# Copyright 2024 Commit [Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

_CHANGEDATA_TRIGGER_FIELDS = {"checkin", "checkout"}


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    televes_checkin_sent = fields.Boolean(
        string="Televes Check-in Sent",
        help="Whether a check-in notification has been sent to Televes",
        default=False,
        copy=False,
    )
    televes_current_room_id = fields.Many2one(
        string="Televes Current Room",
        comodel_name="pms.room",
        help="Last room sent to the Televes system for this reservation",
        copy=False,
    )

    def action_reservation_checkout(self):
        result = super().action_reservation_checkout()
        for record in self:
            if record.televes_checkin_sent:
                record.pms_property_id._televes_send_checkout(record)
                record.televes_checkin_sent = False
        return result

    def write(self, vals):
        if self.env.context.get("televes_skip"):
            return super().write(vals)

        # Reservations transitioning to onboard for the first time
        going_onboard = self.env["pms.reservation"]
        if vals.get("state") == "onboard":
            going_onboard = self.filtered(
                lambda r: r.state != "onboard" and not r.televes_checkin_sent
            )

        # Onboard reservations with a date change that require changedata
        if _CHANGEDATA_TRIGGER_FIELDS & vals.keys():
            need_changedata = self.filtered(
                lambda r: r.state == "onboard" and r.televes_checkin_sent
            )
        else:
            need_changedata = self.env["pms.reservation"]

        result = super().write(vals)

        for record in going_onboard:
            property_id = record.pms_property_id
            if not property_id.televes_enabled:
                continue
            today_room = property_id._televes_get_today_room(record)
            if not today_room:
                continue
            success = property_id._televes_send_checkin(record)
            if success:
                # Write tracking fields without re-triggering this logic
                record.with_context(televes_skip=True).write(
                    {
                        "televes_checkin_sent": True,
                        "televes_current_room_id": today_room.id,
                    }
                )

        for record in need_changedata:
            record.pms_property_id._televes_send_changedata(record)

        return result
