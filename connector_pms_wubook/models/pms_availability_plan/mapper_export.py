# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping

# What a night says when no rule covers it: nothing is restricted. Wubook
# holds a value for every night, so saying nothing would leave whatever it
# was last told, and a lifted restriction would never come off.
_NO_RESTRICTION = {
    "min_stay": 0,
    "min_stay_arrival": 0,
    "max_stay": 0,
    "max_stay_arrival": 0,
    "closed": False,
    "closed_arrival": False,
    "closed_departure": False,
}


class ChannelWubookPmsAvailabilityPlanMapperExport(Component):
    _name = "channel.wubook.pms.availability.plan.mapper.export"
    _inherit = "channel.wubook.mapper.export"

    _apply_on = "channel.wubook.pms.availability.plan"

    @mapping
    def name(self, record):
        """Only emit ``name`` when it differs from the last value pushed to
        Wubook (or has never been pushed). The adapter calls
        ``rplan_rename_rplan`` only when ``name`` is present, so this skip
        avoids redundant XMLRPC traffic when the export was triggered by
        rule changes via the scheduler.
        """
        last = record.wubook_last_synced_name
        if last and last == record.name:
            return None
        return {"name": record.name}

    @mapping
    def items(self, record):
        """One item per (room type, night) of the window being pushed.

        The rules are ranges and Wubook is written night by night, so the
        ranges are expanded here. The expansion is dense on purpose: a night
        no rule covers is sent as unrestricted, which is what it means, and
        is how a restriction that was lifted actually comes off the channel.
        """
        window = record._wubook_export_window()
        if not window:
            return None
        date_from, date_to = window
        room_types = record._wubook_export_room_types()
        if not room_types:
            return None
        external_ids = self._external_room_ids(room_types)
        winner = self.env["pms.availability.plan.rule"]._resolve_rules(
            record.odoo_id.id,
            record.backend_id.pms_property_id.id,
            date_from,
            date_to,
            room_type_ids=room_types.ids,
        )
        items = []
        for offset in range((date_to - date_from).days + 1):
            date = date_from + datetime.timedelta(days=offset)
            for room_type in room_types:
                rule = winner.get((room_type.id, date))
                values = (
                    {field: rule[field] for field in _NO_RESTRICTION}
                    if rule
                    else dict(_NO_RESTRICTION)
                )
                items.append(
                    {
                        **values,
                        "date": date,
                        "id_room": external_ids[room_type.id],
                    }
                )
        return {"items": items}

    def _external_room_ids(self, room_types):
        """:return: ``{room_type_id: external id}`` on this backend."""
        binder = self.binder_for("channel.wubook.pms.room.type")
        external_ids = {}
        for room_type in room_types:
            external_id = binder.to_external(room_type, wrap=True)
            if not external_id:
                raise ValidationError(
                    _(
                        "External record of Room Type id [%(code)s] %(room)s "
                        "does not exists. It should be exported in "
                        "_export_dependencies"
                    )
                    % {"code": room_type.id, "room": room_type.name}
                )
            external_ids[room_type.id] = external_id
        return external_ids
