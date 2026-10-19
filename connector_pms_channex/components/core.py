# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import AbstractComponent


class BaseChannelChannexConnector(AbstractComponent):
    """The single declaration of this connector's collection: it is what makes
    ``backend.work_on(...)`` resolve Channex components and not another
    connector's."""

    _name = "base.channel.channex.connector"
    _inherit = "base.channel.connector"
    _collection = "channel.channex.backend"

    _description = "Base Channel Channex Connector Component"
