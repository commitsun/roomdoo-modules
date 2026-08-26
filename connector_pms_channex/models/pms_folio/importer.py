# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsFolioImporter(Component):
    """Writes the folio.

    No ``_import_dependencies``: Odoo is the source of truth for the masters,
    so a room type Channex names and Odoo does not know is not something to
    fetch, it is a mapping that does not exist. That is checked before getting
    here, where it can be reported instead of crashing.
    """

    _name = "channel.channex.pms.folio.importer"
    _inherit = "channel.channex.importer"
    _apply_on = "channel.channex.pms.folio"

    def _create(self, model, values):
        return super()._create(self._channex_context(model), values)

    def _update(self, binding, values):
        return super()._update(self._channex_context(binding), values)

    def _channex_context(self, records):
        """The OTA already sold the room: refusing the booking because Odoo
        thinks there is no availability loses it. And a booking arriving is not
        something to subscribe followers to."""
        return records.with_context(
            mail_create_nosubscribe=True,
            force_overbooking=True,
        )
