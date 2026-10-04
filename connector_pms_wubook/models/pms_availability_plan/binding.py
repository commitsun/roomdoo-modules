# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models

from ..common.wubook_window import accepted_window


class ChannelWubookPmsAvailabilityPlanBinding(models.Model):
    _name = "channel.wubook.pms.availability.plan"
    _inherit = "channel.wubook.binding"
    _inherits = {"pms.availability.plan": "odoo_id"}

    # binding fields
    odoo_id = fields.Many2one(
        comodel_name="pms.availability.plan",
        string="Odoo ID",
        required=True,
        ondelete="cascade",
    )

    wubook_last_synced_name = fields.Char(
        string="Last name pushed to Wubook",
        readonly=True,
        help=(
            "Snapshot of the plan name at the last successful export. "
            "Used by the mapper to skip the ``rplan_rename_rplan`` XMLRPC "
            "call when the name has not actually changed (the export is "
            "otherwise triggered by rule changes via the scheduler)."
        ),
    )

    # The rules are ranges and Wubook is written night by night, so what
    # decides the size of the payload is not how many rules moved but how
    # many nights they span. The listener accumulates that span here and the
    # export mapper expands exactly it; an empty window means the whole
    # accepted window, which is what a first export or a manual resync wants.
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
            values = {
                "wubook_pending_date_from": min(pending_from or date_from, date_from),
                "wubook_pending_date_to": max(pending_to or date_to, date_to),
                # A rule changed, so the plan is no longer in sync. This is
                # what the scheduler looks at to pick the plan up.
                "actual_write_date": fields.Datetime.now(),
            }
            record.write(values)

    def _wubook_export_window(self):
        """:return: the ``(first, last)`` nights this export has to push,
        clipped to what Wubook accepts, or ``None`` when there is nothing
        left inside the window.
        """
        self.ensure_one()
        accepted_from, accepted_to = accepted_window()
        # Nothing staged means this is not an incremental push: a plan
        # connected for the first time, or a manual resynchronization.
        date_from = self.wubook_pending_date_from or accepted_from
        date_to = self.wubook_pending_date_to or accepted_to
        # A season can start before Wubook's floor or run past its ceiling.
        # Trimming it keeps the part Wubook can hold; discarding the rule
        # would stop exporting restrictions that are in force.
        date_from = max(date_from, accepted_from)
        date_to = min(date_to, accepted_to)
        if date_from > date_to:
            return None
        return (date_from, date_to)

    def _wubook_export_room_types(self):
        """:return: the room types whose restrictions this backend publishes.

        Only types this backend sells (bound, and not in a room type class
        marked as not to be synchronized) and that the plan actually
        configures on the backend's property. A type the plan has never had
        a rule for is not managed from here, so its restrictions in Wubook
        are left alone.

        A plan that inherits configures what its parents configure, so the
        whole chain counts: leaving out a type the child does not restate
        would stop publishing it instead of publishing what it inherits.
        """
        self.ensure_one()
        backend = self.backend_id
        self.env.cr.execute(
            """
            SELECT DISTINCT rule.room_type_id
            FROM pms_availability_plan_rule rule
            WHERE rule.availability_plan_id IN %s
              AND rule.pms_property_id = %s
            """,
            (
                tuple(self.odoo_id._inheritance_chain()),
                backend.pms_property_id.id,
            ),
        )
        configured_ids = {row[0] for row in self.env.cr.fetchall()}
        if not configured_ids:
            return self.env["pms.room.type"].browse()
        nosync = (
            backend.backend_type_id.child_id.room_type_class_ids.get_nosync_shortnames()
        )
        bindings = self.env["channel.wubook.pms.room.type"].search(
            [
                ("backend_id", "=", backend.id),
                ("odoo_id", "in", list(configured_ids)),
                ("external_id", "!=", False),
            ]
        )
        return bindings.odoo_id.filtered(
            lambda room_type: room_type.class_id.default_code not in nosync
        )

    @api.model
    def import_data(
        self,
        backend_id,
        date_from,
        date_to,
        room_type_ids=None,
        plan_ids=None,
        delayed=True,
    ):
        """Prepare the batch import of Availability Plans from Channel"""
        domain = []
        if date_from and date_to:
            domain += [
                ("date", ">=", date_from),
                ("date", "<=", date_to),
            ]
        # TODO: duplicated code, unify
        if room_type_ids:
            with backend_id.work_on("channel.wubook.pms.room.type") as work:
                binder = work.component(usage="binder")
            external_ids = []
            for rt in room_type_ids:
                binding = binder.wrap_record(rt)
                if not binding or not binding.external_id:
                    raise NotImplementedError(
                        _(
                            "The Room type %s has no binding. Import of Odoo records "
                            "without binding is not supported yet"
                        )
                        % rt.name
                    )
                external_ids.append(binding.external_id)
            domain.append(("id_room", "in", external_ids))
        if plan_ids:
            with backend_id.work_on("channel.wubook.pms.availability.plan") as work:
                binder = work.component(usage="binder")
            external_ids = []
            for plan in plan_ids:
                binding = binder.wrap_record(plan)
                if not binding or not binding.external_id:
                    raise NotImplementedError(
                        _(
                            "The Availability Plan %s has no binding. "
                            "Import of Odoo records without binding is not "
                            "supported yet"
                        )
                        % plan.name
                    )
                external_ids.append(binding.external_id)
            domain.append(("id", "in", external_ids))
        return self.import_batch(
            backend_record=backend_id, domain=domain, delayed=delayed
        )

    @api.model
    def export_data(self, backend_record=None):
        """Prepare the batch export of Availability Plan to Channel"""
        domain = [
            ("channel_wubook_bind_ids.backend_id", "in", backend_record.ids),
            "|",
            ("pms_property_ids", "=", False),
            ("pms_property_ids", "in", backend_record.pms_property_id.ids),
        ]
        return self.export_batch(
            backend_record=backend_record,
            domain=domain,
        )

    def resync_import(self):
        for record in self:
            items = record.rule_ids.filtered(
                lambda x: x.pms_property_id == self.backend_id.pms_property_id
            )
            if items:
                date_from = min(items.mapped("date_from"))
                date_to = max(items.mapped("date_to"))
                room_types = items.mapped("room_type_id")
                record.import_data(
                    self.backend_id,
                    date_from,
                    date_to,
                    room_type_ids=room_types,
                    plan_ids=record.odoo_id,
                    delayed=False,
                )
