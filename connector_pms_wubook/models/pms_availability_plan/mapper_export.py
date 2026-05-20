# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping


class ChannelWubookPmsAvailabilityPlanMapperExport(Component):
    _name = "channel.wubook.pms.availability.plan.mapper.export"
    _inherit = "channel.wubook.mapper.export"

    _apply_on = "channel.wubook.pms.availability.plan"

    children = [
        (
            "channel_wubook_rule_ids",
            "items",
            "channel.wubook.pms.availability.plan.rule",
        )
    ]

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


class ChannelWubookPmsAvailabilityPlanChildBinderMapperExport(Component):
    _name = "channel.wubook.pms.availability.plan.child.binder.mapper.export"
    _inherit = "channel.wubook.child.binder.mapper.export"

    _apply_on = "channel.wubook.pms.availability.plan.rule"

    def skip_item(self, map_record):
        # flake8: noqa: B950
        return any(
            [
                map_record.source.room_type_id.class_id.default_code
                in self.backend_record.backend_type_id.child_id.room_type_class_ids.get_nosync_shortnames(),  # noqa: E501
                map_record.source.pms_property_id
                != self.backend_record.pms_property_id,
                map_record.source.synced_export,
                not map_record.source.odoo_id.wubook_date_valid(),
                not map_record.source.room_type_id.channel_wubook_bind_ids.filtered(
                    lambda x: x.backend_id == self.backend_record
                ),
            ]
        )

    def get_all_items(self, mapper, items, parent, to_attr, options):
        # Resolve "rules of this plan in this backend's property that
        # don't have a binding for this backend yet" with a single SQL
        # query. The naive ``parent.source["rule_ids"].filtered(...)``
        # walks the whole plan's rule_ids in Python (~671k records for
        # the global OTA'S plan), turning every export into a multi-
        # minute job even when no new bindings need to be created.
        backend = self.backend_record
        bindings = items.filtered(lambda x: x.backend_id == backend)
        self.env.cr.execute(
            """
            SELECT r.id
            FROM pms_availability_plan_rule r
            LEFT JOIN channel_wubook_pms_availability_plan_rule rb
                ON rb.odoo_id = r.id AND rb.backend_id = %s
            WHERE r.availability_plan_id = %s
              AND r.pms_property_id = %s
              AND rb.id IS NULL
            """,
            (backend.id, parent.source.odoo_id.id, backend.pms_property_id.id),
        )
        new_rule_ids = [row[0] for row in self.env.cr.fetchall()]
        if new_rule_ids:
            new_rules = self.env["pms.availability.plan.rule"].browse(new_rule_ids)
            new_binding_ids = [
                self.binder_for().wrap_record(r, force=True).id for r in new_rules
            ]
            items = items.browse(new_binding_ids) | bindings
        else:
            items = bindings
        return super().get_all_items(mapper, items, parent, to_attr, options)
