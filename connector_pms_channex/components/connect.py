# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexConnectCandidates(Component):
    """Narrows the candidate list to the property this backend serves.

    The generic component lists everything the adapter returns, which for
    Channex would be every room type of the whole account, across hotels.
    """

    _name = "channel.channex.connect.candidates"
    _inherit = "channel.connect.candidates"
    _collection = "channel.channex.backend"

    def fetch(self):
        adapter = self.component(usage="backend.adapter")
        domain = []
        if "property_id" in (adapter._server_filters or ()):
            property_external_id = self._property_external_id()
            if not property_external_id:
                # Without the property on Channex there is nothing of this
                # hotel's to connect to yet.
                return []
            domain = [("property_id", "=", property_external_id)]
        return adapter.search_read(domain)

    def _property_external_id(self):
        binding = self.env["channel.channex.pms.property"].search(
            [
                ("odoo_id", "=", self.backend_record.pms_property_id.id),
                ("backend_id", "=", self.backend_record.id),
            ],
            limit=1,
        )
        return binding.external_id
