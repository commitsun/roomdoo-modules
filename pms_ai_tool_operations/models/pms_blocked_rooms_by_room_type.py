from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .pms_ai_operations_common import PROPERTY_IDS_INPUT, UNCOUNTED_STATES


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "date": {
                "type": "string",
                "format": "date",
                "description": "Night to check (YYYY-MM-DD). Default: today.",
            },
            "room_type_id": {
                "type": "integer",
                "description": "Room type to count (id). Use this or room_type_code.",
            },
            "room_type_code": {
                "type": "string",
                "maxLength": 20,
                "description": "Room type code (e.g. OUTS), as in its default code. "
                "Use this or room_type_id.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_blocked_rooms_by_room_type(
        self, date=None, room_type_id=None, room_type_code=None, property_ids=None
    ):
        day = date or self._ai_ops_today()
        warnings = []
        scope = self._ai_ops_scope(property_ids, warnings)
        room_types = self.env["pms.room.type"]
        if room_type_id:
            room_types = room_types.search([("id", "=", room_type_id)])
        elif room_type_code and room_type_code.strip():
            room_types = room_types.search(
                [("default_code", "=ilike", room_type_code.strip())]
            )
        if not room_types:
            warnings.append(_("No room type matches the filter."))
        rooms = self.env["pms.room"]
        if room_types:
            rooms = rooms.search(
                [
                    ("pms_property_id", "in", scope),
                    ("room_type_id", "in", room_types.ids),
                ]
            )
        blocked_by_prop = {}
        groups = self.env["pms.reservation.line"].read_group(
            [
                ("date", "=", day),
                ("pms_property_id", "in", scope),
                ("state", "not in", UNCOUNTED_STATES),
                ("room_id", "in", rooms.ids),
                ("reservation_id.reservation_type", "=", "out"),
            ],
            ["pms_property_id"],
            ["pms_property_id"],
            lazy=False,
        )
        for group in groups:
            blocked_by_prop[self._ai_ops_group_property(group)] = group["__count"]
        total_rooms = {}
        for room in rooms:
            total_rooms[room.pms_property_id.id] = (
                total_rooms.get(room.pms_property_id.id, 0) + 1
            )
        names = self._ai_ops_property_names(list(total_rooms))
        rows = [
            {
                "property_id": prop_id,
                "property": names.get(prop_id, ""),
                "rooms_of_type": count,
                "blocked_rooms": blocked_by_prop.get(prop_id, 0),
            }
            for prop_id, count in total_rooms.items()
        ]
        rows.sort(key=lambda row: (-row["blocked_rooms"], row["property"]))
        totals = {
            "properties_with_the_type": len(rows),
            "properties_with_blocks": sum(1 for row in rows if row["blocked_rooms"]),
            "rooms_of_type": sum(row["rooms_of_type"] for row in rows),
            "blocked_rooms": sum(row["blocked_rooms"] for row in rows),
        }
        return {
            "summary": _(
                "%(rooms)s rooms blocked in %(properties)s properties on %(date)s."
            )
            % {
                "rooms": totals["blocked_rooms"],
                "properties": totals["properties_with_blocks"],
                "date": day,
            },
            "period": {"date_from": day, "date_to": day},
            "definitions": [
                _(
                    "Blocked room: a room with an out of service block (not "
                    "cancelled) that includes the night of the date."
                ),
                _(
                    "The room type is matched by id or by code; rooms of every "
                    "room type with that code are counted, in the properties of "
                    "the user."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": self._ai_limit_rows(rows, warnings),
            },
        }
