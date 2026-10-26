# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import json

from odoo.addons.component.core import AbstractComponent


class ChannelChannexAriSender(AbstractComponent):
    """Sends, in one call, everything a backend has pending of one kind.

    Each kind says how its values are computed and where they go; reading the
    pending bindings, the call and what Channex refuses are the same for all.
    """

    _name = "channel.channex.ari.sender"
    _inherit = "base.channel.channex.connector"
    _usage = "ari.sender"

    #: The kind this sender publishes, as in ``ARI_KINDS``.
    _ari_kind = None
    #: The Channex endpoint it publishes to.
    _ari_resource = None

    def run(self):
        """:return: how many values were sent."""
        bindings = self.model.with_context(active_test=False).search(
            [
                ("backend_id", "=", self.backend_record.id),
                ("channex_ari_pending_from", "!=", False),
            ],
            order="id",
        )
        if not bindings:
            return 0
        first, last = self.backend_record._channex_ari_horizon()
        result = self._ari_publish(bindings, first, last)
        # Cleared last and in one go: anything noted while the values were being
        # worked out wrote to these same rows, and this write clashing with it
        # is what makes the send start again instead of losing it.
        bindings.write(
            {"channex_ari_pending_from": False, "channex_ari_pending_to": False}
        )
        return result

    def _ari_publish(self, bindings, first, last):
        """Publish what these bindings have pending, in one call.

        :return: how many values were sent.
        """
        values = self._ari_values(bindings, first, last)
        if values:
            self._ari_send(values)
        return len(values)

    def _ari_window(self, binding, first, last):
        """The nights of this binding still to publish, within the window."""
        return (
            max(binding.channex_ari_pending_from, first),
            min(binding.channex_ari_pending_to, last),
        )

    def _ari_values(self, bindings, first, last):
        """The values to send, as Channex expects them in ``values``."""
        raise NotImplementedError()

    def _ari_send(self, values):
        """One call with every value. What Channex throws away comes back in an
        answer that is otherwise a success, so it is looked for every time."""
        adapter = self.component(usage="backend.adapter")
        body = adapter.request("POST", self._ari_resource, payload={"values": values})
        if body is None:
            # Exports are disabled on this backend: nothing left Odoo.
            return
        warnings = ((body or {}).get("meta") or {}).get("warnings") or []
        if warnings:
            self._ari_warn(warnings)

    def _ari_warn(self, warnings):
        """Keep what Channex refused, for someone to look at."""
        self.env["channel.channex.ari.warning"].create(
            {
                "backend_id": self.backend_record.id,
                "kind": self._ari_kind,
                "count": len(warnings),
                "warnings": json.dumps(warnings, indent=2, sort_keys=True),
            }
        )
