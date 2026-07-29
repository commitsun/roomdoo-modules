# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import AbstractComponent


class ChannelChannexExporter(AbstractComponent):
    _name = "channel.channex.exporter"
    _inherit = ["channel.exporter", "base.channel.channex.connector"]

    _default_binding_field = "channel_channex_bind_ids"


class ChannelChannexBatchExporter(AbstractComponent):
    _name = "channel.channex.batch.exporter"
    _inherit = ["channel.batch.exporter", "base.channel.channex.connector"]


class ChannelChannexDirectBatchExporter(AbstractComponent):
    _name = "channel.channex.direct.batch.exporter"
    _inherit = "channel.direct.batch.exporter"


class ChannelChannexDelayedBatchExporter(AbstractComponent):
    _name = "channel.channex.delayed.batch.exporter"
    _inherit = "channel.delayed.batch.exporter"
