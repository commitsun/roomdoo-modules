# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


from odoo.addons.component.core import Component


class ChannelWubookPmsPropertyAvailabilityDelayedBatchExporter(Component):
    _name = "channel.wubook.pms.property.availability.delayed.batch.exporter"
    _inherit = "channel.wubook.delayed.batch.exporter"

    _apply_on = "channel.wubook.pms.property.availability"


class ChannelWubookPmsPropertyAvailabilityDirectBatchExporter(Component):
    _name = "channel.wubook.pms.property.availability.direct.batch.exporter"
    _inherit = "channel.wubook.direct.batch.exporter"

    _apply_on = "channel.wubook.pms.property.availability"


class ChannelWubookPmsPropertyAvailabilityExporter(Component):
    _name = "channel.wubook.pms.property.availability.exporter"
    _inherit = "channel.wubook.exporter"

    _apply_on = "channel.wubook.pms.property.availability"

    def _has_to_skip(self):
        return any(
            [
                self.binding.synced_export,
            ]
        )

    def _after_export(self):
        super()._after_export()
        if not self.binding:
            return
        # The window the mapper expanded has been pushed, so the next export
        # starts from whatever moves after this one.
        self.binding.with_context(connector_no_export=True).write(
            {
                "wubook_pending_date_from": False,
                "wubook_pending_date_to": False,
            }
        )
