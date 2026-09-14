# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelConnectCandidates(Component):
    """Lists the records available to connect to, on the channel manager side.

    Declared without ``_collection`` so it serves every channel manager. A
    connector whose API needs a narrower query or a different label declares its
    own with ``_collection`` set, which takes precedence.
    """

    _name = "channel.connect.candidates"
    _inherit = "base.channel.connector"
    _usage = "connect.candidates"

    def fetch(self):
        """Return the raw external records, each a dict with at least ``id``."""
        adapter = self.component(usage="backend.adapter")
        return adapter.search_read([])

    def label(self, external_record):
        """Human readable label for the dropdown."""
        for key in ("name", "title", "shortname", "default_code", "code"):
            value = external_record.get(key)
            if value:
                return f"{value} [#{external_record['id']}]"
        return f"#{external_record['id']}"


class ChannelConnectHooks(Component):
    """Post-connect side effects, if the channel manager has any.

    No-op by default; a connector overrides it with ``_collection`` set.
    """

    _name = "channel.connect.hooks"
    _inherit = "base.channel.connector"
    _usage = "connect.hooks"

    def after_connect(self, record):
        return None
