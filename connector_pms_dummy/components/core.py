# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import AbstractComponent


class BaseChannelDummyConnector(AbstractComponent):
    """The single place this channel manager's collection is declared. Every
    component below inherits it, which is what makes ``work_on`` resolve this
    connector's components and not another's."""

    _name = "base.channel.dummy.connector"
    _inherit = "base.channel.connector"
    _collection = "channel.dummy.backend"

    _description = "Base Channel Dummy Connector Component"
