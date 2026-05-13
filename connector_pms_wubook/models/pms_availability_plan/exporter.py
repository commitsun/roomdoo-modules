# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


from odoo.addons.component.core import Component


class ChannelWubookPmsAvailabilityPlanDelayedBatchExporter(Component):
    _name = "channel.wubook.pms.availability.plan.delayed.batch.exporter"
    _inherit = "channel.wubook.delayed.batch.exporter"

    _apply_on = "channel.wubook.pms.availability.plan"


class ChannelWubookPmsAvailabilityPlanDirectBatchExporter(Component):
    _name = "channel.wubook.pms.availability.plan.direct.batch.exporter"
    _inherit = "channel.wubook.direct.batch.exporter"

    _apply_on = "channel.wubook.pms.availability.plan"


class ChannelWubookPmsAvailabilityPlanExporter(Component):
    _name = "channel.wubook.pms.availability.plan.exporter"
    _inherit = "channel.wubook.exporter"

    _apply_on = "channel.wubook.pms.availability.plan"

    def _export_dependencies(self):
        """Re-export already-connected dependencies (so they're up to date)
        before the plan is exported.

        Strict policy: only iterate room types that ALREADY have a
        binding on this backend, and only rules applicable to the
        backend's property. Unconnected room types must be connected
        manually via the ``Connect to Wubook`` wizard first.
        """
        binding = self.binding
        backend = binding.backend_id
        prop = backend.pms_property_id

        applicable_rules = binding.rule_ids.filtered(
            lambda r: not r.pms_property_id or r.pms_property_id == prop
        )
        ref_room_types = applicable_rules.mapped("room_type_id")
        if not ref_room_types:
            return
        bound_ids = (
            self.env["channel.wubook.pms.room.type"]
            .search(
                [
                    ("backend_id", "=", backend.id),
                    ("odoo_id", "in", ref_room_types.ids),
                ]
            )
            .mapped("odoo_id.id")
        )
        for room_type in ref_room_types.filtered(
            lambda r: r.id in bound_ids
        ):
            self._export_dependency(room_type, "channel.wubook.pms.room.type")

    def _has_to_skip(self):
        return any(
            [
                self.binding.synced_export,
            ]
        )

    def _after_export(self):
        super()._after_export()
        if self.binding:
            current_name = self.binding.name
            if self.binding.wubook_last_synced_name != current_name:
                self.binding.with_context(
                    connector_no_export=True
                ).write({"wubook_last_synced_name": current_name})
