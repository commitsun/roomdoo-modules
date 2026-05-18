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

        The "applicable room types" set is resolved with a single SQL
        ``SELECT DISTINCT room_type_id`` filtered by plan and property,
        instead of iterating ``binding.rule_ids`` in Python — the plan
        may be shared by every property in the chain (e.g. plan "OTA'S"
        holds ~671k rules), so the Python filter would walk hundreds of
        thousands of records just to collapse to a handful of room types.
        """
        binding = self.binding
        backend = binding.backend_id
        prop = backend.pms_property_id

        self.env.cr.execute(
            """
            SELECT DISTINCT r.room_type_id
            FROM pms_availability_plan_rule r
            WHERE r.availability_plan_id = %s
              AND (r.pms_property_id = %s OR r.pms_property_id IS NULL)
              AND r.room_type_id IS NOT NULL
            """,
            (binding.odoo_id.id, prop.id),
        )
        ref_room_type_ids = [row[0] for row in self.env.cr.fetchall()]
        if not ref_room_type_ids:
            return
        bound_ids = (
            self.env["channel.wubook.pms.room.type"]
            .search(
                [
                    ("backend_id", "=", backend.id),
                    ("odoo_id", "in", ref_room_type_ids),
                ]
            )
            .mapped("odoo_id.id")
        )
        if not bound_ids:
            return
        for room_type in self.env["pms.room.type"].browse(bound_ids):
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
                self.binding.with_context(connector_no_export=True).write(
                    {"wubook_last_synced_name": current_name}
                )
