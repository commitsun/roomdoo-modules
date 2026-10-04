# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo.addons.component.core import AbstractComponent
from odoo.addons.connector.exception import IDMissingInBackend

_logger = logging.getLogger(__name__)


class ChannelChannexDeleter(AbstractComponent):
    _name = "channel.channex.deleter"
    _inherit = ["channel.deleter", "base.channel.channex.connector"]

    def run(self, external_id):
        try:
            self.backend_adapter.delete(external_id)
        except IDMissingInBackend:
            # Already gone on Channex. Deleting is the one operation where that
            # is the wanted outcome, so it is not an error.
            _logger.info(
                "%s %s was already absent from Channex",
                self.model._name,
                external_id,
            )
            return "absent"
        return "deleted"
