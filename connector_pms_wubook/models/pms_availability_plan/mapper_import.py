# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create
from odoo.addons.pms.models.date_ranges import collapse_dates

from .adapter import RESTRICTION_FIELDS_IMPORT

# ``max_stay_arrival`` is missing on purpose: Wubook takes it on write but
# never returns it, so an import cannot say anything about it.
_IMPORTED_FIELDS = tuple(RESTRICTION_FIELDS_IMPORT)


class ChannelWubookPmsAvailabilityPlanMapperImport(Component):
    _name = "channel.wubook.pms.availability.plan.mapper.import"
    _inherit = "channel.wubook.mapper.import"

    _apply_on = "channel.wubook.pms.availability.plan"

    direct = [
        ("name", "name"),
    ]

    @only_create
    @mapping
    def backend_id(self, record):
        return {"backend_id": self.backend_record.id}

    @mapping
    def property_ids(self, record):
        binding = self.options.get("binding")
        has_pms_properties = binding and bool(binding.pms_property_ids)
        if self.options.for_create or has_pms_properties:
            return {
                "pms_property_ids": [(4, self.backend_record.pms_property_id.id, 0)]
            }

    @mapping
    def rule_ids(self, record):
        """Turn Wubook's one value per night back into ranges.

        Wubook only knows about days, so the nights that share the same
        restrictions for a room type are grouped back into the fewest ranges
        that cover them. A range that already exists with the same bounds is
        updated in place; anything else is created, and overlapping what was
        there is how the model expresses that the channel had the last word.
        """
        items = record.get("items")
        if not items:
            return None
        by_room_type = {}
        for item in items:
            room_type = self._room_type(item)
            values = tuple(item[field] for field in _IMPORTED_FIELDS)
            by_room_type.setdefault(room_type.id, {}).setdefault(values, []).append(
                item["date"]
            )
        ranges = []
        for room_type_id, dates_by_values in by_room_type.items():
            for values, dates in dates_by_values.items():
                for date_from, date_to in collapse_dates(dates):
                    ranges.append((room_type_id, values, date_from, date_to))
        existing = self._existing_rules(record, ranges)
        ops = []
        for room_type_id, values, date_from, date_to in ranges:
            rule_values = {
                **dict(zip(_IMPORTED_FIELDS, values, strict=True)),
                "room_type_id": room_type_id,
                "pms_property_id": self.backend_record.pms_property_id.id,
                "date_from": date_from,
                "date_to": date_to,
            }
            rule_id = existing.get((room_type_id, date_from, date_to))
            if rule_id:
                ops.append((1, rule_id, rule_values))
            else:
                ops.append((0, 0, rule_values))
        return {"rule_ids": ops}

    def _room_type(self, item):
        rt_binder = self.binder_for("channel.wubook.pms.room.type")
        room_type = rt_binder.to_internal(item["id_room"], unwrap=True)
        if not room_type:
            raise ValidationError(
                _(
                    "External record with id %i not exists. "
                    "It should be imported in _import_dependencies"
                )
                % item["id_room"]
            )
        return room_type

    def _existing_rules(self, record, ranges):
        """:return: ``{(room_type_id, date_from, date_to): rule id}`` for the
        ranges already stored with those exact bounds, so that re-importing
        the same window updates instead of piling up copies.
        """
        binding = self.options.get("binding")
        if not binding or not ranges:
            return {}
        rules = self.env["pms.availability.plan.rule"].search(
            [
                ("availability_plan_id", "=", binding.odoo_id.id),
                ("pms_property_id", "=", self.backend_record.pms_property_id.id),
                ("room_type_id", "in", [r[0] for r in ranges]),
                ("date_from", ">=", min(r[2] for r in ranges)),
                ("date_to", "<=", max(r[3] for r in ranges)),
            ]
        )
        return {
            (rule.room_type_id.id, rule.date_from, rule.date_to): rule.id
            for rule in rules
        }
